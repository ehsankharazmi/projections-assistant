"""Claude API adapter (official `anthropic` Python SDK, Messages API).

- The client gets the API key explicitly from settings and `max_retries=0`:
  retries happen here, once, so each attempt has its own budget reservation.
- Every attempt reserves an upper-bound cost first (input measured with
  `messages.count_tokens`), and is reconciled with reported usage afterwards.
- Transient failures (429 with retry-after, 5xx, 529, connection errors,
  timeouts) are retried with bounded exponential backoff and jitter, honouring
  `retry-after` and the turn deadline. Authentication, permission, unknown model,
  bad request (including workspace spend limits), billing, and budget errors are
  never retried.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic

from app.ai.ledger import Ledger, TokenUsage
from app.ai.payload import PAYLOAD_SCHEMA
from app.config import Settings
from app.errors import AppError

log = logging.getLogger("ai.provider")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
SYSTEM_PROMPT_VERSION = "system_v1"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / f"{SYSTEM_PROMPT_VERSION}.md").read_text()


class AIServiceError(AppError):
    def __init__(self, code: str, message: str, *, status_code: int = 503, request_id: str | None = None):
        super().__init__(code, message, status_code=status_code,
                         details={"request_id": request_id} if request_id else {})


@dataclass
class CallResult:
    message: Any  # anthropic Message or BetaMessage
    request_id: str | None
    cost_usd: float
    fallback_used: bool


def _retry_after(exc: anthropic.APIStatusError) -> float | None:
    value = exc.response.headers.get("retry-after") if exc.response is not None else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _request_id(exc: anthropic.APIStatusError) -> str | None:
    return exc.response.headers.get("request-id") if exc.response is not None else None


def _usages(message: Any, requested_model: str) -> tuple[list[TokenUsage], bool]:
    """Token usage per serving model. With server-side fallback, `usage.iterations` itemizes each model run."""
    usage = message.usage
    iterations = getattr(usage, "iterations", None) or []
    model_runs = [it for it in iterations if getattr(it, "type", "") in ("message", "fallback_message")]
    fallback_used = any(getattr(it, "type", "") == "fallback_message" for it in iterations)

    def split(u: Any, model: str) -> TokenUsage:
        cc = getattr(u, "cache_creation", None)
        w1h = getattr(cc, "ephemeral_1h_input_tokens", 0) or 0 if cc is not None else 0
        written = getattr(u, "cache_creation_input_tokens", 0) or 0
        return TokenUsage(model=model, input_tokens=u.input_tokens or 0, cache_write_5m=max(written - w1h, 0),
                          cache_write_1h=w1h, cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                          output_tokens=u.output_tokens or 0)

    if model_runs:
        return [split(it, getattr(it, "model", None) or requested_model) for it in model_runs], fallback_used
    return [split(usage, getattr(message, "model", None) or requested_model)], fallback_used


class ClaudeAdapter:
    def __init__(self, settings: Settings, ledger: Ledger, client: Any | None = None):
        self.settings = settings
        self.ledger = ledger
        self.client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value(),
                                                    max_retries=0, timeout=settings.ai_timeout_seconds)

    @property
    def fallback_enabled(self) -> bool:
        return self.settings.anthropic_refusal_fallback == "default"

    def _params(self, system: str, tools: list[dict], messages: list[dict]) -> dict[str, Any]:
        return {
            "model": self.settings.anthropic_model,
            "max_tokens": self.settings.ai_max_output_tokens,
            "system": [{"type": "text", "text": system}],
            "tools": tools,
            "messages": messages,
            "output_config": {"effort": self.settings.anthropic_effort,
                              "format": {"type": "json_schema", "schema": PAYLOAD_SCHEMA}},
            "cache_control": {"type": "ephemeral"},
        }

    def _count_input_tokens(self, params: dict[str, Any]) -> int:
        try:
            counted = self.client.messages.count_tokens(
                model=params["model"], system=params["system"], tools=params["tools"],
                messages=params["messages"], output_config=params["output_config"])
            return int(counted.input_tokens)
        except anthropic.APIError as exc:
            # Conservative fallback estimate (about 3 characters per token) so a reservation is still made.
            log.warning("token counting failed (%s); using a conservative estimate", type(exc).__name__)
            return len(json.dumps([params["system"], params["tools"], params["messages"]], default=str)) // 3

    def create(self, *, system: str, tools: list[dict], messages: list[dict], user_id: str, turn_id: str | None,
               deadline: float) -> CallResult:
        params = self._params(system, tools, messages)
        input_tokens = self._count_input_tokens(params)
        attempt = 0
        while True:
            attempt += 1
            amount = self.ledger.upper_bound(params["model"], input_tokens, params["max_tokens"],
                                             self.fallback_enabled)
            usage_id = self.ledger.reserve(user_id=user_id, turn_id=turn_id, model=params["model"], amount=amount)
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                self.ledger.release(usage_id, "deadline_before_call")
                raise AIServiceError("ai_timeout", "The AI service timed out; your workbook is unchanged.",
                                     status_code=504)
            try:
                timeout = min(self.settings.ai_timeout_seconds, remaining)
                if self.fallback_enabled:
                    message = self.client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default",
                                                               timeout=timeout)
                else:
                    message = self.client.messages.create(**params, timeout=timeout)
            except anthropic.APITimeoutError:
                self.ledger.mark_uncertain(usage_id, "timeout")
                retry, wait = True, None
                error = AIServiceError("ai_timeout", "The AI service timed out; your workbook is unchanged.",
                                       status_code=504)
            except anthropic.APIConnectionError:
                self.ledger.mark_uncertain(usage_id, "connection_error")
                retry, wait = True, None
                error = AIServiceError("ai_connection", "The AI service could not be reached; your workbook is "
                                                        "unchanged.")
            except anthropic.AuthenticationError as exc:
                self.ledger.release(usage_id, "authentication_error")
                raise AIServiceError("ai_auth_failed", "AI access is not configured correctly (the API key was "
                                                       "rejected). The owner needs to check the key.",
                                     request_id=_request_id(exc)) from exc
            except anthropic.PermissionDeniedError as exc:
                self.ledger.release(usage_id, "permission_error")
                raise AIServiceError("ai_permission", "The AI key is not allowed to use this model or workspace.",
                                     request_id=_request_id(exc)) from exc
            except anthropic.NotFoundError as exc:
                self.ledger.release(usage_id, "not_found")
                raise AIServiceError("ai_model_unavailable", "The configured AI model is not available.",
                                     request_id=_request_id(exc)) from exc
            except anthropic.RateLimitError as exc:
                self.ledger.release(usage_id, "rate_limited")
                wait = _retry_after(exc)
                if wait is None:  # spend-cap 429s carry no retry-after and keep failing
                    raise AIServiceError("ai_provider_limit", "The AI provider's usage limit has been reached. "
                                                              "The owner can check limits in the Claude Console.",
                                         status_code=429, request_id=_request_id(exc)) from exc
                retry = True
                error = AIServiceError("ai_rate_limited", "The AI service is busy right now. Please try again "
                                                          "shortly; your workbook is unchanged.", status_code=429,
                                       request_id=_request_id(exc))
            except anthropic.BadRequestError as exc:
                self.ledger.release(usage_id, "bad_request")
                log.error("Claude API rejected the request (request id %s)", _request_id(exc))
                raise AIServiceError("ai_request_rejected", "The AI service rejected the request (this can also "
                                                            "mean the workspace spend limit was reached). Your "
                                                            "workbook is unchanged.", status_code=502,
                                     request_id=_request_id(exc)) from exc
            except anthropic.APIStatusError as exc:
                if exc.status_code == 402:
                    self.ledger.release(usage_id, "billing_error")
                    raise AIServiceError("ai_billing", "AI billing needs attention in the Claude Console.",
                                         request_id=_request_id(exc)) from exc
                if exc.status_code >= 500:
                    self.ledger.release(usage_id, f"server_error_{exc.status_code}")
                    retry, wait = True, _retry_after(exc)
                    error = AIServiceError("ai_unavailable", "The AI service is temporarily unavailable; your "
                                                             "workbook is unchanged.", request_id=_request_id(exc))
                else:
                    self.ledger.release(usage_id, f"status_{exc.status_code}")
                    raise AIServiceError("ai_error", "The AI service returned an error; your workbook is unchanged.",
                                         request_id=_request_id(exc)) from exc
            else:
                usages, fallback_used = _usages(message, params["model"])
                request_id = getattr(message, "_request_id", None)
                cost = self.ledger.reconcile(usage_id, request_id=request_id,
                                             served_model=getattr(message, "model", params["model"]),
                                             usages=usages, fallback_used=fallback_used)
                return CallResult(message, request_id, cost, fallback_used)

            if not retry or attempt > self.settings.ai_max_retries:
                raise error
            delay = wait if wait is not None else min(2 ** (attempt - 1), 20) + random.uniform(0, 1)
            if time.monotonic() + delay >= deadline - 2:
                raise error
            time.sleep(delay)

    def connection_check(self, *, user_id: str, live: bool) -> dict[str, Any]:
        """Verify key, workspace and model. `live` additionally sends one minimal message (small cost)."""
        try:
            raw = self.client.models.with_raw_response.retrieve(self.settings.anthropic_model)
            model = raw.parse()
            result: dict[str, Any] = {"model": model.id, "display_name": getattr(model, "display_name", None),
                                      "workspace_id": raw.headers.get("anthropic-workspace-id"), "live": False}
        except anthropic.AuthenticationError as exc:
            raise AIServiceError("ai_auth_failed", "The API key was rejected.") from exc
        except anthropic.NotFoundError as exc:
            raise AIServiceError("ai_model_unavailable", f"Model {self.settings.anthropic_model} is not available "
                                                         "to this key.") from exc
        except anthropic.APIError as exc:
            raise AIServiceError("ai_unavailable", f"Connection check failed ({type(exc).__name__}).") from exc
        if live:
            usage_id = self.ledger.reserve(user_id=user_id, turn_id=None, model=self.settings.anthropic_model,
                                           amount=self.ledger.upper_bound(self.settings.anthropic_model, 50, 256,
                                                                          False), purpose="connection_check")
            try:
                message = self.client.messages.create(
                    model=self.settings.anthropic_model, max_tokens=256, output_config={"effort": "low"},
                    messages=[{"role": "user", "content": "Reply with the single word: ready"}])
            except anthropic.APIError as exc:
                self.ledger.mark_uncertain(usage_id, type(exc).__name__)
                raise AIServiceError("ai_unavailable", f"Live check failed ({type(exc).__name__}).") from exc
            usages, _ = _usages(message, self.settings.anthropic_model)
            cost = self.ledger.reconcile(usage_id, request_id=message._request_id, served_model=message.model,
                                         usages=usages, fallback_used=False)
            result.update({"live": True, "stop_reason": message.stop_reason, "request_id": message._request_id,
                           "estimated_cost_usd": round(cost, 6)})
        return result
