"""Process-wide application context (settings, storage, engine, locks)."""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field

from app.config import Settings
from app.storage import Store
from app.workbook.compare import Tolerance
from app.workbook.engine import LibreOfficeEngine


@dataclass
class AppContext:
    settings: Settings
    store: Store
    engine: LibreOfficeEngine
    tolerance: Tolerance
    _locks: dict[str, threading.RLock] = field(default_factory=lambda: defaultdict(threading.RLock))
    _locks_guard: threading.Lock = field(default_factory=threading.Lock)

    def workbook_lock(self, workbook_id: str) -> threading.RLock:
        """Serializes state-changing operations (scenario proposals, apply, restore, map edits) per workbook."""
        with self._locks_guard:
            return self._locks[workbook_id]


_context: AppContext | None = None


def build_context(settings: Settings) -> AppContext:
    tolerance = Tolerance(settings.abs_tolerance, settings.rel_tolerance)
    engine = LibreOfficeEngine(jobs_dir=settings.jobs_dir, calc_timeout=settings.calc_timeout_seconds,
                               max_cells=settings.max_populated_cells,
                               inspection_timeout=settings.inspection_timeout_seconds, tolerance=tolerance)
    return AppContext(settings=settings, store=Store(settings.data_dir), engine=engine, tolerance=tolerance)


def set_context(ctx: AppContext) -> None:
    global _context
    _context = ctx


def get_context() -> AppContext:
    if _context is None:
        raise RuntimeError("Application context not initialised")
    return _context
