"""Heuristic candidate mappings for an imported workbook.

Candidates come from text labels next to values, number formats, and single-cell
named ranges. Every candidate is unconfirmed, and inferred inputs are *not*
editable until a person reviews them: a constant cell is not assumed to be an
input just because it has no formula. Fill colours are not used.
"""

from __future__ import annotations

import re
from typing import Any

from openpyxl.utils.cell import column_index_from_string, coordinate_from_string, get_column_letter

MAX_CANDIDATES = 150
_UNIT_HINT = re.compile(r"^(usd|eur|gbp|\$|%|hours?|units?|days?|fraction|pct|percent|[a-z]{1,6}/[a-z]{1,8})", re.I)


def _slug(text: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:50] or "field"
    if not base[0].isalpha():
        base = f"f_{base}"
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}_{n}", n + 1
    used.add(candidate)
    return candidate


def _value_type(number_format: str | None, value: Any) -> str:
    fmt = (number_format or "").lower()
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "text"
    if "%" in fmt:
        return "percent"
    if any(sym in fmt for sym in ("$", "€", "£", "[$")):
        return "currency"
    return "number"


def infer_map(inspection: dict[str, Any]) -> dict[str, Any]:
    used: set[str] = set()
    fields: list[dict[str, Any]] = []
    hidden_sheets = {s["name"] for s in inspection["sheets"] if s.get("state") != "visible"}
    for sheet in inspection["sheets"]:
        name = sheet["name"]
        if name in hidden_sheets or sheet.get("chartsheet"):
            continue
        values = inspection["input_values"].get(name, {})
        formulas = inspection["formulas"].get(name, {})
        formats = inspection.get("number_formats", {}).get(name, {})
        for address, label in values.items():
            if len(fields) >= MAX_CANDIDATES:
                break
            if not isinstance(label, str) or not label.strip() or len(label) > 80:
                continue
            col, row = coordinate_from_string(address)
            target = f"{get_column_letter(column_index_from_string(col) + 1)}{row}"
            unit_cell = f"{get_column_letter(column_index_from_string(col) + 2)}{row}"
            unit = values.get(unit_cell)
            unit = unit if isinstance(unit, str) and _UNIT_HINT.match(unit.strip()) else ""
            if target in formulas:
                role, value = "output", inspection["cached_values"].get(name, {}).get(target)
            elif target in values and isinstance(values[target], (int, float)) and not isinstance(values[target], bool):
                role, value = "input", values[target]
            else:
                continue
            fields.append({
                "id": _slug(label, used), "label": label.strip()[:120],
                "description": f"Suggested from the label in {name}!{address}", "sheet": name, "address": target,
                "role": role, "value_type": _value_type(formats.get(target), value), "unit": unit[:40],
                "minimum": None, "maximum": None, "aliases": [], "editable": False, "confirmed": False,
                "source": "inferred",
            })
    mapped = {(f["sheet"], f["address"]) for f in fields}
    for dn in inspection["defined_names"]:
        if len(fields) >= MAX_CANDIDATES or not dn.get("simple_range") or ":" in dn["value"]:
            continue
        sheet, _, addr = dn["value"].rpartition("!")
        sheet, addr = sheet.strip("'"), addr.replace("$", "")
        if sheet in hidden_sheets or (sheet, addr) in mapped or sheet not in inspection["formulas"]:
            continue
        role = "output" if addr in inspection["formulas"].get(sheet, {}) else "input"
        fields.append({
            "id": _slug(dn["name"], used), "label": re.sub(r"(?<!^)(?=[A-Z])", " ", dn["name"]).replace("_", " "),
            "description": f"Suggested from the named range {dn['name']}", "sheet": sheet, "address": addr,
            "role": role, "value_type": "number", "unit": "", "minimum": None, "maximum": None, "aliases": [],
            "editable": False, "confirmed": False, "source": "inferred",
        })
    return {"schema_version": 1, "source": "inferred", "fields": fields, "output_tables": [],
            "output_field_ids": [f["id"] for f in fields if f["role"] == "output"], "business_rules": []}
