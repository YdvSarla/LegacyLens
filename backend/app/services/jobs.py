"""Small background job runner. Status is stored in the database for the UI to poll."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.errors import AppError
from app.db import models as m
from app.db.session import session_scope

logger = logging.getLogger("legacylens.jobs")

Progress = Callable[[int, str], None]
JobFunction = Callable[[Progress], None]

# Imports can run for many minutes, so they get their own workers. Otherwise one large
# repository would keep test runs and documentation waiting.
_LONG_RUNNING_KINDS = {"import", "analyze", "sync"}
_executors: dict[str, ThreadPoolExecutor] = {}
_run_inline = False


def set_inline(value: bool) -> None:
    """Test hook: runs jobs synchronously."""
    global _run_inline
    _run_inline = value


def _get_executor(kind: str) -> ThreadPoolExecutor:
    pool = "long" if kind in _LONG_RUNNING_KINDS else "short"
    if pool not in _executors:
        _executors[pool] = ThreadPoolExecutor(
            max_workers=get_settings().worker_threads, thread_name_prefix=f"legacylens-{pool}"
        )
    return _executors[pool]


def user_message(exc: Exception) -> str:
    """A message that is safe to show. Unexpected errors stay in the server log."""
    if isinstance(exc, AppError):
        return exc.message
    return "The job failed because of an internal error. Check the server log for details."


def _execute(job_id: uuid.UUID, function: JobFunction) -> None:
    with session_scope() as db:
        db.execute(update(m.Job).where(m.Job.id == job_id).values(status="running", started_at=datetime.now(UTC)))

    def progress(percent: int, message: str) -> None:
        with session_scope() as db:
            db.execute(update(m.Job).where(m.Job.id == job_id).values(progress=percent, message=message))

    try:
        function(progress)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s failed", job_id)
        with session_scope() as db:
            db.execute(
                update(m.Job)
                .where(m.Job.id == job_id)
                .values(status="failed", error=user_message(exc), finished_at=datetime.now(UTC))
            )
        return
    with session_scope() as db:
        db.execute(
            update(m.Job).where(m.Job.id == job_id).values(status="done", progress=100, finished_at=datetime.now(UTC))
        )


def submit(project_id: uuid.UUID, kind: str, function: JobFunction) -> uuid.UUID:
    with session_scope() as db:
        job = m.Job(project_id=project_id, kind=kind)
        db.add(job)
        db.flush()
        job_id = job.id
    if _run_inline:
        _execute(job_id, function)
    else:
        _get_executor(kind).submit(_execute, job_id, function)
    return job_id


def recover_interrupted() -> None:
    """Work that was running when the server stopped will not resume by itself."""
    message = "The server restarted while this job was running. Please start it again."
    now = datetime.now(UTC)
    with session_scope() as db:
        db.execute(
            update(m.Job)
            .where(m.Job.status.in_(("queued", "running")))
            .values(status="failed", error=message, finished_at=now)
        )
        db.execute(
            update(m.Project)
            .where(m.Project.status.in_(("pending", "importing", "analyzing")))
            .values(status="failed", error=message)
        )
        db.execute(
            update(m.TestRun)
            .where(m.TestRun.status.in_(("queued", "running")))
            .values(status="error", output=message, finished_at=now)
        )
        db.execute(
            update(m.Document)
            .where(m.Document.status.in_(("queued", "running")))
            .values(status="failed", error=message)
        )


def latest_job(project_id: uuid.UUID, kind: str | None = None) -> m.Job | None:
    with session_scope() as db:
        statement = select(m.Job).where(m.Job.project_id == project_id)
        if kind:
            statement = statement.where(m.Job.kind == kind)
        return db.scalars(statement.order_by(m.Job.created_at.desc()).limit(1)).first()
