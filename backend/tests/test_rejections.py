"""Acceptance check 6 (backend rejections) and 11 (malicious packages, path traversal)."""

from __future__ import annotations

import io
import zipfile

import pytest
from openpyxl import Workbook
from pydantic import ValidationError

from app.demo import generate
from app.errors import AppError
from app.mapping.schema import FieldDef, WorkbookMap
from app.services.scenarios import ChangeRejected, resolve_change
from app.storage import Store
from app.workbook.compare import Tolerance
from app.workbook.engine import CellWrite, LibreOfficeEngine, WriteRejected
from app.workbook.package import PackageLimits, PackageRejected, validate_package
from tests.conftest import H, load_demo

LIMITS = PackageLimits(max_bytes=10 * 1024 * 1024, max_members=200, max_uncompressed_bytes=50 * 1024 * 1024,
                       max_member_ratio=200)


def _field(**kw) -> FieldDef:
    base = dict(id="wage", label="Wage", sheet="Inputs", address="B4", role="input", value_type="currency",
                unit="USD/hour", minimum=0, maximum=100, editable=True, confirmed=True)
    base.update(kw)
    return FieldDef(**base)


def _xlsx_bytes() -> bytes:
    wb = Workbook()
    wb.active["A1"] = 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------- change validation
def test_unknown_field_rejected():
    with pytest.raises(ChangeRejected) as e:
        resolve_change(None, "set", 1, 0, "nope")
    assert e.value.code == "unknown_field"


def test_output_field_cannot_be_written():
    with pytest.raises(ChangeRejected) as e:
        resolve_change(_field(role="output", editable=False), "set", 1, 0, "wage")
    assert e.value.code == "not_an_input"


def test_unconfirmed_or_non_editable_input_rejected():
    for field in (_field(confirmed=False, editable=False), _field(editable=False)):
        with pytest.raises(ChangeRejected) as e:
            resolve_change(field, "set", 1, 0, "wage")
        assert e.value.code == "field_not_editable"


@pytest.mark.parametrize("operand", ["12", True, None, float("nan"), float("inf")])
def test_invalid_numeric_operand_rejected(operand):
    with pytest.raises(ChangeRejected):
        resolve_change(_field(), "set", operand, 32.0, "wage")


@pytest.mark.parametrize("operation,operand,code", [("set", -1, "below_minimum"), ("multiply", 10, "above_maximum"),
                                                    ("add", 1000, "above_maximum")])
def test_out_of_range_rejected(operation, operand, code):
    with pytest.raises(ChangeRejected) as e:
        resolve_change(_field(), operation, operand, 32.0, "wage")
    assert e.value.code == code


def test_relative_and_absolute_operations():
    assert resolve_change(_field(), "multiply", 1.08, 32.0, "wage") == pytest.approx(34.56)
    assert resolve_change(_field(), "add", 2, 32.0, "wage") == 34.0
    rate = _field(value_type="percent", unit="fraction", minimum=0, maximum=1)
    assert resolve_change(rate, "add", 0.02, 0.20, "wage") == pytest.approx(0.22)  # +2 percentage points


def test_text_formula_injection_rejected():
    field = _field(value_type="text", unit="", minimum=None, maximum=None)
    for text in ("=HYPERLINK(\"http://x\")", "+1+1", "-2", "@SUM(A1)", "  =1"):
        with pytest.raises(ChangeRejected) as e:
            resolve_change(field, "set", text, "old", "wage")
        assert e.value.code == "formula_injection"
    assert resolve_change(field, "set", "Plain text", "old", "wage") == "Plain text"


def test_integer_type_enforced():
    field = _field(value_type="integer", unit="units", maximum=None)
    assert resolve_change(field, "set", 5.0, 1, "wage") == 5
    with pytest.raises(ChangeRejected):
        resolve_change(field, "set", 5.5, 1, "wage")


# ----------------------------------------------------------------- engine-level guards
def _engine(tmp_path) -> LibreOfficeEngine:
    return LibreOfficeEngine(jobs_dir=tmp_path, calc_timeout=5, max_cells=1000, inspection_timeout=10,
                             tolerance=Tolerance())


def test_engine_refuses_formula_cell_write_and_injection(tmp_path):
    wb, _ = generate.build_workbook()
    buf = io.BytesIO()
    wb.save(buf)
    engine = _engine(tmp_path)
    with pytest.raises(WriteRejected) as e:
        engine.apply_inputs(buf.getvalue(), [CellWrite("Products", "N4", 1.0)])
    assert e.value.code == "formula_cell"
    with pytest.raises(WriteRejected) as e:
        engine.apply_inputs(buf.getvalue(), [CellWrite("Inputs", "D4", "=1+1")])
    assert e.value.code == "formula_injection"
    with pytest.raises(WriteRejected):
        engine.apply_inputs(buf.getvalue(), [CellWrite("Nope", "A1", 1)])


def test_map_rejects_formula_cell_as_input_and_unconfirmed_editable(make_client):
    client = make_client()
    wb = load_demo(client)
    new_map = {k: v for k, v in wb["map"].items() if k not in ("workbook_id", "mapping_version_id",
                                                                 "source_version_id")}
    new_map["fields"].append({"id": "sneaky", "label": "Profit as input", "sheet": "Summary", "address": "B6",
                              "role": "input", "value_type": "currency", "editable": True, "confirmed": True})
    r = client.put(f"/api/workbooks/{wb['id']}/map", headers=H,
                   json={"expected_mapping_version_id": wb["mapping_version_id"], "map": new_map})
    assert r.status_code == 422 and r.json()["error"]["code"] == "mapping_invalid"
    new_map["fields"][-1].update(address="E4", sheet="Inputs", role="input", confirmed=False)
    r = client.put(f"/api/workbooks/{wb['id']}/map", headers=H,
                   json={"expected_mapping_version_id": wb["mapping_version_id"], "map": new_map})
    assert r.status_code == 422


def test_api_rejects_unknown_field_and_out_of_range(make_client):
    client = make_client()
    wb = load_demo(client)
    base = wb["active_version"]["id"]
    for change, code in [({"field_id": "total_profit", "operation": "set", "operand": 1}, "not_an_input"),
                         ({"field_id": "ghost", "operation": "set", "operand": 1}, "unknown_field"),
                         ({"field_id": "payroll_burden_rate", "operation": "set", "operand": 20}, "above_maximum"),
                         ({"field_id": "manufacturing_hourly_rate", "operation": "set", "operand": "high"},
                          "invalid_operand")]:
        r = client.post(f"/api/workbooks/{wb['id']}/scenarios", headers=H,
                        json={"base_version_id": base, "name": "bad", "changes": [change]})
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == code


# ----------------------------------------------------------------- package safety
def test_rejects_non_xlsx_extension_and_garbage():
    with pytest.raises(PackageRejected):
        validate_package(_xlsx_bytes(), "book.xlsm", LIMITS)
    with pytest.raises(PackageRejected) as e:
        validate_package(b"not a zip at all", "book.xlsx", LIMITS)
    assert e.value.code == "not_xlsx"


def test_rejects_encrypted_ole_container():
    ole = bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 512
    with pytest.raises(PackageRejected) as e:
        validate_package(ole, "secret.xlsx", LIMITS)
    assert e.value.code == "encrypted_or_legacy"


def test_rejects_macro_workbook_renamed_to_xlsx():
    src = zipfile.ZipFile(io.BytesIO(_xlsx_bytes()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        dst.writestr("xl/vbaProject.bin", b"\0" * 64)
    with pytest.raises(PackageRejected) as e:
        validate_package(out.getvalue(), "innocent.xlsx", LIMITS)
    assert e.value.code == "macros_not_supported"


def test_rejects_zip_bomb_and_path_traversal():
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "x")
        z.writestr("xl/huge.xml", b"\0" * (20 * 1024 * 1024))
    with pytest.raises(PackageRejected) as e:
        validate_package(bomb.getvalue(), "bomb.xlsx", LIMITS)
    assert e.value.code == "expansion_limit"
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("[Content_Types].xml", "x")
        z.writestr("../../etc/passwd", "root")
    with pytest.raises(PackageRejected) as e:
        validate_package(evil.getvalue(), "evil.xlsx", LIMITS)
    assert e.value.code == "unsafe_package_path"
    many = io.BytesIO()
    with zipfile.ZipFile(many, "w") as z:
        for i in range(LIMITS.max_members + 1):
            z.writestr(f"x/{i}.xml", "")
    with pytest.raises(PackageRejected) as e:
        validate_package(many.getvalue(), "many.xlsx", LIMITS)
    assert e.value.code == "too_many_parts"


def test_rejects_oversized_upload():
    with pytest.raises(PackageRejected) as e:
        validate_package(b"PK\x03\x04" + b"\0" * 2048, "big.xlsx", PackageLimits(1024, 10, 10_000, 10))
    assert e.value.code == "file_too_large"


def test_upload_endpoint_rejects_bad_files(make_client):
    client = make_client()
    r = client.post("/api/workbooks", headers=H, files={"file": ("x.xlsx", b"hello", "application/octet-stream")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_xlsx"
    assert "Traceback" not in r.text


def test_store_refuses_path_traversal(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ValueError):
        store.resolve("../outside.xlsx")
    with pytest.raises(ValueError):
        store.workbook_dir("../../etc")
    with pytest.raises(AppError):
        raise AppError("x", "y")


def test_csrf_header_required_for_state_changes(make_client):
    client = make_client()
    assert client.post("/api/workbooks/demo").status_code == 403
    assert client.post("/api/workbooks/demo", headers={**H, "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/workbooks").status_code == 200


def test_unmapped_workbook_map_model_rejects_extra_keys():
    with pytest.raises(ValidationError):
        WorkbookMap.model_validate({"fields": [], "unexpected": 1})
