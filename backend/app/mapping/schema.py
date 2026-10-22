"""Versioned workbook map: business meaning of cells and output tables.

Percentages are stored as fractions (0.08 = 8%). Display conversion happens in
the UI. A requested "% increase" of a value is relative (multiply); a change in
"percentage points" of a percent-type field is absolute (add).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.errors import AppError

ADDRESS = re.compile(r"^\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})$")
COLUMN = re.compile(r"^[A-Z]{1,3}$")
IDENT = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

ValueType = Literal["number", "currency", "percent", "integer", "text", "boolean", "date"]
NUMERIC_TYPES = frozenset({"number", "currency", "percent", "integer"})


class MappingInvalid(AppError):
    def __init__(self, problems: list[str]):
        super().__init__("mapping_invalid", "The workbook map has problems: " + "; ".join(problems[:10]),
                         status_code=422, details={"problems": problems})


class FieldDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=500)
    sheet: str = Field(min_length=1, max_length=100)
    address: str
    role: Literal["input", "output", "reference"]
    value_type: ValueType
    unit: str = Field("", max_length=40)
    minimum: float | None = None
    maximum: float | None = None
    aliases: list[str] = Field(default_factory=list, max_length=20)
    editable: bool = False
    confirmed: bool = False
    source: Literal["demo", "inferred", "ai", "reviewed"] = "reviewed"

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not IDENT.match(v):
            raise ValueError("field ids use lowercase letters, digits and underscores")
        return v

    @field_validator("address")
    @classmethod
    def _address(cls, v: str) -> str:
        m = ADDRESS.match(v.upper())
        if not m:
            raise ValueError("address must be a single cell such as B4")
        return f"{m.group(1)}{m.group(2)}"

    @model_validator(mode="after")
    def _consistency(self) -> "FieldDef":
        if self.editable and self.role != "input":
            raise ValueError("only input fields can be editable")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum is greater than maximum")
        return self


class TableColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str = Field(min_length=1, max_length=120)
    column: str
    unit: str = ""
    value_type: ValueType = "number"

    @field_validator("column")
    @classmethod
    def _column(cls, v: str) -> str:
        if not COLUMN.match(v.upper()):
            raise ValueError("column must be a letter reference such as G")
        return v.upper()


class OutputTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    description: str = ""
    sheet: str
    row_label_column: str
    first_row: int = Field(ge=1)
    last_row: int = Field(ge=1)
    total_row: int | None = None
    columns: list[TableColumn] = Field(min_length=1, max_length=40)
    confirmed: bool = False

    @model_validator(mode="after")
    def _rows(self) -> "OutputTable":
        if self.last_row < self.first_row or self.last_row - self.first_row > 500:
            raise ValueError("table rows must be a range of at most 500 rows")
        return self


class BusinessRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str = Field(max_length=600)
    confirmed: bool = True


class WorkbookMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    source: Literal["demo", "inferred", "reviewed"] = "reviewed"
    fields: list[FieldDef] = Field(default_factory=list, max_length=500)
    output_tables: list[OutputTable] = Field(default_factory=list, max_length=20)
    output_field_ids: list[str] = Field(default_factory=list)
    business_rules: list[BusinessRule] = Field(default_factory=list, max_length=50)

    def field(self, field_id: str) -> FieldDef | None:
        return next((f for f in self.fields if f.id == field_id), None)

    def table(self, table_id: str) -> OutputTable | None:
        return next((t for t in self.output_tables if t.id == table_id), None)


def validate_against_workbook(m: WorkbookMap, inspection: dict[str, Any]) -> list[str]:
    """Check a map against the inspected workbook. Returns a list of problems (empty if valid)."""
    problems: list[str] = []
    sheets = {s["name"]: s for s in inspection["sheets"]}
    formulas = inspection["formulas"]
    ids = [f.id for f in m.fields] + [t.id for t in m.output_tables]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        problems.append(f"duplicate ids: {', '.join(sorted(dupes))}")
    for f in m.fields:
        sheet = sheets.get(f.sheet)
        if sheet is None:
            problems.append(f"{f.id}: sheet '{f.sheet}' does not exist")
            continue
        is_formula = f.address in formulas.get(f.sheet, {})
        if f.role == "input" and is_formula:
            problems.append(f"{f.id}: {f.sheet}!{f.address} contains a formula, so it cannot be an input")
        if f.role == "output" and not is_formula:
            problems.append(f"{f.id}: {f.sheet}!{f.address} is not a formula, so it cannot be an output")
        if f.editable and sheet.get("protected"):
            problems.append(f"{f.id}: sheet '{f.sheet}' is protected, so its cells cannot be editable")
        if f.editable and sheet.get("state") != "visible":
            problems.append(f"{f.id}: sheet '{f.sheet}' is hidden, so its cells cannot be editable")
        if f.editable and not f.confirmed:
            problems.append(f"{f.id}: an editable input must be confirmed by a person")
    known = {f.id for f in m.fields}
    for fid in m.output_field_ids:
        if fid not in known:
            problems.append(f"output_field_ids refers to unknown field '{fid}'")
    for t in m.output_tables:
        if t.sheet not in sheets:
            problems.append(f"{t.id}: sheet '{t.sheet}' does not exist")
    return problems
