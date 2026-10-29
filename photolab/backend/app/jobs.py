"""Tiny background job runner: work runs in threads, progress lives in the Job table."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlmodel import Session

from app.db import engine
from app.models import Job

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")
_cancel: set[int] = set()
_lock = threading.Lock()
# Live progress stays in memory: writing it to SQLite would contend with the job's own writes.
live: dict[int, dict] = {}


class JobContext:
    def __init__(self, job_id: int):
        self.id = job_id
        self._last = 0.0

    def progress(self, done: int, total: int | None = None, message: str | None = None, force=False):
        cur = live.setdefault(self.id, {})
        cur["done"] = done
        if total is not None:
            cur["total"] = total
        if message is not None:
            cur["message"] = message

    @property
    def cancelled(self) -> bool:
        with _lock:
            return self.id in _cancel


def start(kind: str, title: str, fn: Callable[[JobContext], str | None]) -> int:
    with Session(engine) as s:
        job = Job(kind=kind, title=title, status="queued")
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id
    assert job_id is not None

    def run():
        ctx = JobContext(job_id)
        _set(job_id, status="running")
        try:
            result = fn(ctx)
            cur = live.pop(job_id, {})
            _set(job_id, status="cancelled" if ctx.cancelled else "done", result=result,
                 finished_at=datetime.now(), done=cur.get("done", 0), total=cur.get("total", 0),
                 message=cur.get("message"))
        except Exception as e:
            traceback.print_exc()
            live.pop(job_id, None)
            _set(job_id, status="error", message=str(e)[:500], finished_at=datetime.now())

    _pool.submit(run)
    return job_id


def cancel(job_id: int) -> None:
    with _lock:
        _cancel.add(job_id)


def _set(job_id: int, **fields) -> None:
    with Session(engine) as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        s.add(job)
        s.commit()
