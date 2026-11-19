from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, OwnedProject, ReadyProject
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.security import rate_limiter
from app.db import models as m
from app.services import testgen
from app.services.projects import repo_path
from app.services.sandbox.runner import SandboxUnavailable

router = APIRouter(prefix="/projects/{project_id}", tags=["regression tests"])


class GenerateRequest(BaseModel):
    target_path: str = Field(min_length=1, max_length=1024)
    symbol: str | None = Field(default=None, max_length=1024)
    use_ai: bool = True


class UpdateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=200_000)


class RunRequest(BaseModel):
    # Off by default. Turning it on gives the install phase network access.
    install_dependencies: bool = False


def _test_payload(test: m.GeneratedTest, notice: str | None = None) -> dict[str, Any]:
    return {
        "id": str(test.id),
        "target_path": test.target_path,
        "target_symbol": test.target_symbol,
        "language": test.language,
        "framework": test.framework,
        "test_path": test.test_path,
        "code": test.code,
        "source": test.source,
        "provider": test.provider,
        "model": test.model,
        "notice": notice,
        "created_at": test.created_at,
    }


def _run_payload(run: m.TestRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "test_id": str(run.test_id),
        "status": run.status,
        "install_dependencies": run.install_dependencies,
        "exit_code": run.exit_code,
        "output": run.output,
        "results": run.results,
        "duration_seconds": run.duration_seconds,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }


def _owned_test(db: DbSession, project: m.Project, test_id: uuid.UUID) -> m.GeneratedTest:
    test = db.get(m.GeneratedTest, test_id)
    if test is None or test.project_id != project.id:
        raise NotFoundError("Test not found.")
    return test


@router.get("/sandbox-status")
def sandbox_status(project: OwnedProject) -> dict[str, Any]:
    try:
        testgen.sandbox().check()
    except SandboxUnavailable as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, "reason": None}


@router.post("/tests/generate", status_code=201)
def generate(body: GenerateRequest, project: ReadyProject, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    settings = get_settings()
    if body.use_ai:
        rate_limiter.check(f"ai:{user.id}", settings.rate_limit_ai_per_minute, 60, "AI")
    repo = repo_path(settings, project.id)
    test, notice = testgen.generate_test(db, project, user, repo, body.target_path, body.symbol, body.use_ai)
    return _test_payload(test, notice)


@router.get("/tests")
def list_tests(project: OwnedProject, db: DbSession) -> list[dict[str, Any]]:
    tests = db.scalars(
        select(m.GeneratedTest)
        .where(m.GeneratedTest.project_id == project.id)
        .order_by(m.GeneratedTest.created_at.desc())
        .limit(200)
    ).all()
    payload: list[dict[str, Any]] = []
    for test in tests:
        latest = db.scalars(
            select(m.TestRun).where(m.TestRun.test_id == test.id).order_by(m.TestRun.created_at.desc()).limit(1)
        ).first()
        payload.append({**_test_payload(test), "latest_run": _run_payload(latest) if latest else None})
    return payload


@router.put("/tests/{test_id}")
def update_test(test_id: uuid.UUID, body: UpdateRequest, project: OwnedProject, db: DbSession) -> dict[str, Any]:
    test = _owned_test(db, project, test_id)
    test.code = body.code
    test.source = "edited"
    db.commit()
    return _test_payload(test)


@router.delete("/tests/{test_id}", status_code=204)
def delete_test(test_id: uuid.UUID, project: OwnedProject, db: DbSession) -> None:
    db.delete(_owned_test(db, project, test_id))
    db.commit()


@router.post("/tests/{test_id}/run", status_code=202)
def run_test(
    test_id: uuid.UUID, project: OwnedProject, user: CurrentUser, db: DbSession, body: RunRequest | None = None
) -> dict[str, Any]:
    test = _owned_test(db, project, test_id)
    rate_limiter.check(f"test-run:{user.id}", get_settings().rate_limit_test_runs_per_minute, 60, "test run")
    run = testgen.start_run(db, project, test, body.install_dependencies if body else False)
    return _run_payload(run)


@router.get("/tests/{test_id}/runs")
def list_runs(test_id: uuid.UUID, project: OwnedProject, db: DbSession) -> list[dict[str, Any]]:
    _owned_test(db, project, test_id)
    runs = db.scalars(
        select(m.TestRun).where(m.TestRun.test_id == test_id).order_by(m.TestRun.created_at.desc()).limit(30)
    ).all()
    return [_run_payload(r) for r in runs]


@router.get("/test-runs/{run_id}")
def get_run(run_id: uuid.UUID, project: OwnedProject, db: DbSession) -> dict[str, Any]:
    run = db.get(m.TestRun, run_id)
    if run is None:
        raise NotFoundError("Test run not found.")
    _owned_test(db, project, run.test_id)
    return _run_payload(run)
