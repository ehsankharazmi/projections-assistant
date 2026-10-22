"""Scenario lifecycle: propose -> calculate -> preview -> apply / discard.

Lifecycle and allowed transitions:

    draft ---------------> validating -> calculating -> ready ----> applied
      |                         |             |           |-------> discarded
      |                         v             v           '-------> superseded
      '-> needs_clarification  failed <-------'
                                  |--> discarded
                                  '--> superseded

`superseded` is also reachable from draft/validating/calculating/needs_clarification
when a revision replaces the scenario or the workbook map changes.

All numbers (new input values, before/after values, deltas) are computed here,
never by the model.
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.context import AppContext
from app.errors import AppError, Conflict, NotFound
from app.mapping.schema import NUMERIC_TYPES, FieldDef, WorkbookMap
from app.models import (
    IdempotencyRecord,
    Job,
    MappingVersion,
    Scenario,
    ScenarioChange,
    User,
    Validation,
    Workbook,
    WorkbookVersion,
    new_id,
)
from app.services import workbooks as wbs
from app.storage import sha256_bytes
from app.workbook import compare
from app.workbook.engine import FORMULA_PREFIXES, CellWrite

TRANSITIONS: dict[str, set[str]] = {
    "draft": {"validating", "needs_clarification", "failed", "superseded", "discarded"},
    "needs_clarification": {"superseded", "discarded"},
    "validating": {"calculating", "failed", "superseded"},
    "calculating": {"ready", "failed", "superseded"},
    "ready": {"applied", "discarded", "superseded"},
    "failed": {"discarded", "superseded"},
    "applied": set(),
    "discarded": set(),
    "superseded": set(),
}


class ChangeRejected(AppError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(code, message, status_code=422, details=details)


def transition(scenario: Scenario, new_status: str, reason: str | None = None) -> None:
    if new_status not in TRANSITIONS[scenario.status]:
        raise Conflict("invalid_transition", f"A scenario that is {scenario.status} cannot become {new_status}.")
    scenario.status = new_status
    scenario.status_reason = reason


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def resolve_change(field: FieldDef | None, operation: str, operand: Any, old: Any, field_id: str) -> Any:
    """Validate one requested change against the confirmed map and compute the new value."""
    if field is None:
        raise ChangeRejected("unknown_field", f"There is no mapped field '{field_id}'.")
    if field.role != "input":
        raise ChangeRejected("not_an_input", f"'{field.label}' is a calculated result and cannot be changed "
                                             "directly. Change the inputs it depends on instead.")
    if not field.confirmed or not field.editable:
        raise ChangeRejected("field_not_editable", f"'{field.label}' has not been approved as an editable input.")
    if operation not in ("set", "add", "multiply"):
        raise ChangeRejected("invalid_operation", "Operation must be set, add, or multiply.")
    if field.value_type in NUMERIC_TYPES:
        if not _is_number(operand):
            raise ChangeRejected("invalid_operand", f"'{field.label}' needs a numeric value.")
        if operation != "set" and not _is_number(old):
            raise ChangeRejected("invalid_current_value", f"The current value of '{field.label}' is not a number.")
        new = operand if operation == "set" else old + operand if operation == "add" else old * operand
        if not math.isfinite(new):
            raise ChangeRejected("invalid_result", "The resulting value is not a finite number.")
        if field.value_type == "integer":
            if not float(new).is_integer():
                raise ChangeRejected("not_an_integer", f"'{field.label}' must be a whole number.")
            new = int(new)
        if field.minimum is not None and new < field.minimum:
            raise ChangeRejected("below_minimum", f"'{field.label}' cannot be below {field.minimum:g} {field.unit}"
                                 .strip() + f" (requested {new:g}).", {"minimum": field.minimum, "requested": new})
        if field.maximum is not None and new > field.maximum:
            raise ChangeRejected("above_maximum", f"'{field.label}' cannot be above {field.maximum:g} {field.unit}"
                                 .strip() + f" (requested {new:g}).", {"maximum": field.maximum, "requested": new})
        return new
    if operation != "set":
        raise ChangeRejected("invalid_operation", f"'{field.label}' can only be set to a new value.")
    if field.value_type == "text":
        if not isinstance(operand, str) or len(operand) > 500:
            raise ChangeRejected("invalid_operand", f"'{field.label}' needs text of at most 500 characters.")
        if operand.lstrip().startswith(FORMULA_PREFIXES):
            raise ChangeRejected("formula_injection", "Text values may not start with =, +, - or @.")
        return operand
    if field.value_type == "boolean":
        if not isinstance(operand, bool):
            raise ChangeRejected("invalid_operand", f"'{field.label}' needs true or false.")
        return operand
    raise ChangeRejected("unsupported_type", f"Changing {field.value_type} values is not supported yet.")


def propose(session: Session, ctx: AppContext, user: User, wb: Workbook, *, changes: list[dict[str, Any]],
            name: str, interpreted_request: str, assumptions: list[str], base_version_id: str | None,
            mapping_version_id: str | None, revision_of_id: str | None, conversation_id: str | None) -> Scenario:
    with ctx.workbook_lock(wb.id):
        if wb.compatibility_status == "pending":
            raise ChangeRejected("compatibility_pending", "The workbook is still being checked for compatibility. "
                                                          "Please try again in a moment.")
        if wb.compatibility_status != "editable":
            raise ChangeRejected("editing_blocked", "Editing is not enabled for this workbook; see the "
                                                    "compatibility review.")
        if not changes:
            raise ChangeRejected("no_changes", "A scenario needs at least one input change.")
        if len(changes) > 25:
            raise ChangeRejected("too_many_changes", "A scenario can change at most 25 inputs.")
        mv, wmap = wbs.active_map(session, wb)
        if mapping_version_id is not None and mapping_version_id != mv.id:
            raise ChangeRejected("stale_mapping", "The workbook map changed; re-read the workbook summary.")
        revised = None
        if revision_of_id:
            revised = session.get(Scenario, revision_of_id)
            if revised is None or revised.workbook_id != wb.id:
                raise ChangeRejected("unknown_scenario", "The scenario being revised does not exist.")
            if revised.status == "applied":
                raise ChangeRejected("already_applied", "That scenario was already applied; start a new one from "
                                                        "the active version instead.")
            base_id = revised.base_version_id  # revisions never compound on the unapplied preview
            if base_version_id and base_version_id != base_id:
                raise ChangeRejected("revision_base_mismatch",
                                     "A revision must use the same starting version as the scenario it replaces.")
        else:
            base_id = base_version_id or wb.active_version_id
        base = wbs.get_version(session, wb, base_id)
        base_values = wbs.version_results(session, ctx, base, wmap)["fields"]

        seen: set[str] = set()
        resolved = []
        for change in changes:
            fid = change["field_id"]
            if fid in seen:
                raise ChangeRejected("duplicate_field", f"'{fid}' appears more than once; combine the changes.")
            seen.add(fid)
            field = wmap.field(fid)
            new = resolve_change(field, change["operation"], change["operand"], base_values.get(fid), fid)
            resolved.append((field, change, base_values.get(fid), new))

        scenario = Scenario(id=new_id("scn"), workbook_id=wb.id, conversation_id=conversation_id,
                            name=(name or "Scenario").strip()[:120], status="draft", base_version_id=base.id,
                            mapping_version_id=mv.id, revision_of_id=revised.id if revised else None,
                            interpreted_request=interpreted_request[:1000],
                            assumptions=[a[:300] for a in assumptions[:10]])
        session.add(scenario)
        for field, change, old, new in resolved:
            session.add(ScenarioChange(scenario_id=scenario.id, field_id=field.id, operation=change["operation"],
                                       operand=change["operand"], rationale=str(change.get("rationale", ""))[:500],
                                       sheet=field.sheet, address=field.address, old_value=old, new_value=new))
        if revised is not None and revised.status in ("draft", "needs_clarification", "validating",
                                                      "calculating", "ready", "failed"):
            transition(revised, "superseded", f"revised_by:{scenario.id}")
        wbs.audit(session, "scenario_proposed", workbook_id=wb.id, user_id=user.id, scenario_id=scenario.id,
                  base_version_id=base.id, revision_of=revision_of_id)
        session.flush()
        return scenario


def run(session: Session, ctx: AppContext, scenario: Scenario) -> Job:
    with ctx.workbook_lock(scenario.workbook_id):
        if scenario.status != "draft":
            raise Conflict("not_runnable", f"A scenario that is {scenario.status} cannot be calculated again.")
        transition(scenario, "validating")
        wb = session.get(Workbook, scenario.workbook_id)
        base = wbs.get_version(session, wb, scenario.base_version_id)
        changes = session.scalars(select(ScenarioChange).where(ScenarioChange.scenario_id == scenario.id)).all()
        writes = [CellWrite(c.sheet, c.address, c.new_value) for c in changes]
        try:
            candidate_input = ctx.engine.apply_inputs(ctx.store.resolve(base.file_path).read_bytes(), writes)
        except AppError as exc:
            transition(scenario, "failed")
            scenario.error = {"code": exc.code, "message": exc.message}
            return None
        job = Job(id=new_id("job"), kind="recalc", purpose="scenario", workbook_id=wb.id, scenario_id=scenario.id,
                  status="queued", attempts=1)
        session.add(job)
        scenario.job_id = job.id
        transition(scenario, "calculating")
        session.flush()
        ctx.engine.submit_recalculation(job.id, candidate_input)
        return job


def finalize(session: Session, ctx: AppContext, job: Job, outcome) -> None:
    scenario = session.get(Scenario, job.scenario_id)
    if scenario is None or scenario.status != "calculating":
        return  # superseded or deleted while calculating: result is ignored
    if not outcome.ok:
        transition(scenario, "failed")
        scenario.error = {"code": outcome.error_code, "message": outcome.error_message,
                          "diagnostics": outcome.diagnostics or {}}
        return
    wb = session.get(Workbook, scenario.workbook_id)
    base = wbs.get_version(session, wb, scenario.base_version_id)
    changes = session.scalars(select(ScenarioChange).where(ScenarioChange.scenario_id == scenario.id)).all()
    base_insp = ctx.engine.inspect(ctx.store.resolve(base.values_file_path).read_bytes())
    out_insp = ctx.engine.inspect(outcome.data)
    expected = {(c.sheet, c.address): c.new_value for c in changes}
    checks = compare.compare_structure(base_insp, out_insp)
    checks += [compare.compare_formulas(base_insp, out_insp),
               compare.compare_inputs(base_insp, out_insp, expected, ctx.tolerance),
               compare.compare_errors(base_insp, out_insp)]
    validations = [c.as_dict() for c in checks]
    rel = ctx.store.save_candidate(wb.id, scenario.id, outcome.data)
    scenario.candidate_file_path = rel
    scenario.candidate_sha256 = sha256_bytes(outcome.data)
    wmap = WorkbookMap.model_validate(session.get(MappingVersion, scenario.mapping_version_id).content)
    before = wbs.results_snapshot(ctx, wmap, ctx.store.resolve(base.values_file_path).read_bytes())
    after = wbs.results_snapshot(ctx, wmap, outcome.data)
    scenario.comparison = compare_results(wmap, before, after, changes)
    scenario.validations = validations
    passed = all(v["passed"] for v in validations)
    session.add(Validation(workbook_id=wb.id, scenario_id=scenario.id, kind="scenario", passed=passed,
                           details={"checks": validations}))
    if passed:
        transition(scenario, "ready")
    else:
        transition(scenario, "failed", "validation_failed")
        scenario.error = {"code": "validation_failed",
                          "message": "The calculated scenario failed checks, so it cannot be applied.",
                          "failed_checks": [v["message"] for v in validations if not v["passed"]]}


def _delta(before: Any, after: Any, value_type: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"before": before, "after": after}
    if _is_number(before) and _is_number(after):
        entry["delta"] = after - before
        entry["changed"] = after != before
        if value_type == "percent":
            entry["percentage_point_change"] = (after - before) * 100
        if before == 0:
            entry["percent_change"] = None
            entry["zero_baseline"] = True
        else:
            entry["percent_change"] = (after - before) / abs(before)
        entry["sign_change"] = ("became_negative" if before >= 0 > after else
                                "became_non_negative" if before < 0 <= after else None)
    else:
        entry["changed"] = before != after
    return entry


def compare_results(wmap: WorkbookMap, before: dict, after: dict, changes: list | None = None) -> dict[str, Any]:
    """Structured before/after comparison under one map. Percent change is relative; for percent-type
    values the absolute difference is also given in percentage points. Zero baselines have no percent change."""
    inputs = []
    for c in changes or []:
        f = wmap.field(c.field_id)
        inputs.append({"field_id": c.field_id, "label": f.label if f else c.field_id,
                       "unit": f.unit if f else "", "value_type": f.value_type if f else "number",
                       "operation": c.operation, "operand": c.operand, "rationale": c.rationale,
                       "source": {"sheet": c.sheet, "address": c.address},
                       **_delta(c.old_value, c.new_value, f.value_type if f else "number")})
    outputs = []
    for f in wmap.fields:
        if f.role != "output":
            continue
        outputs.append({"field_id": f.id, "label": f.label, "unit": f.unit, "value_type": f.value_type,
                        "source": {"sheet": f.sheet, "address": f.address},
                        **_delta(before["fields"].get(f.id), after["fields"].get(f.id), f.value_type)})
    tables = []
    for t in wmap.output_tables:
        b, a = before["tables"].get(t.id, {}), after["tables"].get(t.id, {})
        rows, highlights = [], []
        for rb, ra in zip(b.get("rows", []), a.get("rows", []), strict=False):
            cells = {c.id: _delta(rb["values"].get(c.id), ra["values"].get(c.id), c.value_type) for c in t.columns}
            rows.append({"label": ra["label"], "row": ra["row"], "cells": cells})
            for c in t.columns:
                change = cells[c.id].get("sign_change")
                if change:
                    highlights.append({"row_label": ra["label"], "column_id": c.id, "column_label": c.label,
                                       "change": change, "before": cells[c.id]["before"],
                                       "after": cells[c.id]["after"]})
        total = None
        if b.get("total") and a.get("total"):
            total = {c.id: _delta(b["total"].get(c.id), a["total"].get(c.id), c.value_type) for c in t.columns}
        tables.append({"table_id": t.id, "label": t.label, "sheet": t.sheet,
                       "columns": [c.model_dump() for c in t.columns], "rows": rows, "total": total,
                       "sign_changes": highlights})
    return {"inputs": inputs, "outputs": outputs, "tables": tables}


def get_scenario(session: Session, wb: Workbook, scenario_id: str) -> Scenario:
    scenario = session.get(Scenario, scenario_id)
    if scenario is None or scenario.workbook_id != wb.id:
        raise NotFound("scenario")
    return scenario


def apply(session: Session, ctx: AppContext, user: User, wb: Workbook, scenario: Scenario, *,
          expected_active_version_id: str, idempotency_key: str) -> tuple[int, dict[str, Any]]:
    if not idempotency_key or len(idempotency_key) > 200:
        raise AppError("idempotency_key_required", "An Idempotency-Key header is required.", status_code=400)
    with ctx.workbook_lock(wb.id):
        prior = session.get(IdempotencyRecord, idempotency_key)
        if prior is not None:
            if prior.user_id != user.id or prior.endpoint != f"apply:{scenario.id}":
                raise Conflict("idempotency_key_reused", "This idempotency key was used for a different request.")
            return prior.status_code, prior.response
        if scenario.status == "applied":
            raise Conflict("already_applied", "This scenario has already been applied.")
        if scenario.status != "ready":
            raise Conflict("not_ready", "Only a successfully calculated, validated scenario can be applied.")
        if wb.active_version_id != expected_active_version_id or scenario.base_version_id != wb.active_version_id:
            raise Conflict("stale_preview", "The workbook changed since this preview was calculated. "
                                            "Recalculate the scenario from the current version.")
        if scenario.mapping_version_id != wb.active_mapping_version_id:
            raise Conflict("stale_preview", "The workbook map changed since this preview was calculated.")
        data = ctx.store.resolve(scenario.candidate_file_path).read_bytes()
        if sha256_bytes(data) != scenario.candidate_sha256:
            raise AppError("artifact_corrupted", "The calculated preview file changed unexpectedly.",
                           status_code=500)
        seq = session.scalar(select(func.max(WorkbookVersion.seq)).where(WorkbookVersion.workbook_id == wb.id)) + 1
        version_id = new_id("ver")
        rel = ctx.store.save_version(wb.id, version_id, data)
        version = WorkbookVersion(id=version_id, workbook_id=wb.id, seq=seq, parent_version_id=scenario.base_version_id,
                                  kind="scenario", label=f"Applied: {scenario.name}"[:200], file_path=rel,
                                  values_file_path=rel, values_source="recalculated", sha256=scenario.candidate_sha256,
                                  size_bytes=len(data), mapping_version_id=scenario.mapping_version_id,
                                  scenario_id=scenario.id)
        session.add(version)
        wmap = WorkbookMap.model_validate(session.get(MappingVersion, scenario.mapping_version_id).content)
        version.results = wbs.results_snapshot(ctx, wmap, data)
        wb.active_version_id = version.id
        transition(scenario, "applied")
        scenario.applied_version_id = version.id
        wbs.audit(session, "scenario_applied", workbook_id=wb.id, user_id=user.id, scenario_id=scenario.id,
                  version_id=version.id, previous_version_id=scenario.base_version_id)
        response = {"scenario_id": scenario.id, "version": wbs.version_dict(version)}
        session.add(IdempotencyRecord(key=idempotency_key, user_id=user.id, endpoint=f"apply:{scenario.id}",
                                      status_code=200, response=response))
        session.flush()
        return 200, response


def discard(session: Session, ctx: AppContext, user: User, scenario: Scenario) -> None:
    with ctx.workbook_lock(scenario.workbook_id):
        if scenario.status in ("draft", "needs_clarification"):
            transition(scenario, "superseded", "discarded_before_calculation")
        else:
            transition(scenario, "discarded")
        wbs.audit(session, "scenario_discarded", workbook_id=scenario.workbook_id, user_id=user.id,
                  scenario_id=scenario.id)


def compare_versions(session: Session, ctx: AppContext, wb: Workbook, version_a: str, version_b: str) -> dict:
    """Compare two versions under the active map (explicitly labelled common base = version_a)."""
    _, wmap = wbs.active_map(session, wb)
    a, b = wbs.get_version(session, wb, version_a), wbs.get_version(session, wb, version_b)
    comparison = compare_results(wmap, wbs.version_results(session, ctx, a, wmap),
                                 wbs.version_results(session, ctx, b, wmap))
    return {"base_version": wbs.version_dict(a), "compared_version": wbs.version_dict(b), **comparison}


def scenario_dict(session: Session, wb: Workbook, scenario: Scenario) -> dict[str, Any]:
    changes = session.scalars(select(ScenarioChange).where(ScenarioChange.scenario_id == scenario.id)).all()
    base = session.get(WorkbookVersion, scenario.base_version_id)
    stale = (scenario.base_version_id != wb.active_version_id
             or scenario.mapping_version_id != wb.active_mapping_version_id)
    return {
        "id": scenario.id, "name": scenario.name, "status": scenario.status, "status_reason": scenario.status_reason,
        "base_version": wbs.version_dict(base), "mapping_version_id": scenario.mapping_version_id,
        "revision_of_id": scenario.revision_of_id, "interpreted_request": scenario.interpreted_request,
        "assumptions": scenario.assumptions or [],
        "changes": [{"field_id": c.field_id, "operation": c.operation, "operand": c.operand,
                     "rationale": c.rationale, "old_value": c.old_value, "new_value": c.new_value,
                     "source": {"sheet": c.sheet, "address": c.address}} for c in changes],
        "comparison": scenario.comparison, "validations": scenario.validations, "error": scenario.error,
        "job_id": scenario.job_id, "applied_version_id": scenario.applied_version_id,
        "stale": stale and scenario.status == "ready",
        "apply_allowed": scenario.status == "ready" and not stale,
        "created_at": scenario.created_at.isoformat(), "updated_at": scenario.updated_at.isoformat(),
    }
