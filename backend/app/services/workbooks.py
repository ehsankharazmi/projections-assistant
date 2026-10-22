"""Workbook import, compatibility gate, versions, mapping, restore, export, and deletion."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calc import protocol
from app.context import AppContext
from app.errors import AppError, Conflict, Forbidden, NotFound
from app.mapping.infer import infer_map
from app.mapping.schema import MappingInvalid, WorkbookMap, validate_against_workbook
from app.models import (
    AuditEvent,
    Conversation,
    Job,
    MappingVersion,
    Scenario,
    User,
    Validation,
    Workbook,
    WorkbookVersion,
    new_id,
    utcnow,
)
from app.storage import sha256_bytes
from app.workbook.package import PackageLimits, validate_package

PENDING_SCENARIO_STATES = ("draft", "needs_clarification", "validating", "calculating", "ready")


def audit(session: Session, kind: str, *, workbook_id: str | None, user_id: str | None, **details: Any) -> None:
    session.add(AuditEvent(kind=kind, workbook_id=workbook_id, user_id=user_id, details=details))


def get_owned_workbook(session: Session, user: User, workbook_id: str) -> Workbook:
    wb = session.get(Workbook, workbook_id)
    if wb is None or wb.owner_id != user.id:  # not-owned looks like not-found: no existence leak
        raise NotFound("workbook")
    return wb


def get_version(session: Session, wb: Workbook, version_id: str) -> WorkbookVersion:
    version = session.get(WorkbookVersion, version_id)
    if version is None or version.workbook_id != wb.id:
        raise NotFound("version")
    return version


def active_map(session: Session, wb: Workbook) -> tuple[MappingVersion, WorkbookMap]:
    mv = session.get(MappingVersion, wb.active_mapping_version_id)
    if mv is None:
        raise AppError("no_mapping", "This workbook has no map yet.", status_code=409)
    return mv, WorkbookMap.model_validate(mv.content)


def _display_name(filename: str) -> str:
    stem = Path(filename.replace("\\", "/")).name
    stem = re.sub(r"\.xlsx$", "", stem, flags=re.I)
    stem = re.sub(r"[^\w .()\-]+", "_", stem).strip() or "Workbook"
    return stem[:100]


def _reference_from_cells(cells: dict[str, Any] | None) -> dict[tuple[str, str], Any] | None:
    if not cells:
        return None
    out = {}
    for key, value in cells.items():
        sheet, _, addr = key.rpartition("!")
        if not sheet or not addr:
            raise AppError("invalid_reference_outputs", f"Reference output key '{key}' must look like Sheet!A1.",
                           status_code=422)
        out[(sheet.strip("'"), addr.replace("$", "").upper())] = value
    return out


def import_workbook(session: Session, ctx: AppContext, user: User, filename: str, data: bytes, *,
                    demo: bool = False, demo_map: dict | None = None,
                    reference_outputs: dict | None = None) -> tuple[Workbook, Job | None]:
    s = ctx.settings
    validate_package(data, filename, PackageLimits(s.max_upload_bytes, s.max_zip_members,
                                                   s.max_uncompressed_bytes, s.max_member_compression_ratio))
    inspection = ctx.engine.inspect(data)  # bounded, in a child process
    wb = Workbook(id=new_id("wb"), owner_id=user.id, name=_display_name(filename), is_demo=demo,
                  status="ready", inspection=inspection, original_sha256=sha256_bytes(data),
                  reference_outputs=reference_outputs)
    session.add(wb)
    session.flush()
    rel = ctx.store.save_original(wb.id, data)

    raw_map = demo_map if demo_map is not None else infer_map(inspection)
    wmap = WorkbookMap.model_validate(raw_map)
    problems = validate_against_workbook(wmap, inspection)
    if problems:
        raise MappingInvalid(problems)
    mv = MappingVersion(workbook_id=wb.id, seq=1, source=wmap.source, content=wmap.model_dump(),
                        created_by=user.id)
    session.add(mv)
    session.flush()
    version = WorkbookVersion(id=new_id("ver"), workbook_id=wb.id, seq=1, kind="original",
                              label="Original upload", file_path=rel, values_file_path=rel,
                              values_source="excel_cached", sha256=wb.original_sha256, size_bytes=len(data),
                              mapping_version_id=mv.id)
    session.add(version)
    wb.active_version_id = version.id
    wb.active_mapping_version_id = mv.id
    session.add(Conversation(workbook_id=wb.id, user_id=user.id))

    job = None
    if inspection["compatibility"]["preflight"] == "blocked":
        wb.compatibility_status = "inspect_only"
        wb.compatibility_report = {"preflight": inspection["compatibility"], "round_trip": None}
    else:
        wb.compatibility_status = "pending"
        wb.compatibility_report = {"preflight": inspection["compatibility"], "round_trip": None}
        job = Job(id=new_id("job"), kind="recalc", purpose="round_trip", workbook_id=wb.id, status="queued",
                  attempts=1)
        session.add(job)
        no_change = ctx.engine.apply_inputs(data, [])  # the same editing pipeline scenarios use
        ctx.engine.submit_recalculation(job.id, no_change)
    audit(session, "workbook_imported", workbook_id=wb.id, user_id=user.id, demo=demo,
          sha256=wb.original_sha256, preflight=inspection["compatibility"]["preflight"])
    return wb, job


def load_demo(session: Session, ctx: AppContext, user: User) -> tuple[Workbook, Job | None]:
    from app.demo.generate import MAP_FILENAME, REFERENCE_FILENAME, WORKBOOK_FILENAME, generate

    demo_dir = ctx.settings.demo_dir
    if not (demo_dir / WORKBOOK_FILENAME).exists():
        generate(demo_dir)
    data = (demo_dir / WORKBOOK_FILENAME).read_bytes()
    reference = json.loads((demo_dir / REFERENCE_FILENAME).read_text())
    demo_map = json.loads((demo_dir / MAP_FILENAME).read_text())
    return import_workbook(session, ctx, user, "Demo - Product margins (synthetic).xlsx", data, demo=True,
                           demo_map=demo_map, reference_outputs={"cells": reference["baseline_cells"],
                                                                 "source": "demo generator (trusted, independent)"})


def finalize_round_trip(session: Session, ctx: AppContext, job: Job, outcome) -> None:
    wb = session.get(Workbook, job.workbook_id)
    if wb is None:
        return
    report = dict(wb.compatibility_report or {})
    if not outcome.ok:
        wb.compatibility_status = "inspect_only"
        report["round_trip"] = {"passed": False, "engine_error": {"code": outcome.error_code,
                                                                   "message": outcome.error_message}}
        wb.compatibility_report = report
        session.add(Validation(workbook_id=wb.id, kind="round_trip", passed=False, details=report["round_trip"]))
        return
    out_inspection = ctx.engine.inspect(outcome.data)
    reference = _reference_from_cells((wb.reference_outputs or {}).get("cells"))
    result = ctx.engine.validate_round_trip(wb.inspection, out_inspection, reference)
    report["round_trip"] = result
    rel = ctx.store.save_version(wb.id, new_id("ver"), outcome.data)  # recalculated baseline (values only)
    original = session.scalars(select(WorkbookVersion).where(WorkbookVersion.workbook_id == wb.id,
                                                             WorkbookVersion.seq == 1)).one()
    if result["passed"]:
        original.values_file_path = rel
        original.values_source = "recalculated"
    missing_cache = wb.inspection["compatibility"]["formula_cells_without_cache"]
    if not result["passed"]:
        wb.compatibility_status = "inspect_only"
    elif missing_cache and not reference and not wb.baseline_confirmed_by:
        wb.compatibility_status = "needs_baseline_confirmation"
    else:
        wb.compatibility_status = "editable"
    wb.compatibility_report = report
    session.add(Validation(workbook_id=wb.id, kind="round_trip", passed=result["passed"], details=result))
    _refresh_version_results(session, ctx, wb)


def confirm_baseline(session: Session, ctx: AppContext, user: User, wb: Workbook) -> None:
    if user.email != ctx.settings.effective_owner_email:
        raise Forbidden("Only the administrator can confirm baseline results.")
    if wb.compatibility_status != "needs_baseline_confirmation":
        raise Conflict("not_awaiting_confirmation", "This workbook is not waiting for baseline confirmation.")
    wb.baseline_confirmed_by = user.id
    wb.compatibility_status = "editable"
    audit(session, "baseline_confirmed", workbook_id=wb.id, user_id=user.id)


def set_reference_outputs(session: Session, ctx: AppContext, user: User, wb: Workbook,
                          cells: dict[str, Any]) -> dict:
    """Administrator-supplied trusted outputs ({"Sheet!A1": value}); re-checked against the recalculation."""
    if user.email != ctx.settings.effective_owner_email:
        raise Forbidden("Only the administrator can provide reference outputs.")
    reference = _reference_from_cells(cells)
    original = session.scalars(select(WorkbookVersion).where(WorkbookVersion.workbook_id == wb.id,
                                                             WorkbookVersion.seq == 1)).one()
    if original.values_source != "recalculated":
        raise Conflict("no_recalculation", "The workbook has not passed the round-trip check, so there are "
                                           "no recalculated results to compare with.")
    from app.workbook import compare
    out = ctx.engine.inspect(ctx.store.resolve(original.values_file_path).read_bytes())
    check = compare.compare_formula_values(reference, out, ctx.tolerance, code="matches_trusted_reference_outputs",
                                           label="the trusted reference outputs").as_dict()
    wb.reference_outputs = {"cells": cells, "source": f"administrator {user.email}"}
    report = dict(wb.compatibility_report or {})
    report["reference_check"] = check
    wb.compatibility_report = report
    if check["passed"] and wb.compatibility_status == "needs_baseline_confirmation":
        wb.compatibility_status = "editable"
    elif not check["passed"]:
        wb.compatibility_status = "inspect_only"
    audit(session, "reference_outputs_set", workbook_id=wb.id, user_id=user.id, passed=check["passed"])
    return check


def results_snapshot(ctx: AppContext, wmap: WorkbookMap, values_data: bytes) -> dict[str, Any]:
    """Read the mapped outputs (and inputs) of a workbook state."""
    cells: list[tuple[str, str]] = [(f.sheet, f.address) for f in wmap.fields]
    for t in wmap.output_tables:
        for row in range(t.first_row, t.last_row + 1):
            cells.append((t.sheet, f"{t.row_label_column}{row}"))
            cells.extend((t.sheet, f"{c.column}{row}") for c in t.columns)
        if t.total_row:
            cells.extend((t.sheet, f"{c.column}{t.total_row}") for c in t.columns)
    values = ctx.engine.read_results(values_data, cells)
    fields = {f.id: _plain(values.get((f.sheet, f.address))) for f in wmap.fields}
    tables = {}
    for t in wmap.output_tables:
        rows = []
        for row in range(t.first_row, t.last_row + 1):
            label = values.get((t.sheet, f"{t.row_label_column}{row}"))
            rows.append({"row": row, "label": str(label) if label is not None else f"Row {row}",
                         "values": {c.id: _plain(values.get((t.sheet, f"{c.column}{row}"))) for c in t.columns}})
        total = ({c.id: _plain(values.get((t.sheet, f"{c.column}{t.total_row}"))) for c in t.columns}
                 if t.total_row else None)
        tables[t.id] = {"rows": rows, "total": total}
    return {"fields": fields, "tables": tables}


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def version_results(session: Session, ctx: AppContext, version: WorkbookVersion,
                    wmap: WorkbookMap | None = None) -> dict[str, Any]:
    if wmap is None:
        mv = session.get(MappingVersion, version.mapping_version_id)
        wmap = WorkbookMap.model_validate(mv.content)
    return results_snapshot(ctx, wmap, ctx.store.resolve(version.values_file_path).read_bytes())


def _refresh_version_results(session: Session, ctx: AppContext, wb: Workbook) -> None:
    version = session.get(WorkbookVersion, wb.active_version_id)
    mv, wmap = active_map(session, wb)
    if version.mapping_version_id == mv.id:
        version.results = version_results(session, ctx, version, wmap)


def update_map(session: Session, ctx: AppContext, user: User, wb: Workbook, content: dict[str, Any],
               expected_mapping_version_id: str) -> MappingVersion:
    with ctx.workbook_lock(wb.id):
        if wb.active_mapping_version_id != expected_mapping_version_id:
            raise Conflict("stale_mapping", "The workbook map was changed elsewhere. Reload and try again.")
        wmap = WorkbookMap.model_validate({**content, "source": "reviewed"})
        problems = validate_against_workbook(wmap, wb.inspection)
        if problems:
            raise MappingInvalid(problems)
        seq = (session.scalar(select(func.max(MappingVersion.seq)).where(MappingVersion.workbook_id == wb.id)) or 0) + 1
        mv = MappingVersion(workbook_id=wb.id, seq=seq, source="reviewed", content=wmap.model_dump(),
                            created_by=user.id)
        session.add(mv)
        session.flush()
        wb.active_mapping_version_id = mv.id
        stale = session.scalars(select(Scenario).where(Scenario.workbook_id == wb.id,
                                                       Scenario.status.in_(PENDING_SCENARIO_STATES))).all()
        for scenario in stale:
            scenario.status = "superseded"
            scenario.status_reason = "mapping_changed"
        audit(session, "mapping_updated", workbook_id=wb.id, user_id=user.id, mapping_version_id=mv.id,
              invalidated_scenarios=[s.id for s in stale])
        return mv


def restore_version(session: Session, ctx: AppContext, user: User, wb: Workbook, target_version_id: str,
                    expected_active_version_id: str) -> WorkbookVersion:
    with ctx.workbook_lock(wb.id):
        if wb.active_version_id != expected_active_version_id:
            raise Conflict("stale_active_version", "The active version changed elsewhere. Reload and try again.")
        target = get_version(session, wb, target_version_id)
        if ctx.store.resolve(target.file_path).exists() is False:
            raise AppError("artifact_missing", "The stored file for that version is missing.", status_code=500)
        previous = wb.active_version_id
        wb.active_version_id = target.id
        wb.active_mapping_version_id = target.mapping_version_id  # workbook and compatible map together
        audit(session, "version_restored", workbook_id=wb.id, user_id=user.id, from_version=previous,
              to_version=target.id, mapping_version_id=target.mapping_version_id)
        return target


def export_version(session: Session, ctx: AppContext, user: User, wb: Workbook,
                   version_id: str) -> tuple[bytes, str, WorkbookVersion]:
    version = get_version(session, wb, version_id)
    data = ctx.store.resolve(version.file_path).read_bytes()
    if sha256_bytes(data) != version.sha256:
        raise AppError("artifact_corrupted", "The stored file no longer matches its recorded fingerprint.",
                       status_code=500)
    audit(session, "version_exported", workbook_id=wb.id, user_id=user.id, version_id=version.id)
    return data, f"{wb.name} - version {version.seq}.xlsx", version


def delete_workbook(session: Session, ctx: AppContext, user: User, wb: Workbook) -> None:
    with ctx.workbook_lock(wb.id):
        job_ids = session.scalars(select(Job.id).where(Job.workbook_id == wb.id)).all()
        workbook_id = wb.id
        session.delete(wb)  # cascades to versions, maps, conversations, messages, scenarios, jobs, validations
        session.execute(AuditEvent.__table__.delete().where(AuditEvent.workbook_id == workbook_id))
        session.flush()
        ctx.store.delete_workbook(workbook_id)
        for job_id in job_ids:
            protocol.remove_job(ctx.settings.jobs_dir, job_id)
        session.add(AuditEvent(kind="workbook_deleted", workbook_id=None, user_id=user.id,
                               details={"deleted_at": utcnow().isoformat()}))


def workbook_summary(session: Session, ctx: AppContext, wb: Workbook, *, admin: bool) -> dict[str, Any]:
    mv, wmap = active_map(session, wb)
    version = session.get(WorkbookVersion, wb.active_version_id)
    insp = wb.inspection or {}
    summary: dict[str, Any] = {
        "id": wb.id, "name": wb.name, "is_demo": wb.is_demo,
        "compatibility_status": wb.compatibility_status,
        "editing_enabled": wb.compatibility_status == "editable",
        "active_version": _version_dict(version),
        "mapping_version_id": mv.id, "mapping_seq": mv.seq,
        "map": {**wmap.model_dump(), "workbook_id": wb.id, "mapping_version_id": mv.id,
                "source_version_id": version.id},
        "unconfirmed_fields": sum(1 for f in wmap.fields if not f.confirmed),
        "sheets": [{"name": s["name"], "state": s.get("state")} for s in insp.get("sheets", [])],
        "compatibility": {
            "blocking": (wb.compatibility_report or {}).get("preflight", {}).get("blocking", []),
            "warnings": (wb.compatibility_report or {}).get("preflight", {}).get("warnings", []),
            "round_trip": _round_trip_summary((wb.compatibility_report or {}).get("round_trip")),
            "reference_check": (wb.compatibility_report or {}).get("reference_check"),
        },
    }
    if admin:
        summary["admin"] = {
            "inspection": {k: insp.get(k) for k in ("sheets", "defined_names", "functions_used", "features",
                                                    "populated_cells", "compatibility")},
            "round_trip": (wb.compatibility_report or {}).get("round_trip"),
            "original_sha256": wb.original_sha256,
            "reference_outputs_source": (wb.reference_outputs or {}).get("source"),
            "baseline_confirmed": bool(wb.baseline_confirmed_by),
        }
    return summary


def _round_trip_summary(rt: dict | None) -> dict | None:
    if rt is None:
        return None
    return {"passed": rt.get("passed"), "fidelity_verified": rt.get("fidelity_verified"),
            "engine_error": rt.get("engine_error"),
            "failed_checks": [c["message"] for c in rt.get("checks", []) if not c["passed"]]}


def _version_dict(v: WorkbookVersion | None) -> dict[str, Any] | None:
    if v is None:
        return None
    return {"id": v.id, "seq": v.seq, "label": v.label, "kind": v.kind, "parent_version_id": v.parent_version_id,
            "sha256": v.sha256, "mapping_version_id": v.mapping_version_id, "scenario_id": v.scenario_id,
            "values_source": v.values_source, "created_at": v.created_at.isoformat()}


def list_versions(session: Session, wb: Workbook) -> list[dict[str, Any]]:
    versions = session.scalars(select(WorkbookVersion).where(WorkbookVersion.workbook_id == wb.id)
                               .order_by(WorkbookVersion.seq)).all()
    return [{**_version_dict(v), "active": v.id == wb.active_version_id} for v in versions]


version_dict = _version_dict
