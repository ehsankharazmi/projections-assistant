"""Scripted demo mode: deterministic responses with no AI provider calls.

This is a labelled script, not AI. It recognizes a small set of phrasings
(see SUGGESTED_PROMPTS) and drives the same tools and services as the real
assistant, so the full upload -> clarify -> preview -> revise -> apply workflow
works without an API key. Every number it reports comes from the application's
computed results.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from app.ai import tools as T
from app.db import session_scope
from app.models import Conversation, Scenario
from app.services import scenarios as scenario_service
from app.services import workbooks as wbs

SUGGESTED_PROMPTS = [
    "Increase labor costs by 8%, keep prices unchanged, and tell me which products lose money.",
    "Increase manufacturing wages by 8%.",
    "Actually make that 5%.",
    "Which products lose money?",
    "Explain how the profit margin of Walnut Side Table is calculated.",
    "What are the inputs?",
]

DEMO_NOTE = "Demo mode: scripted responses, not AI."
_PCT = re.compile(r"(-?\d+(?:\.\d+)?)\s*(%|(?:percentage points?|percent|pct|points?|pts?)\b)")
_REVISION = re.compile(r"\b(make (that|it)|instead|actually|change (that|it) to|rather|try)\b")


def _fmt(value: Any, value_type: str = "number", unit: str = "") -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "n/a" if value is None else str(value)
    if value_type == "percent":
        return f"{value * 100:.1f}%"
    if value_type == "currency" or unit.upper().startswith("USD"):
        sign = "-" if value < 0 else ""
        text = f"{sign}${abs(value):,.2f}"
        return text + (f"/{unit.split('/', 1)[1]}" if "/" in unit and unit.split("/", 1)[1] != "year" else "")
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _payload(kind: str, *, scenario_id: str | None = None, clarification: dict | None = None,
             fields: list[str] | None = None) -> dict[str, Any]:
    return {"kind": kind, "scenario_id": scenario_id, "clarification": clarification,
            "referenced_field_ids": fields or [], "proposed_rule": None, "demo_note": DEMO_NOTE}


class DemoResponder:
    def respond(self, tc: T.ToolContext, text: str) -> tuple[str, dict[str, Any]]:
        lowered = text.lower()
        summary = T.get_workbook_summary(tc, T.GetSummaryIn())
        fields = {f["id"]: f for f in summary["fields"]}
        state = self._state(tc)
        pct_match = _PCT.search(lowered)
        pct = float(pct_match.group(1)) if pct_match else None
        points = bool(pct_match and pct_match.group(2).startswith(("point", "pt", "percentage point")))

        pending = state.get("pending")
        if pending and pending.get("type") == "labor_meaning":
            resolved = self._resolve_labor(lowered, fields)
            if resolved is not None:
                self._set_state(tc, {})
                return self._scenario(tc, summary, resolved, pending["pct"], pending["request"])
        if pct is not None and _REVISION.search(lowered):
            latest = self._latest_unapplied(tc)
            if latest is not None:
                return self._revise(tc, summary, latest, pct)
        if pct is not None and re.search(r"\blabou?r\b", lowered) and not re.search(r"wage|hourly|hours|burden", lowered):
            if "manufacturing_hourly_rate" in fields:
                self._set_state(tc, {"pending": {"type": "labor_meaning", "pct": pct, "request": text}})
                return ("Before I change anything: by \"labor costs\" do you mean hourly wages, hours worked, or the "
                        "payroll burden (benefits and payroll taxes)? And should it apply to manufacturing, "
                        "packaging, or both? Prices will stay unchanged either way.",
                        _payload("clarification", clarification={
                            "question": "Which labor cost should increase, and for which department?",
                            "options": ["Hourly wages only, for manufacturing", "Hourly wages, manufacturing and "
                                        "packaging", "Payroll burden rate", "Hours worked"]}))
        if pct is not None:
            targets = self._resolve_labor(lowered, fields) or self._match_inputs(lowered, fields)
            if targets:
                if points and all(fields[t]["value_type"] == "percent" for t in targets):
                    return self._scenario(tc, summary, targets, pct, text, add_points=True)
                return self._scenario(tc, summary, targets, pct, text)
        if re.search(r"lose money|losing money|unprofitable|loss[- ]making|negative profit", lowered):
            return self._losers(tc, summary)
        if re.search(r"\b(why|explain|how (is|are|does|do))\b", lowered):
            return self._explain(tc, summary, lowered)
        if re.search(r"\binputs?\b|what can i change|assumptions", lowered):
            return self._inputs(tc, summary)
        return ("I'm running in demo mode, which follows a fixed script rather than real AI. Try one of these: "
                + " | ".join(SUGGESTED_PROMPTS), _payload("answer"))

    # ---------------------------------------------------------------- state
    def _state(self, tc: T.ToolContext) -> dict[str, Any]:
        with session_scope() as session:
            return dict(session.get(Conversation, tc.conversation_id).state or {})

    def _set_state(self, tc: T.ToolContext, state: dict[str, Any]) -> None:
        with session_scope() as session:
            session.get(Conversation, tc.conversation_id).state = state

    def _latest_unapplied(self, tc: T.ToolContext) -> Scenario | None:
        with session_scope() as session:
            latest = session.scalars(select(Scenario).where(Scenario.conversation_id == tc.conversation_id)
                                     .order_by(Scenario.created_at.desc())).first()
            if latest is None or latest.status in ("applied", "discarded"):
                return None
            session.expunge(latest)
            return latest

    # ---------------------------------------------------------------- intents
    @staticmethod
    def _resolve_labor(lowered: str, fields: dict[str, dict]) -> list[str] | None:
        if not re.search(r"wage|hourly", lowered):
            return None
        both = re.search(r"\bboth\b|\ball\b|and packaging|packaging and", lowered)
        targets = []
        if "manufactur" in lowered or both:
            targets.append("manufacturing_hourly_rate")
        if "packag" in lowered or both:
            targets.append("packaging_hourly_rate")
        targets = [t for t in targets if t in fields]
        return targets or None

    @staticmethod
    def _match_inputs(lowered: str, fields: dict[str, dict]) -> list[str]:
        hits = []
        for f in fields.values():
            if not f["editable"]:
                continue
            names = [f["label"].lower()] + [a.lower() for a in f["aliases"]]
            if any(n and n in lowered for n in names):
                hits.append(f["id"])
        return hits[:3]

    def _scenario(self, tc: T.ToolContext, summary: dict, targets: list[str], pct: float, request: str, *,
                  add_points: bool = False, revision_of: str | None = None, base_version_id: str | None = None):
        fields = {f["id"]: f for f in summary["fields"]}
        operation, operand = ("add", pct / 100) if add_points else ("multiply", round(1 + pct / 100, 10))
        change_text = (f"by {pct:g} percentage points" if add_points else f"by {pct:g}%")
        labels = " and ".join(fields[t]["label"].lower() for t in targets)
        proposal = T.execute(tc, "propose_input_changes", {
            "base_version_id": base_version_id, "mapping_version_id": summary["mapping_version_id"],
            "revision_of_scenario_id": revision_of,
            "name": f"{labels.capitalize()} {'+' if pct >= 0 else ''}{pct:g}{' pts' if add_points else '%'}",
            "interpreted_request": f"Change {labels} {change_text}; all other inputs, including prices, unchanged.",
            "assumptions": ([f"{pct:g}% is a relative increase of the current value."] if not add_points else
                            [f"{pct:g} percentage points are added to the rate."])
                           + (["Starts from the same version as the scenario it replaces (not compounded)."]
                              if revision_of else []),
            "changes": [{"field_id": t, "operation": operation, "operand": operand,
                         "rationale": f"Requested change {change_text}"} for t in targets]})
        if proposal.is_error:
            return proposal.content["error"]["message"], _payload("error", fields=targets)
        run = T.execute(tc, "run_scenario", {"scenario_id": proposal.content["scenario_id"]})
        if run.pending_job_id:
            return ("I'm recalculating the workbook; the preview will appear when it's ready.",
                    _payload("scenario_pending", scenario_id=proposal.content["scenario_id"], fields=targets))
        result = run.content
        if run.is_error or result.get("status") != "ready":
            reason = (result.get("error") or {}).get("message") if isinstance(result.get("error"), dict) else None
            return (f"The scenario could not be completed: {reason or 'the calculation failed checks'}. Your "
                    "workbook is unchanged.", _payload("error", scenario_id=result.get("scenario_id"), fields=targets))
        return self._describe(result, request, revision_of is not None), _payload(
            "scenario_ready", scenario_id=result["scenario_id"], fields=targets)

    def _revise(self, tc: T.ToolContext, summary: dict, latest: Scenario, pct: float):
        with session_scope() as session:
            wb = wbs.get_owned_workbook(session, T._user(session, tc), tc.workbook_id)
            details = scenario_service.scenario_dict(session, wb, latest)
        targets = [c["field_id"] for c in details["changes"]]
        add_points = all(c["operation"] == "add" for c in details["changes"])
        return self._scenario(tc, summary, targets, pct, f"Revision of '{latest.name}'", add_points=add_points,
                              revision_of=latest.id)

    @staticmethod
    def _describe(result: dict, request: str, revised: bool) -> str:
        comparison = result["comparison"]
        parts = []
        for c in comparison["inputs"]:
            parts.append(f"{c['label']} goes from {_fmt(c['before'], c['value_type'], c['unit'])} to "
                         f"{_fmt(c['after'], c['value_type'], c['unit'])}.")
        profit = next((o for o in comparison["outputs"] if o["field_id"] == "total_profit"), None)
        if profit:
            parts.append(f"Total profit changes from {_fmt(profit['before'], 'currency')} to "
                         f"{_fmt(profit['after'], 'currency')} ({_fmt(profit['delta'], 'currency')}).")
        for table in comparison["tables"]:
            losers = [r["label"] for r in table["rows"]
                      if isinstance(r["cells"].get("profit", {}).get("after"), (int, float))
                      and r["cells"]["profit"]["after"] < 0]
            newly = [h["row_label"] for h in table["sign_changes"]
                     if h["column_id"] == "profit" and h["change"] == "became_negative"]
            if "profit" in {c["id"] for c in table["columns"]}:
                if losers:
                    parts.append("Products losing money in this scenario: " + ", ".join(losers)
                                 + (f" ({', '.join(newly)} {'is' if len(newly) == 1 else 'are'} newly unprofitable)."
                                    if newly else "."))
                else:
                    parts.append("No product loses money in this scenario.")
        prefix = ("This replaces the previous preview and starts from the same base version, so the changes are "
                  "not stacked. " if revised else "")
        return (prefix + " ".join(parts) + " Prices and all other inputs are unchanged. Review the preview, then "
                "choose Apply, Revise, or Discard.")

    def _losers(self, tc: T.ToolContext, summary: dict):
        version_id = summary["active_version"]["id"]
        tables = [t for t in summary["output_tables"] if any(c["id"] == "profit" for c in t["columns"])]
        if not tables:
            return "This workbook's map has no per-product profit table to check.", _payload("answer")
        table = T.execute(tc, "read_table", {"version_id": version_id, "table_id": tables[0]["id"]}).content
        losers = [(r["label"], r["values"]["profit"]) for r in table["rows"]
                  if isinstance(r["values"].get("profit"), (int, float)) and r["values"]["profit"] < 0]
        if not losers:
            lowest = min(table["rows"], key=lambda r: r["values"].get("profit") or 0)
            return (f"In the active version (version {summary['active_version']['seq']}), no product loses money. "
                    f"The lowest profit is {lowest['label']} at {_fmt(lowest['values']['profit'], 'currency')}.",
                    _payload("answer"))
        text = "; ".join(f"{name} ({_fmt(value, 'currency')})" for name, value in losers)
        return (f"In the active version (version {summary['active_version']['seq']}), these products lose money: "
                f"{text}.", _payload("answer"))

    def _explain(self, tc: T.ToolContext, summary: dict, lowered: str):
        version_id = summary["active_version"]["id"]
        target: dict[str, Any] | None = None
        for table in summary["output_tables"]:
            rows = T.execute(tc, "read_table", {"version_id": version_id, "table_id": table["id"]}).content["rows"]
            row = next((r["label"] for r in rows if str(r["label"]).lower() in lowered), None)
            column = next((c for c in sorted(table["columns"], key=lambda c: -len(c["label"]))
                           if c["label"].lower() in lowered or c["id"].replace("_", " ") in lowered), None)
            if row and column:
                target = {"field_id": None, "table_id": table["id"], "row_label": row, "column_id": column["id"]}
                break
        if target is None:
            field = next((f for f in sorted(summary["fields"], key=lambda f: -len(f["label"]))
                          if f["label"].lower() in lowered), None)
            if field is None:
                return ("Tell me which result to explain, for example \"Explain how the profit margin of Walnut Side "
                        "Table is calculated\" or \"Explain total profit\".", _payload("answer"))
            target = {"field_id": field["id"], "table_id": None, "row_label": None, "column_id": None}
        result = T.execute(tc, "explain_field", {"version_id": version_id, **target})
        if result.is_error:
            return result.content["error"]["message"], _payload("error")
        trace = result.content
        lines = [f"{trace['target']['label']} is {self._fmt_cell(trace['target'])} in version "
                 f"{trace['version_seq']}. It is calculated from:"]
        for node in trace["precedents"][:8]:
            if node.get("depth") != 1 or node.get("hidden"):
                continue
            lines.append(f"- {node['label']}: {self._fmt_cell(node)}")
        if not trace["complete"]:
            lines.append("This explanation is partial: " + " ".join(trace["incomplete_reasons"][:2]))
        lines.append("These are the workbook's own formulas and the stored results of this exact version.")
        return "\n".join(lines), _payload("answer", fields=[target["field_id"]] if target["field_id"] else [])

    @staticmethod
    def _fmt_cell(node: dict) -> str:
        value = node.get("value")
        label = (node.get("label") or "").lower()
        if node.get("value_type"):
            return _fmt(value, node["value_type"], node.get("unit", ""))
        if "margin" in label or "rate" in label:
            return _fmt(value, "percent") if isinstance(value, float) and abs(value) < 5 else _fmt(value)
        if any(word in label for word in ("revenue", "cost", "profit", "overhead", "price", "wage")):
            return _fmt(value, "currency")
        return _fmt(value)

    def _inputs(self, tc: T.ToolContext, summary: dict):
        editable = [f for f in summary["fields"] if f["role"] == "input" and f["editable"]]
        if not editable:
            return ("No inputs have been approved for editing yet. Review the workbook map first.", _payload("answer"))
        values = T.execute(tc, "read_fields", {"version_id": summary["active_version"]["id"],
                                               "field_ids": [f["id"] for f in editable]}).content["fields"]
        lines = ["These inputs can be changed in scenarios:"]
        lines += [f"- {v['label']}: {_fmt(v['value'], v['value_type'], v['unit'])}" for v in values]
        return "\n".join(lines), _payload("answer", fields=[f["id"] for f in editable])
