"""REST API (OpenAPI docs at /api/docs)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.ai.demo import SUGGESTED_PROMPTS
from app.db import session_scope
from app.errors import AppError, Forbidden, NotFound
from app.models import Job, Scenario, User, Workbook
from app.services import scenarios as scenario_service
from app.services import workbooks as wbs
from app.services.jobs import job_dict

router = APIRouter(prefix="/api")


def current_user(request: Request) -> User:
    auth = request.app.state.authenticator
    auth.check_csrf(request)
    return auth.user_for(request)


def _ctx(request: Request):
    return request.app.state.ctx


def _is_owner(request: Request, user: User) -> bool:
    return request.app.state.authenticator.is_owner(user)


# ------------------------------------------------------------------------- request bodies
class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageIn(_Body):
    text: str = Field(max_length=4000)
    request_key: str | None = Field(None, max_length=100)


class MapUpdateIn(_Body):
    expected_mapping_version_id: str
    map: dict[str, Any]


class ReferenceOutputsIn(_Body):
    cells: dict[str, float | int | str | bool]


class ChangeIn(_Body):
    field_id: str
    operation: Literal["set", "add", "multiply"]
    operand: float | int | str | bool
    rationale: str = ""


class ScenarioIn(_Body):
    base_version_id: str
    name: str = Field(min_length=1, max_length=120)
    interpreted_request: str = ""
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    changes: list[ChangeIn] = Field(min_length=1, max_length=25)
    revision_of_scenario_id: str | None = None


class ApplyIn(_Body):
    expected_active_version_id: str


class RestoreIn(_Body):
    target_version_id: str
    expected_active_version_id: str


class AICheckIn(_Body):
    live: bool = False


# ------------------------------------------------------------------------- session / health
@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    """Component readiness. Returns no secrets, filenames, paths, or user information."""
    return request.app.state.health()


@router.get("/me")
def me(request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    s = _ctx(request).settings
    return {"email": user.email, "is_owner": _is_owner(request, user), "ai_mode": s.ai_mode,
            "ai_model": s.anthropic_model if s.ai_mode == "anthropic" else None,
            "suggested_prompts": SUGGESTED_PROMPTS,
            "privacy_policy_url": ("https://platform.claude.com/docs/en/manage-claude/api-and-data-retention"
                                   if s.ai_mode == "anthropic" else None),
            "privacy_notice": ("Workbook files and history are stored on this application's server. "
                               + ("In AI mode, your messages and the relevant workbook excerpts (labels, values, and "
                                  "formulas needed to answer) are sent to Anthropic's Claude API and handled under "
                                  "Anthropic's data-retention policies. Hidden sheets, rows, and columns are not sent."
                                  if s.ai_mode == "anthropic" else
                                  "Demo mode uses scripted responses; nothing is sent to an AI provider."))}


# ------------------------------------------------------------------------- workbooks
@router.get("/workbooks")
def list_workbooks(request: Request, user: User = Depends(current_user)) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(select(Workbook).where(Workbook.owner_id == user.id)
                               .order_by(Workbook.created_at.desc())).all()
        return [{"id": w.id, "name": w.name, "is_demo": w.is_demo, "compatibility_status": w.compatibility_status,
                 "created_at": w.created_at.isoformat()} for w in rows]


@router.post("/workbooks", status_code=201)
async def upload_workbook(request: Request, file: UploadFile = File(...),
                          user: User = Depends(current_user)) -> dict[str, Any]:
    ctx = _ctx(request)
    data = await file.read(ctx.settings.max_upload_bytes + 1)
    if len(data) > ctx.settings.max_upload_bytes:
        raise AppError("file_too_large", "The file is larger than the upload limit.", status_code=413)
    with session_scope() as session:
        wb, job = wbs.import_workbook(session, ctx, user, file.filename or "workbook.xlsx", data)
        workbook_id, job_id = wb.id, job.id if job else None
    with session_scope() as session:
        wb = session.get(Workbook, workbook_id)
        return {"workbook": wbs.workbook_summary(session, ctx, wb, admin=_is_owner(request, user)), "job_id": job_id}


@router.post("/workbooks/demo", status_code=201)
def load_demo(request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    ctx = _ctx(request)
    with session_scope() as session:
        wb, job = wbs.load_demo(session, ctx, user)
        workbook_id, job_id = wb.id, job.id if job else None
    with session_scope() as session:
        wb = session.get(Workbook, workbook_id)
        return {"workbook": wbs.workbook_summary(session, ctx, wb, admin=_is_owner(request, user)), "job_id": job_id}


@router.get("/workbooks/{workbook_id}")
def get_workbook(workbook_id: str, request: Request, admin: bool = False,
                 user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as session:
        wb = wbs.get_owned_workbook(session, user, workbook_id)
        summary = wbs.workbook_summary(session, _ctx(request), wb, admin=admin)
        version = wbs.get_version(session, wb, wb.active_version_id)
        summary["active_results"] = version.results
        return summary


@router.put("/workbooks/{workbook_id}/map")
def update_map(workbook_id: str, body: MapUpdateIn, request: Request,
               user: User = Depends(current_user)) -> dict[str, Any]:
    ctx = _ctx(request)
    with session_scope() as session:
        wb = wbs.get_owned_workbook(session, user, workbook_id)
        mv = wbs.update_map(session, ctx, user, wb, body.map, body.expected_mapping_version_id)
        return {"mapping_version_id": mv.id, "seq": mv.seq}


@router.post("/workbooks/{workbook_id}/confirm-baseline")
def confirm_baseline(workbook_id: str, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as session:
        wb = wbs.get_owned_workbook(session, user, workbook_id)
        wbs.confirm_baseline(session, _ctx(request), user, wb)
        return {"compatibility_status": wb.compatibility_status}


@router.put("/workbooks/{workbook_id}/reference-outputs")
def reference_outputs(workbook_id: str, body: ReferenceOutputsIn, request: Request,
                      user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as session:
        wb = wbs.get_owned_workbook(session, user, workbook_id)
        check = wbs.set_reference_outputs(session, _ctx(request), user, wb, body.cells)
        return {"check": check, "compatibility_status": wb.compatibility_status}


@router.delete("/workbooks/{workbook_id}", status_code=204)
def delete_workbook(workbook_id: str, request: Request, x_confirm_delete: str | None = Header(None),
                    user: User = Depends(current_user)) -> Response:
    if x_confirm_delete != workbook_id:
        raise AppError("confirmation_required", "Deleting a workbook must be confirmed.", status_code=400)
    with session_scope() as session:
        wb = wbs.get_owned_workbook(session, user, workbook_id)
        wbs.delete_workbook(session, _ctx(request), user, wb)
    return Response(status_code=204)


# ------------------------------------------------------------------------- conversation
@router.post("/workbooks/{workbook_id}/messages")
def post_message(workbook_id: str, body: MessageIn, request: Request,
                 user: User = Depends(current_user)) -> dict[str, Any]:
    return request.app.state.chat.post_message(user, workbook_id, body.text, body.request_key)


@router.get("/workbooks/{workbook_id}/messages")
def list_messages(workbook_id: str, request: Request, after: int = 0,
                  user: User = Depends(current_user)) -> dict[str, Any]:
    return request.app.state.chat.list_messages(user, workbook_id, after)


# ------------------------------------------------------------------------- scenarios
def _owned_scenario(session, user: User, scenario_id: str) -> tuple[Workbook, Scenario]:
    scenario = session.get(Scenario, scenario_id)
    if scenario is None:
        raise NotFound("scenario")
    wb = wbs.get_owned_workbook(session, user, scenario.workbook_id)
    return wb, scenario


@router.post("/workbooks/{workbook_id}/scenarios", status_code=201)
def create_scenario(workbook_id: str, body: ScenarioIn, request: Request,
                    user: User = Depends(current_user)) -> dict[str, Any]:
    """Validated proposal from an explicit base version, then recalculation (poll the job / scenario)."""
    ctx = _ctx(request)
    with session_scope() as session:
        wb = wbs.get_owned_workbook(session, user, workbook_id)
        scenario = scenario_service.propose(
            session, ctx, user, wb, changes=[c.model_dump() for c in body.changes], name=body.name,
            interpreted_request=body.interpreted_request, assumptions=body.assumptions,
            base_version_id=body.base_version_id, mapping_version_id=None,
            revision_of_id=body.revision_of_scenario_id, conversation_id=None)
        job = scenario_service.run(session, ctx, scenario)
        return {"scenario": scenario_service.scenario_dict(session, wb, scenario), "job_id": job.id if job else None}


@router.get("/workbooks/{workbook_id}/scenarios")
def list_scenarios(workbook_id: str, request: Request, user: User = Depends(current_user)) -> list[dict[str, Any]]:
    with session_scope() as session:
        wb = wbs.get_owned_workbook(session, user, workbook_id)
        rows = session.scalars(select(Scenario).where(Scenario.workbook_id == wb.id)
                               .order_by(Scenario.created_at.desc()).limit(100)).all()
        return [{"id": s.id, "name": s.name, "status": s.status, "base_version_id": s.base_version_id,
                 "created_at": s.created_at.isoformat(), "applied_version_id": s.applied_version_id} for s in rows]


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as session:
        wb, scenario = _owned_scenario(session, user, scenario_id)
        return scenario_service.scenario_dict(session, wb, scenario)


@router.post("/scenarios/{scenario_id}/apply")
def apply_scenario(scenario_id: str, body: ApplyIn, request: Request,
                   idempotency_key: str | None = Header(None), user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as session:
        wb, scenario = _owned_scenario(session, user, scenario_id)
        _, response = scenario_service.apply(session, _ctx(request), user, wb, scenario,
                                             expected_active_version_id=body.expected_active_version_id,
                                             idempotency_key=idempotency_key or "")
        return response


@router.post("/scenarios/{scenario_id}/discard")
def discard_scenario(scenario_id: str, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as session:
        wb, scenario = _owned_scenario(session, user, scenario_id)
        scenario_service.discard(session, _ctx(request), user, scenario)
        return {"id": scenario.id, "status": scenario.status}


@router.get("/scenarios/{scenario_id}/download")
def download_scenario(scenario_id: str, request: Request, user: User = Depends(current_user)) -> Response:
    ctx = _ctx(request)
    with session_scope() as session:
        wb, scenario = _owned_scenario(session, user, scenario_id)
        if scenario.status not in ("ready", "applied") or not scenario.candidate_file_path:
            raise AppError("not_available", "Only a calculated scenario can be downloaded.", status_code=409)
        data = ctx.store.resolve(scenario.candidate_file_path).read_bytes()
        name = f"{wb.name} - SCENARIO PREVIEW (not applied) - {scenario.name}.xlsx"
        wbs.audit(session, "scenario_downloaded", workbook_id=wb.id, user_id=user.id, scenario_id=scenario.id)
    return _xlsx(data, name, {"X-Scenario-Id": scenario_id, "X-Scenario-Preview": "true"})


# ------------------------------------------------------------------------- versions
@router.get("/workbooks/{workbook_id}/versions")
def versions(workbook_id: str, request: Request, user: User = Depends(current_user)) -> list[dict[str, Any]]:
    with session_scope() as session:
        return wbs.list_versions(session, wbs.get_owned_workbook(session, user, workbook_id))


@router.post("/workbooks/{workbook_id}/restore")
def restore(workbook_id: str, body: RestoreIn, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as session:
        wb = wbs.get_owned_workbook(session, user, workbook_id)
        version = wbs.restore_version(session, _ctx(request), user, wb, body.target_version_id,
                                      body.expected_active_version_id)
        return {"active_version": wbs.version_dict(version), "mapping_version_id": wb.active_mapping_version_id}


@router.get("/workbooks/{workbook_id}/compare")
def compare(workbook_id: str, request: Request, base_version_id: str = Query(...),
            compared_version_id: str = Query(...), user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as session:
        wb = wbs.get_owned_workbook(session, user, workbook_id)
        return scenario_service.compare_versions(session, _ctx(request), wb, base_version_id, compared_version_id)


@router.get("/workbooks/{workbook_id}/export")
def export(workbook_id: str, request: Request, version_id: str = Query(...),
           user: User = Depends(current_user)) -> Response:
    with session_scope() as session:
        wb = wbs.get_owned_workbook(session, user, workbook_id)
        data, name, version = wbs.export_version(session, _ctx(request), user, wb, version_id)
        headers = {"X-Version-Id": version.id, "X-Version-Seq": str(version.seq), "X-Version-Sha256": version.sha256}
    return _xlsx(data, name, headers)


def _xlsx(data: bytes, filename: str, headers: dict[str, str]) -> Response:
    from urllib.parse import quote
    safe = filename.encode("ascii", "replace").decode().replace('"', "'")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=\"{safe}\"; filename*=UTF-8''{quote(filename)}",
                             "Cache-Control": "no-store", **headers})


# ------------------------------------------------------------------------- jobs
@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None or job.workbook_id is None:
            raise NotFound("job")
        wbs.get_owned_workbook(session, user, job.workbook_id)
        return job_dict(job)


# ------------------------------------------------------------------------- owner-only
def _require_owner(request: Request, user: User) -> None:
    if not _is_owner(request, user):
        raise Forbidden("This page is only available to the application owner.")


@router.get("/admin/usage")
def usage(request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    _require_owner(request, user)
    ledger = request.app.state.ledger
    s = _ctx(request).settings
    base = {"ai_mode": s.ai_mode, "model": s.anthropic_model, "effort": s.anthropic_effort,
            "refusal_fallback": s.anthropic_refusal_fallback}
    if ledger is None:
        return {**base, "note": "AI usage tracking is active only in anthropic mode."}
    return {**base, **ledger.summary()}


@router.post("/admin/ai-check")
def ai_check(body: AICheckIn, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    _require_owner(request, user)
    adapter = request.app.state.adapter
    if adapter is None:
        raise AppError("ai_not_configured", "AI access is not configured (demo mode).", status_code=409)
    return adapter.connection_check(user_id=user.id, live=body.live)
