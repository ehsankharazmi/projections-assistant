"""Filesystem storage for immutable workbook artifacts.

Paths stored in the database are relative to `data_dir` and are always generated
by the server; nothing from the browser or the model is used as a path.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path

_SAFE_ID = re.compile(r"^[a-z]+_[0-9a-f]{20}$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()

    def _checked_id(self, value: str) -> str:
        if not _SAFE_ID.match(value):
            raise ValueError("invalid identifier")
        return value

    def resolve(self, relative: str) -> Path:
        path = (self.data_dir / relative).resolve()
        if not path.is_relative_to(self.data_dir):
            raise ValueError("path escapes data directory")
        return path

    def workbook_dir(self, workbook_id: str) -> Path:
        return self.data_dir / "workbooks" / self._checked_id(workbook_id)

    def save_original(self, workbook_id: str, data: bytes) -> str:
        rel = f"workbooks/{self._checked_id(workbook_id)}/original.xlsx"
        atomic_write_bytes(self.data_dir / rel, data, mode=0o440)
        return rel

    def save_version(self, workbook_id: str, version_id: str, data: bytes) -> str:
        rel = f"workbooks/{self._checked_id(workbook_id)}/versions/{self._checked_id(version_id)}.xlsx"
        target = self.data_dir / rel
        if target.exists():
            raise FileExistsError("versions are immutable")
        atomic_write_bytes(target, data, mode=0o440)
        return rel

    def save_candidate(self, workbook_id: str, scenario_id: str, data: bytes) -> str:
        rel = f"workbooks/{self._checked_id(workbook_id)}/candidates/{self._checked_id(scenario_id)}.xlsx"
        atomic_write_bytes(self.data_dir / rel, data)
        return rel

    def delete_workbook(self, workbook_id: str) -> None:
        directory = self.workbook_dir(workbook_id)
        if directory.exists():
            shutil.rmtree(directory)
