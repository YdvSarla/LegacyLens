from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, OwnedProject, ReadyProject
from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationFailed
from app.core.security import rate_limiter
from app.db import models as m
from app.services import compare as service
from app.services import git
from app.services.projects import repo_path

router = APIRouter(prefix="/projects/{project_id}", tags=["compare"])


class CompareRequest(BaseModel):
    base: str = Field(min_length=1, max_length=200)
    head: str = Field(min_length=1, max_length=200)
    with_ai_summary: bool = False


def _payload(record: m.Comparison, ai_error: str | None = None) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "base_commit": record.base_commit,
        "head_commit": record.head_commit,
        "result": record.result,
        "ai_summary": record.ai_summary,
        "ai_error": ai_error,
        "provider": record.provider,
        "model": record.model,
        "created_at": record.created_at,
    }


@router.get("/commits")
def commits(project: OwnedProject) -> list[dict[str, str]]:
    repo = repo_path(get_settings(), project.id)
    if not repo.exists():
        return []
    try:
        return [c.__dict__ for c in git.list_commits(repo, limit=200)]
    except git.GitError as exc:
        raise ValidationFailed(f"Could not read the commit history: {exc}") from exc


@router.post("/compare")
def compare(body: CompareRequest, project: ReadyProject, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    if body.base.strip() == body.head.strip():
        raise ValidationFailed("Choose two different commits to compare.")
    repo = repo_path(get_settings(), project.id)
    try:
        result = service.compare_commits(db, project, repo, body.base.strip(), body.head.strip())
    except git.GitError as exc:
        raise ValidationFailed(f"Could not compare these commits: {exc}") from exc
    record = m.Comparison(project_id=project.id, base_commit=result["base"], head_commit=result["head"], result=result)
    ai_error = None
    if body.with_ai_summary:
        rate_limiter.check(f"ai:{user.id}", get_settings().rate_limit_ai_per_minute, 60, "AI")
        db.commit()  # Do not hold a database connection during a slow AI call.
        summary, provider, model, ai_error = service.ai_summary(user, project, result)
        record.ai_summary, record.provider, record.model = summary, provider, model
    db.add(record)
    db.commit()
    return _payload(record, ai_error)


@router.get("/comparisons")
def comparisons(project: OwnedProject, db: DbSession) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(m.Comparison)
        .where(m.Comparison.project_id == project.id)
        .order_by(m.Comparison.created_at.desc())
        .limit(30)
    ).all()
    return [
        {
            "id": str(r.id),
            "base_commit": r.base_commit,
            "head_commit": r.head_commit,
            "risk": (r.result or {}).get("risk"),
            "stats": (r.result or {}).get("stats"),
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/comparisons/{comparison_id}")
def comparison(comparison_id: uuid.UUID, project: OwnedProject, db: DbSession) -> dict[str, Any]:
    record = db.get(m.Comparison, comparison_id)
    if record is None or record.project_id != project.id:
        raise NotFoundError("Comparison not found.")
    return _payload(record)
