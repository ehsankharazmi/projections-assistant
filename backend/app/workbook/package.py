"""Validation of an uploaded .xlsx package before any parser touches it.

Checks run on the raw bytes and the ZIP directory only. They enforce size,
member-count, expansion, and ratio limits independently, and reject encrypted
files, legacy binary formats, and macro-bearing packages regardless of the
file extension.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from app.errors import AppError

OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")  # encrypted OOXML and legacy .xls use OLE containers
ZIP_MAGIC = b"PK\x03\x04"
XLSX_MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
MACRO_CONTENT_TYPES = (
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
    "application/vnd.ms-excel.template.macroEnabled.main+xml",
    "application/vnd.ms-excel.addin.macroEnabled.main+xml",
    "application/vnd.ms-office.vbaProject",
)


@dataclass(frozen=True)
class PackageLimits:
    max_bytes: int
    max_members: int
    max_uncompressed_bytes: int
    max_member_ratio: float


class PackageRejected(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, status_code=422)


def validate_package(data: bytes, filename: str, limits: PackageLimits) -> None:
    if not filename.lower().endswith(".xlsx"):
        raise PackageRejected("unsupported_extension", "Only .xlsx workbooks are supported.")
    if len(data) == 0:
        raise PackageRejected("empty_file", "The uploaded file is empty.")
    if len(data) > limits.max_bytes:
        raise PackageRejected("file_too_large",
                              f"The file is larger than the {limits.max_bytes // (1024 * 1024)} MiB limit.")
    if data.startswith(OLE_MAGIC):
        raise PackageRejected("encrypted_or_legacy",
                              "This file is password-protected/encrypted or in a legacy Excel format. "
                              "Remove the password in Excel and save as .xlsx, then upload again.")
    if not data.startswith(ZIP_MAGIC):
        raise PackageRejected("not_xlsx", "The file is not a valid .xlsx workbook.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackageRejected("not_xlsx", "The file is not a valid .xlsx workbook.") from exc

    infos = archive.infolist()
    if len(infos) > limits.max_members:
        raise PackageRejected("too_many_parts", "The workbook package has too many internal parts.")
    total = 0
    names = set()
    for info in infos:
        name = info.filename
        if name.startswith("/") or "\\" in name or ".." in name.split("/") or ":" in name:
            raise PackageRejected("unsafe_package_path", "The workbook package contains an unsafe internal path.")
        if info.flag_bits & 0x1:
            raise PackageRejected("encrypted_or_legacy", "The workbook package contains encrypted parts.")
        total += info.file_size
        if total > limits.max_uncompressed_bytes:
            raise PackageRejected("expansion_limit", "The workbook expands beyond the allowed size.")
        if info.compress_size > 0 and info.file_size / info.compress_size > limits.max_member_ratio:
            raise PackageRejected("expansion_limit", "The workbook contains a suspiciously compressed part.")
        names.add(name.lower())

    if "[content_types].xml" not in names:
        raise PackageRejected("not_xlsx", "The file is not a valid .xlsx workbook.")
    content_types = archive.read("[Content_Types].xml").decode("utf-8", errors="replace")
    if any(ct in content_types for ct in MACRO_CONTENT_TYPES) or any(n.endswith("vbaproject.bin") for n in names):
        raise PackageRejected("macros_not_supported",
                              "Macro-enabled workbooks are not supported, even when renamed to .xlsx.")
    if XLSX_MAIN not in content_types:
        raise PackageRejected("not_xlsx", "The file is not a standard .xlsx workbook.")


def package_features(data: bytes) -> dict[str, bool]:
    """Detect package-level features that openpyxl does not expose reliably."""
    names = [n.lower() for n in zipfile.ZipFile(io.BytesIO(data)).namelist()]
    return {
        "external_links": any(n.startswith("xl/externallinks/") for n in names),
        "data_connections": any(n.endswith("xl/connections.xml") for n in names),
        "query_tables": any(n.startswith("xl/querytables/") for n in names),
        "pivot_tables": any(n.startswith("xl/pivottables/") or n.startswith("xl/pivotcache/") for n in names),
        "charts": any(n.startswith("xl/charts/") for n in names),
        "drawings": any(n.startswith("xl/drawings/") for n in names),
        "embedded_objects": any(n.startswith("xl/embeddings/") or n.startswith("xl/activex/") for n in names),
        "custom_xml": any(n.startswith("customxml/") for n in names),
        "slicers": any("slicer" in n for n in names),
        "power_query_model": any(n.startswith("xl/model/") for n in names),
    }
