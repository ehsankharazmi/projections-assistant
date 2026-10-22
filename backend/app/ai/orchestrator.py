"""Conversation turns: persistence, rate limits, and the bounded Claude tool loop.

Request shape (stable prefix first, for prompt caching):
    system  - versioned template (prompts/system_v1.md), no per-request data
    tools   - complete authorized tool list, fixed order, all strict
    messages- earlier turns as plain text only (user text + assistant final message),
              then the current user message with server-generated state context

Within a turn the message list is append-only, and assistant content (including
thinking blocks) is passed back exactly as received. Earlier turns' thinking is
never replayed, so editing or summarizing old history cannot invalidate it.
The in-progress list is persisted after every round so a turn can resume when a
slow calculation finishes.
"""

from __future__ import annotations

import collections
import json
import logging
import threading
import time
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select, update

from app.ai import tools as tool_registry
from app.ai.payload import ModelPayload
from app.ai.provider import SYSTEM_PROMPT, SYSTEM_PROMPT_VERSION, AIServiceError, ClaudeAdapter
from app.context import AppContext
from app.db import session_scope
from app.errors import AppError
from app.models import Conversation, Message, Scenario, Turn, User, Workbook, new_id
from app.services import scenarios as scenario_service
from app.services import workbooks as wbs

log = logging.getLogger("ai.orchestrator")

MAX_USER_MESSAGE_CHARS = 4000
TURN_DEADLINE_SECONDS = 300.0


class RateLimited(AppError):
    def __init__(self, message: str):
        super().__init__("rate_limited", message, status_code=429)


class ChatService:
    def __init__(self, ctx: AppContext, monitor, adapter: ClaudeAdapter | None, demo_responder):
        self.ctx = ctx
        self.monitor = monitor
        self.adapter = adapter
        self.demo = demo_responder
        self._recent: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self._active: dict[str, int] = collections.defaultdict(int)
        self._guard = threading.Lock()
        monitor.on_finished.append(self._on_job_finished)

    @property
    def mode(self) -> str:
        return self.ctx.settings.ai_mode

    # ------------------------------------------------------------------ admission
    def _admit(self, user_id: str) -> None:
        s = self.ctx.settings
        now = time.monotonic()
        with self._guard:
            recent = self._recent[user_id]
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= s.ai_requests_per_user_per_minute:
                raise RateLimited("You're sending messages faster than the assistant allows. Please wait a moment.")
            if self._active[user_id] >= s.ai_max_concurrent_turns:
                raise RateLimited("The assistant is still working on your previous message.")
            recent.append(now)
            self._active[user_id] += 1

    def _release(self, user_id: str) -> None:
        with self._guard:
            self._active[user_id] = max(0, self._active[user_id] - 1)

    # ------------------------------------------------------------------ public API
    def post_message(self, user: User, workbook_id: str, text: str, request_key: str | None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise AppError("empty_message", "Please type a message.", status_code=422)
        if len(text) > MAX_USER_MESSAGE_CHARS:
            raise AppError("message_too_long", f"Messages are limited to {MAX_USER_MESSAGE_CHARS} characters.",
                           status_code=422)
        with session_scope() as session:
            wb = wbs.get_owned_workbook(session, user, workbook_id)
            conversation = self._conversation(session, wb, user)
            if request_key:
                existing = session.scalars(select(Turn).where(Turn.conversation_id == conversation.id,
                                                              Turn.request_key == request_key)).first()
                if existing is not None:
                    return self.turn_view(session, existing)
            conversation_id = conversation.id
        self._admit(user.id)
        try:
            with session_scope() as session:
                turn = Turn(id=new_id("turn"), conversation_id=conversation_id, request_key=request_key,
                            status="running", mode=self.mode)
                session.add(turn)
                self._add_message(session, conversation_id, turn.id, "user", text, None)
                turn_id = turn.id
            if self.mode == "demo":
                self._finish(turn_id, *self.demo.respond(self._tool_context(user.id, workbook_id, conversation_id,
                                                                            turn_id), text))
            else:
                self._start_claude_turn(user.id, workbook_id, conversation_id, turn_id, text)
        finally:
            self._release(user.id)
        with session_scope() as session:
            return self.turn_view(session, session.get(Turn, turn_id))

    def list_messages(self, user: User, workbook_id: str, after_seq: int = 0) -> dict[str, Any]:
        with session_scope() as session:
            wb = wbs.get_owned_workbook(session, user, workbook_id)
            conversation = self._conversation(session, wb, user)
            rows = session.scalars(select(Message).where(Message.conversation_id == conversation.id,
                                                         Message.seq > after_seq).order_by(Message.seq)).all()
            pending = session.scalars(select(Turn).where(Turn.conversation_id == conversation.id,
                                                         Turn.status.in_(("running", "awaiting_job")))).all()
            return {"mode": self.mode, "messages": [self._message_dict(session, wb, m) for m in rows],
                    "pending_turns": [{"id": t.id, "status": t.status} for t in pending]}

    # ------------------------------------------------------------------ helpers
    def _conversation(self, session, wb: Workbook, user: User) -> Conversation:
        conversation = session.scalars(select(Conversation).where(Conversation.workbook_id == wb.id,
                                                                  Conversation.user_id == user.id)).first()
        if conversation is None:
            conversation = Conversation(workbook_id=wb.id, user_id=user.id)
            session.add(conversation)
            session.flush()
        return conversation

    def _add_message(self, session, conversation_id: str, turn_id: str | None, role: str, text: str,
                     payload: dict | None) -> Message:
        seq = (session.scalar(select(func.max(Message.seq)).where(Message.conversation_id == conversation_id))
               or 0) + 1
        message = Message(conversation_id=conversation_id, turn_id=turn_id, seq=seq, role=role, text=text,
                          payload=payload)
        session.add(message)
        session.flush()
        return message

    def _tool_context(self, user_id: str, workbook_id: str, conversation_id: str,
                      turn_id: str | None) -> tool_registry.ToolContext:
        return tool_registry.ToolContext(self.ctx, user_id, workbook_id, conversation_id, turn_id,
                                         wait_for_job=self.monitor.wait_for)

    def _message_dict(self, session, wb: Workbook, m: Message) -> dict[str, Any]:
        payload = dict(m.payload or {})
        scenario_id = payload.get("scenario_id")
        if scenario_id:  # always show current, authoritative scenario state
            scenario = session.get(Scenario, scenario_id)
            if scenario is not None and scenario.workbook_id == wb.id:
                payload["scenario"] = scenario_service.scenario_dict(session, wb, scenario)
        return {"id": m.id, "seq": m.seq, "role": m.role, "text": m.text, "payload": payload,
                "turn_id": m.turn_id, "created_at": m.created_at.isoformat()}

    def turn_view(self, session, turn: Turn) -> dict[str, Any]:
        conversation = session.get(Conversation, turn.conversation_id)
        wb = session.get(Workbook, conversation.workbook_id)
        rows = session.scalars(select(Message).where(Message.turn_id == turn.id).order_by(Message.seq)).all()
        return {"turn": {"id": turn.id, "status": turn.status, "mode": turn.mode, "error": turn.error},
                "messages": [self._message_dict(session, wb, m) for m in rows]}

    def _finish(self, turn_id: str, text: str, payload: dict[str, Any], status: str = "completed",
                error: str | None = None) -> None:
        with session_scope() as session:
            turn = session.get(Turn, turn_id)
            payload = self._authoritative(session, turn, payload)
            self._add_message(session, turn.conversation_id, turn.id, "assistant", text, payload)
            turn.status, turn.error, turn.state = status, error, None

    def _authoritative(self, session, turn: Turn, payload: dict[str, Any]) -> dict[str, Any]:
        """Make the payload consistent with server state; the model never decides figures or permissions."""
        conversation = session.get(Conversation, turn.conversation_id)
        payload = {**payload, "mode": turn.mode}
        scenario_id = payload.get("scenario_id")
        if scenario_id:
            scenario = session.get(Scenario, scenario_id)
            if scenario is None or scenario.workbook_id != conversation.workbook_id:
                payload["scenario_id"] = None
                if payload.get("kind") in ("scenario_ready", "scenario_pending"):
                    payload["kind"] = "answer"
            elif scenario.status == "ready":
                payload["kind"] = "scenario_ready"
            elif scenario.status in ("draft", "validating", "calculating"):
                payload["kind"] = "scenario_pending"
            elif scenario.status == "failed" and payload.get("kind") in ("scenario_ready", "scenario_pending"):
                payload["kind"] = "error"
        elif payload.get("kind") in ("scenario_ready", "scenario_pending"):
            payload["kind"] = "answer"
        if payload.get("kind") == "clarification" and not payload.get("clarification"):
            payload["kind"] = "answer"
        return payload

    # ------------------------------------------------------------------ Claude turns
    def _history(self, session, conversation_id: str, current_turn_id: str) -> list[dict[str, Any]]:
        turns = session.scalars(select(Turn).where(Turn.conversation_id == conversation_id, Turn.id != current_turn_id,
                                                   Turn.status == "completed").order_by(Turn.created_at.desc())
                                .limit(self.ctx.settings.ai_history_turns)).all()
        history: list[dict[str, Any]] = []
        for turn in reversed(turns):
            msgs = session.scalars(select(Message).where(Message.turn_id == turn.id).order_by(Message.seq)).all()
            user = next((m.text for m in msgs if m.role == "user"), None)
            assistant = next((m.text for m in reversed(msgs) if m.role == "assistant"), None)
            if user and assistant:
                history.append({"role": "user", "content": [{"type": "text", "text": user}]})
                history.append({"role": "assistant", "content": [{"type": "text", "text": assistant}]})
        return history

    def _state_context(self, session, workbook_id: str, conversation_id: str) -> dict[str, Any]:
        wb = session.get(Workbook, workbook_id)
        latest = session.scalars(select(Scenario).where(Scenario.conversation_id == conversation_id)
                                 .order_by(Scenario.created_at.desc())).first()
        last_assistant = session.scalars(select(Message).where(Message.conversation_id == conversation_id,
                                                               Message.role == "assistant")
                                         .order_by(Message.seq.desc())).first()
        open_question = None
        if last_assistant and (last_assistant.payload or {}).get("kind") == "clarification":
            open_question = (last_assistant.payload.get("clarification") or {}).get("question")
        older = self._older_digest(session, conversation_id)
        return {
            "active_version_id": wb.active_version_id,
            "mapping_version_id": wb.active_mapping_version_id,
            "editing_enabled": wb.compatibility_status == "editable",
            "latest_scenario": ({"id": latest.id, "name": latest.name, "status": latest.status,
                                 "base_version_id": latest.base_version_id,
                                 "interpreted_request": latest.interpreted_request} if latest else None),
            "open_clarification_question": open_question,
            "summary_of_older_conversation": older,
        }

    def _older_digest(self, session, conversation_id: str) -> str | None:
        """Server-written digest of completed turns older than the replayed window (bounded, plain text)."""
        turns = session.scalars(select(Turn).where(Turn.conversation_id == conversation_id,
                                                   Turn.status == "completed").order_by(Turn.created_at.desc())
                                .offset(self.ctx.settings.ai_history_turns).limit(20)).all()
        if not turns:
            return None
        lines = []
        for turn in reversed(turns):
            msgs = session.scalars(select(Message).where(Message.turn_id == turn.id).order_by(Message.seq)).all()
            for m in msgs:
                lines.append(f"{m.role}: {m.text[:160]}")
        return "\n".join(lines)[:3000]

    def _start_claude_turn(self, user_id: str, workbook_id: str, conversation_id: str, turn_id: str,
                           text: str) -> None:
        with session_scope() as session:
            history = self._history(session, conversation_id, turn_id)
            context = self._state_context(session, workbook_id, conversation_id)
        current = {"role": "user", "content": [{"type": "text", "text":
                   "<application_state>\n" + json.dumps(context) + "\n</application_state>\n\n"
                   "<user_message>\n" + text + "\n</user_message>"}]}
        state = {"messages": history + [current], "prompt_version": SYSTEM_PROMPT_VERSION}
        self._persist_state(turn_id, state)
        self._run_claude(user_id, workbook_id, conversation_id, turn_id, state, time.monotonic() + TURN_DEADLINE_SECONDS)

    def _persist_state(self, turn_id: str, state: dict[str, Any], **turn_fields: Any) -> None:
        with session_scope() as session:
            turn = session.get(Turn, turn_id)
            turn.state = json.loads(json.dumps(state, default=str))
            for key, value in turn_fields.items():
                setattr(turn, key, value)

    def _run_claude(self, user_id: str, workbook_id: str, conversation_id: str, turn_id: str,
                    state: dict[str, Any], deadline: float) -> None:
        s = self.ctx.settings
        tc = self._tool_context(user_id, workbook_id, conversation_id, turn_id)
        tools = tool_registry.anthropic_tool_definitions()
        try:
            while True:
                with session_scope() as session:
                    turn = session.get(Turn, turn_id)
                    rounds, tool_calls = turn.rounds, turn.tool_calls
                if rounds >= s.ai_max_model_rounds:
                    return self._fail(turn_id, "round_limit", "I couldn't finish within the allowed number of steps. "
                                                              "Your workbook is unchanged; please try a simpler "
                                                              "request.")
                call = self.adapter.create(system=SYSTEM_PROMPT, tools=tools, messages=state["messages"],
                                           user_id=user_id, turn_id=turn_id, deadline=deadline)
                message = call.message
                content = [block.model_dump(mode="json", exclude_none=True) for block in message.content]
                state["messages"].append({"role": "assistant", "content": content})
                self._persist_state(turn_id, state, rounds=rounds + 1)
                stop = message.stop_reason
                if stop == "tool_use":
                    results, pending_job, tool_calls = self._execute_tools(tc, content, tool_calls)
                    if pending_job is not None:
                        state["pending_tool_results"] = results
                        state["pending"] = pending_job
                        self._persist_state(turn_id, state, status="awaiting_job", awaiting_job_id=pending_job["job_id"],
                                            tool_calls=tool_calls)
                        self._interim(turn_id, pending_job["scenario_id"])
                        # The job may have finished before the turn was marked as waiting, in which case the
                        # completion callback found nothing to resume. Exactly one path wins the claim.
                        if self._job_finished(pending_job["job_id"]) and self._claim_resume(turn_id):
                            return self.resume(turn_id, user_id, workbook_id, conversation_id)
                        return None
                    state["messages"].append({"role": "user", "content": results})
                    self._persist_state(turn_id, state, tool_calls=tool_calls)
                    continue
                if stop == "end_turn":
                    return self._complete_from_model(turn_id, content)
                if stop == "max_tokens":
                    return self._fail(turn_id, "ai_incomplete", "The AI reply was cut off before it finished. Your "
                                                               "workbook is unchanged; please try again.")
                if stop == "refusal":
                    return self._fail(turn_id, "ai_refused", "The AI declined to respond to this request. Your "
                                                             "workbook is unchanged.")
                return self._fail(turn_id, "ai_unexpected_stop", f"The AI stopped unexpectedly ({stop}). Your "
                                                                 "workbook is unchanged.")
        except AIServiceError as exc:
            return self._fail(turn_id, exc.code, exc.message, request_id=exc.details.get("request_id"))
        except AppError as exc:
            return self._fail(turn_id, exc.code, exc.message)
        except Exception:
            log.exception("unexpected failure in turn %s", turn_id)
            return self._fail(turn_id, "internal_error", "Something went wrong while answering. Your workbook is "
                                                         "unchanged.")

    def _execute_tools(self, tc, content: list[dict], tool_calls: int) -> tuple[list[dict], dict | None, int]:
        results: list[dict] = []
        pending = None
        for block in content:
            if block.get("type") != "tool_use":
                continue
            if tool_calls >= self.ctx.settings.ai_max_tool_calls_per_turn:
                result = tool_registry.ToolResult({"error": {"code": "tool_limit", "message":
                                                   "Tool-call limit reached for this turn; answer with what you "
                                                   "have."}}, True)
            else:
                tool_calls += 1
                result = tool_registry.execute(tc, block.get("name", ""), block.get("input"))
            if result.pending_job_id:
                pending = {"tool_use_id": block["id"], "job_id": result.pending_job_id,
                           "scenario_id": result.content.get("scenario_id")}
            results.append({"type": "tool_result", "tool_use_id": block["id"], "content": result.as_text(),
                            **({"is_error": True} if result.is_error else {})})
        return results, pending, tool_calls

    def _interim(self, turn_id: str, scenario_id: str | None) -> None:
        with session_scope() as session:
            turn = session.get(Turn, turn_id)
            payload = self._authoritative(session, turn, {"kind": "scenario_pending", "scenario_id": scenario_id,
                                                          "clarification": None, "referenced_field_ids": [],
                                                          "proposed_rule": None, "interim": True})
            self._add_message(session, turn.conversation_id, turn.id, "assistant",
                              "I'm recalculating the workbook with these changes. This can take a moment; the "
                              "preview will appear here when it's ready.", payload)

    @staticmethod
    def _job_finished(job_id: str) -> bool:
        from app.models import Job
        with session_scope() as session:
            job = session.get(Job, job_id)
            return job is None or job.status in ("succeeded", "failed")

    @staticmethod
    def _claim_resume(turn_id: str) -> bool:
        """Atomically move a turn from awaiting_job to running; only the winner may resume it."""
        with session_scope() as session:
            result = session.execute(update(Turn).where(Turn.id == turn_id, Turn.status == "awaiting_job")
                                     .values(status="running"))
            return result.rowcount == 1

    def _on_job_finished(self, job_id: str) -> None:
        with session_scope() as session:
            turn = session.scalars(select(Turn).where(Turn.awaiting_job_id == job_id,
                                                      Turn.status == "awaiting_job")).first()
            if turn is None:
                return
            turn_id, conversation_id = turn.id, turn.conversation_id
            conversation = session.get(Conversation, conversation_id)
            user_id, workbook_id = conversation.user_id, conversation.workbook_id
        if not self._claim_resume(turn_id):
            return
        threading.Thread(target=self.resume, args=(turn_id, user_id, workbook_id, conversation_id),
                         name=f"resume-{turn_id}", daemon=True).start()

    def resume(self, turn_id: str, user_id: str, workbook_id: str, conversation_id: str) -> None:
        """Continue a turn after its calculation finished: append the tool results and keep going."""
        with session_scope() as session:
            turn = session.get(Turn, turn_id)
            state = dict(turn.state or {})
        pending = state.pop("pending", None)
        results = state.pop("pending_tool_results", [])
        if pending is None:
            return self._fail(turn_id, "internal_error", "The calculation finished but the conversation could not "
                                                         "continue. Your workbook is unchanged.")
        tc = self._tool_context(user_id, workbook_id, conversation_id, turn_id)
        finished = tool_registry.scenario_result(tc, pending["scenario_id"])
        for result in results:
            if result["tool_use_id"] == pending["tool_use_id"]:
                result["content"] = json.dumps(finished, default=str)
        state["messages"].append({"role": "user", "content": results})
        self._persist_state(turn_id, state, awaiting_job_id=None)
        self._run_claude(user_id, workbook_id, conversation_id, turn_id, state,
                         time.monotonic() + TURN_DEADLINE_SECONDS)

    def _complete_from_model(self, turn_id: str, content: list[dict]) -> None:
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        try:
            payload = ModelPayload.model_validate_json(text)
        except ValidationError:
            log.warning("turn %s returned a payload that failed validation", turn_id)
            return self._fail(turn_id, "ai_malformed", "The AI reply could not be understood. Your workbook is "
                                                       "unchanged; please try again.")
        data = payload.model_dump()
        self._finish(turn_id, payload.message, data)

    def _fail(self, turn_id: str, code: str, message: str, request_id: str | None = None) -> None:
        payload = {"kind": "error", "scenario_id": None, "clarification": None, "referenced_field_ids": [],
                   "proposed_rule": None, "error": {"code": code, **({"request_id": request_id} if request_id else {})}}
        self._finish(turn_id, message, payload, status="failed", error=code)


def recover_interrupted_turns() -> int:
    """At startup: turns left 'running' by a crash cannot be continued safely; mark them failed and say so."""
    count = 0
    with session_scope() as session:
        for turn in session.scalars(select(Turn).where(Turn.status == "running")).all():
            turn.status, turn.error, turn.state = "failed", "interrupted", None
            seq = (session.scalar(select(func.max(Message.seq)).where(Message.conversation_id == turn.conversation_id))
                   or 0) + 1
            session.add(Message(conversation_id=turn.conversation_id, turn_id=turn.id, seq=seq, role="assistant",
                                text="This request was interrupted by a restart before it finished. Your workbook "
                                     "is unchanged; please send it again.",
                                payload={"kind": "error", "scenario_id": None, "clarification": None,
                                         "referenced_field_ids": [], "proposed_rule": None,
                                         "error": {"code": "interrupted"}}))
            count += 1
    return count
