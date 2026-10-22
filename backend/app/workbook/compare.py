"""Deterministic comparisons between workbook states.

Used by the round-trip compatibility gate and by scenario validation. Numbers
are compared with absolute and relative tolerances; text, booleans, dates and
error values by type.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from openpyxl.formula.tokenizer import Token, Tokenizer

ERROR_VALUES = frozenset({"#DIV/0!", "#N/A", "#NAME?", "#NULL!", "#NUM!", "#REF!", "#VALUE!", "#SPILL!",
                          "#CALC!", "#GETTING_DATA", "Err:502", "Err:504", "Err:508", "Err:522", "Err:523"})
_QUOTED_SIMPLE_SHEET = re.compile(r"'([A-Za-z_][A-Za-z0-9_.]*)'!")


@dataclass(frozen=True)
class Tolerance:
    absolute: float = 1e-8
    relative: float = 1e-6


def is_error(value: Any) -> bool:
    return isinstance(value, str) and (value in ERROR_VALUES or value.startswith("Err:"))


def value_kind(value: Any) -> str:
    if value is None:
        return "empty"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict) and value.get("type") == "date":
        return "date"
    if is_error(value):
        return "error"
    return "text"


def values_equal(a: Any, b: Any, tol: Tolerance) -> bool:
    ka, kb = value_kind(a), value_kind(b)
    if ka != kb:
        return False
    if ka == "number":
        a, b = float(a), float(b)
        if math.isnan(a) or math.isnan(b):
            return False
        return abs(a - b) <= max(tol.absolute, tol.relative * max(abs(a), abs(b)))
    return a == b


def normalize_formula(formula: str) -> str:
    """Normalization rule covered by tests: ignore whitespace, letter case of functions and
    references, and redundant quotes around simple sheet names. String literals are kept as-is."""
    try:
        tokens = Tokenizer(formula if formula.startswith("=") else f"={formula}").items
    except Exception:
        return formula
    parts = []
    for token in tokens:
        if token.type == Token.WSPACE:
            continue
        value = token.value
        if token.type == Token.FUNC:
            value = value.upper()
            for prefix in ("_XLFN._XLWS.", "_XLFN.", "_XLWS."):
                if value.startswith(prefix):
                    value = value[len(prefix):]
        elif token.type == Token.OPERAND and token.subtype == Token.RANGE:
            value = _QUOTED_SIMPLE_SHEET.sub(r"\1!", value).upper()
        parts.append(value)
    return "=" + "".join(parts)


@dataclass
class Check:
    code: str
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "passed": self.passed, "message": self.message, "details": self.details}


def _cells(insp: dict[str, Any], key: str) -> dict[tuple[str, str], Any]:
    return {(sheet, addr): value for sheet, cells in insp.get(key, {}).items() for addr, value in cells.items()}


def compare_structure(base: dict[str, Any], out: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    base_sheets = [s["name"] for s in base["sheets"]]
    out_sheets = [s["name"] for s in out["sheets"]]
    checks.append(Check("sheets_preserved", base_sheets == out_sheets,
                        "Sheets and their order are unchanged." if base_sheets == out_sheets
                        else "The list or order of sheets changed.", {"before": base_sheets, "after": out_sheets}))
    base_states = {s["name"]: s.get("state") for s in base["sheets"]}
    out_states = {s["name"]: s.get("state") for s in out["sheets"]}
    checks.append(Check("sheet_visibility_preserved", base_states == out_states,
                        "Hidden/visible sheet states are unchanged." if base_states == out_states
                        else "A sheet's visibility changed.", {"before": base_states, "after": out_states}))
    base_names = {(n["name"], n["scope"]): _QUOTED_SIMPLE_SHEET.sub(r"\1!", n["value"]) for n in base["defined_names"]}
    out_names = {(n["name"], n["scope"]): _QUOTED_SIMPLE_SHEET.sub(r"\1!", n["value"]) for n in out["defined_names"]}
    missing = sorted(f"{k[0]} ({k[1]})" for k in base_names if k not in out_names)
    changed = sorted(f"{k[0]} ({k[1]})" for k in base_names if k in out_names and base_names[k] != out_names[k])
    checks.append(Check("names_preserved", not missing and not changed,
                        "Named ranges are unchanged." if not missing and not changed
                        else "Some named ranges were lost or changed.", {"missing": missing, "changed": changed}))
    base_tables = sorted((s["name"], t["name"], t["ref"]) for s in base["sheets"] for t in s.get("tables", []))
    out_tables = sorted((s["name"], t["name"], t["ref"]) for s in out["sheets"] for t in s.get("tables", []))
    checks.append(Check("tables_preserved", base_tables == out_tables,
                        "Tables are unchanged." if base_tables == out_tables else "Tables were lost or changed.",
                        {"before": base_tables, "after": out_tables}))
    base_fmt, out_fmt = _cells(base, "number_formats"), _cells(out, "number_formats")
    fmt_changes = sorted(f"{s}!{a}" for (s, a), f in base_fmt.items() if out_fmt.get((s, a)) != f)
    checks.append(Check("number_formats_preserved", not fmt_changes,
                        "Number formats are unchanged." if not fmt_changes else "Some number formats changed.",
                        {"changed_cells": fmt_changes[:50], "count": len(fmt_changes)}))
    return checks


def compare_formulas(base: dict[str, Any], out: dict[str, Any]) -> Check:
    base_f, out_f = _cells(base, "formulas"), _cells(out, "formulas")
    lost = sorted(f"{s}!{a}" for (s, a) in base_f if (s, a) not in out_f)
    added = sorted(f"{s}!{a}" for (s, a) in out_f if (s, a) not in base_f)
    normalized, changed = [], []
    for key, formula in base_f.items():
        if key not in out_f or out_f[key] == formula:
            continue
        entry = {"cell": f"{key[0]}!{key[1]}", "before": formula, "after": out_f[key]}
        (normalized if normalize_formula(formula) == normalize_formula(out_f[key]) else changed).append(entry)
    passed = not lost and not added and not changed
    if passed and normalized:
        message = "Formulas are preserved (some were rewritten in an equivalent form)."
    elif passed:
        message = "All formulas are preserved exactly."
    else:
        message = "Some formulas were lost, added, or changed in meaning."
    return Check("formulas_preserved", passed, message,
                 {"lost": lost[:50], "added": added[:50], "changed": changed[:50],
                  "normalized_equivalent": normalized[:50], "normalized_count": len(normalized)})


def compare_formula_values(reference: dict[tuple[str, str], Any], out: dict[str, Any], tol: Tolerance,
                           *, code: str, label: str) -> Check:
    """Compare recalculated formula results against reference values (e.g. Excel's cached results)."""
    out_cached = _cells(out, "cached_values")
    mismatches, compared = [], 0
    for key, expected in reference.items():
        if expected is None:
            continue
        compared += 1
        actual = out_cached.get(key)
        if not values_equal(expected, actual, tol):
            mismatches.append({"cell": f"{key[0]}!{key[1]}", "expected": expected, "actual": actual})
    passed = not mismatches and compared > 0
    message = (f"Recalculated results match {label} for {compared} formula cells." if passed else
               f"No {label} were available to compare." if compared == 0 else
               f"{len(mismatches)} of {compared} recalculated results differ from {label}.")
    return Check(code, passed, message, {"compared": compared, "mismatches": mismatches[:50],
                                         "mismatch_count": len(mismatches)})


def compare_errors(base: dict[str, Any], out: dict[str, Any]) -> Check:
    base_errors = {k for k, v in _cells(base, "cached_values").items() if is_error(v)}
    out_errors = {k: v for k, v in _cells(out, "cached_values").items() if is_error(v)}
    new = sorted(f"{s}!{a}" for (s, a) in out_errors if (s, a) not in base_errors)
    return Check("no_new_formula_errors", not new,
                 "No new formula errors." if not new else f"{len(new)} formulas now produce errors.",
                 {"new_errors": [{"cell": c, "error": out_errors[tuple(c.split("!", 1))]} for c in new[:50]],
                  "baseline_errors": sorted(f"{s}!{a}" for (s, a) in base_errors)[:50]})


def compare_inputs(base: dict[str, Any], out: dict[str, Any], expected_changes: dict[tuple[str, str], Any],
                   tol: Tolerance) -> Check:
    base_v, out_v = _cells(base, "input_values"), _cells(out, "input_values")
    unexpected, wrong = [], []
    for key in set(base_v) | set(out_v) | set(expected_changes):
        if key in expected_changes:
            if not values_equal(expected_changes[key], out_v.get(key), tol):
                wrong.append({"cell": f"{key[0]}!{key[1]}", "expected": expected_changes[key],
                              "actual": out_v.get(key)})
        elif not values_equal(base_v.get(key), out_v.get(key), tol):
            unexpected.append({"cell": f"{key[0]}!{key[1]}", "before": base_v.get(key), "after": out_v.get(key)})
    passed = not unexpected and not wrong
    return Check("inputs_as_intended", passed,
                 "Only the intended input cells changed." if passed else "Input cells changed unexpectedly.",
                 {"unexpected_changes": unexpected[:50], "incorrect_new_values": wrong[:50]})
