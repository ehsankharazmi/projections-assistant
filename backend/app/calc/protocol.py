"""Shared-directory job protocol between the backend and the calculation worker.

The worker runs without network access and never touches the database. Each job
is a directory `<jobs_dir>/<job_id>/`:

    input.xlsx      written by the backend
    request.json    written last (atomically); its presence means "ready"
    claim.json      created exclusively by the worker when it starts the job
    output.xlsx     written by the worker
    result.json     written last by the worker (atomically); its presence means "done"

All manifests are written to a temporary name and renamed into place, so a
reader never sees a partial file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.storage import atomic_write_bytes

_JOB_ID = re.compile(r"^job_[0-9a-f]{20}$")


@dataclass
class RecalcRequest:
    job_id: str
    timeout_seconds: float
    attempt: int = 1


@dataclass
class RecalcResult:
    job_id: str
    ok: bool
    error_code: str | None = None
    error_message: str | None = None
    duration_seconds: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)


def job_dir(jobs_dir: Path, job_id: str) -> Path:
    if not _JOB_ID.match(job_id):
        raise ValueError("invalid job id")
    return jobs_dir / job_id


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_bytes(path, json.dumps(payload).encode(), mode=0o664)


def submit(jobs_dir: Path, request: RecalcRequest, workbook: bytes) -> Path:
    directory = job_dir(jobs_dir, request.job_id)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o2775)
    atomic_write_bytes(directory / "input.xlsx", workbook, mode=0o664)
    write_json_atomic(directory / "request.json", asdict(request))
    return directory


def read_result(jobs_dir: Path, job_id: str) -> tuple[RecalcResult, bytes | None] | None:
    directory = job_dir(jobs_dir, job_id)
    result_path = directory / "result.json"
    if not result_path.exists():
        return None
    result = RecalcResult(**json.loads(result_path.read_text()))
    output = directory / "output.xlsx"
    data = output.read_bytes() if result.ok and output.exists() else None
    return result, data


def is_claimed(jobs_dir: Path, job_id: str) -> bool:
    return (job_dir(jobs_dir, job_id) / "claim.json").exists()


def remove_job(jobs_dir: Path, job_id: str) -> None:
    directory = job_dir(jobs_dir, job_id)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)


def pending_jobs(jobs_dir: Path) -> list[str]:
    """Job ids that are ready and not yet claimed, oldest first (worker side)."""
    if not jobs_dir.exists():
        return []
    ready = []
    for entry in jobs_dir.iterdir():
        if not _JOB_ID.match(entry.name):
            continue
        try:  # the backend may remove a finished job directory at any moment
            if (entry / "request.json").exists() and not (entry / "claim.json").exists() \
                    and not (entry / "result.json").exists():
                ready.append(((entry / "request.json").stat().st_mtime, entry.name))
        except FileNotFoundError:
            continue
    return [name for _, name in sorted(ready)]


def try_claim(jobs_dir: Path, job_id: str, worker_id: str) -> bool:
    path = job_dir(jobs_dir, job_id) / "claim.json"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        json.dump({"worker": worker_id}, f)
    return True
