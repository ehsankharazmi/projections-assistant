"""Backend side of calculation jobs: watches the shared jobs directory and finalizes results.

The worker only writes files; all database changes happen here. Jobs that are
never picked up (worker down) or never finish are failed after a deadline, so a
scenario cannot stay "calculating" forever. After a restart, unfinished jobs
are picked up again by the same loop.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections.abc import Callable

from sqlalchemy import select

from app.calc import protocol
from app.context import AppContext
from app.db import session_scope
from app.models import Job, utcnow
from app.services import scenarios as scenario_service
from app.services import workbooks as wbs
from app.workbook.engine import RecalcOutcome

log = logging.getLogger("jobs")

UNCLAIMED_GRACE_SECONDS = 60.0
FINISH_GRACE_SECONDS = 30.0


def job_dict(job: Job) -> dict:
    return {"id": job.id, "purpose": job.purpose, "status": job.status, "workbook_id": job.workbook_id,
            "scenario_id": job.scenario_id, "error": ({"code": job.error_code, "message": job.error_message}
                                                      if job.error_code else None),
            "created_at": job.created_at.isoformat(),
            "finished_at": job.finished_at.isoformat() if job.finished_at else None}


class JobMonitor:
    def __init__(self, ctx: AppContext, poll_seconds: float = 0.3):
        self.ctx = ctx
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self.on_finished: list[Callable[[str], None]] = []
        self._wake = threading.Event()

    def start(self) -> None:
        if self.ctx.settings.worker_mode == "inprocess":
            from app.calc.worker import run_forever
            self._worker_thread = threading.Thread(
                target=run_forever, args=(self.ctx.settings.jobs_dir,),
                kwargs={"soffice": self.ctx.settings.soffice_path, "poll_seconds": 0.2, "stop": self._stop},
                name="calc-worker", daemon=True)
            self._worker_thread.start()
        self._thread = threading.Thread(target=self._run, name="job-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for thread in (self._thread, self._worker_thread):
            if thread is not None:
                thread.join(timeout=10)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:  # keep the monitor alive; failures are logged
                log.exception("job monitor iteration failed")
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def poll_once(self) -> list[str]:
        finished: list[str] = []
        with session_scope() as session:
            jobs = session.scalars(select(Job).where(Job.status.in_(("queued", "running")))).all()
            job_ids = [j.id for j in jobs]
        for job_id in job_ids:
            if self._process(job_id):
                finished.append(job_id)
        for job_id in finished:
            for callback in self.on_finished:
                try:
                    callback(job_id)
                except Exception:
                    log.exception("job completion callback failed for %s", job_id)
        return finished

    def _process(self, job_id: str) -> bool:
        ctx = self.ctx
        with ctx.workbook_lock(self._workbook_of(job_id) or job_id):
            with session_scope() as session:
                job = session.get(Job, job_id)
                if job is None or job.status not in ("queued", "running"):
                    return False
                outcome = ctx.engine.collect_recalculation(job.id)
                claimed = protocol.is_claimed(ctx.settings.jobs_dir, job.id)
                if outcome is None:
                    if claimed and job.status == "queued":
                        job.status, job.started_at = "running", utcnow()
                    age = (utcnow() - _aware(job.created_at)).total_seconds()
                    if not claimed and age > UNCLAIMED_GRACE_SECONDS:
                        outcome = RecalcOutcome(False, None, "calculation_service_unavailable",
                                                "The calculation service is not responding. Your workbook is "
                                                "unchanged.")
                    elif age > UNCLAIMED_GRACE_SECONDS + ctx.settings.calc_timeout_seconds + FINISH_GRACE_SECONDS:
                        outcome = RecalcOutcome(False, None, "engine_timeout",
                                                "The calculation did not finish in time. Your workbook is unchanged.")
                    else:
                        return False
                job.status = "succeeded" if outcome.ok else "failed"
                job.error_code, job.error_message = outcome.error_code, outcome.error_message
                job.finished_at = utcnow()
                if job.purpose == "round_trip":
                    wbs.finalize_round_trip(session, ctx, job, outcome)
                elif job.purpose == "scenario":
                    scenario_service.finalize(session, ctx, job, outcome)
            protocol.remove_job(ctx.settings.jobs_dir, job_id)
            return True

    @staticmethod
    def _workbook_of(job_id: str) -> str | None:
        with session_scope() as session:
            job = session.get(Job, job_id)
            return job.workbook_id if job else None

    def wait_for(self, job_id: str, timeout: float) -> Job | None:
        """Block until a job is finalized or the timeout expires (used inside a conversation turn)."""
        deadline = utcnow() + dt.timedelta(seconds=timeout)
        while utcnow() < deadline:
            with session_scope() as session:
                job = session.get(Job, job_id)
                if job is None or job.status in ("succeeded", "failed"):
                    return job
            self._wake.set()
            threading.Event().wait(0.2)
        return None


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
