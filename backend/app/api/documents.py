from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, OwnedProject, ReadyProject
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.security import rate_limiter
from app.db import models as m
from app.services import docs as service

router = APIRouter(prefix="/projects/{project_id}/docs", tags=["documentation"])


class GenerateDocsRequest(BaseModel):
    use_ai: bool = True


def _payload(document: m.Document, *, content: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(document.id),
        "title": document.title,
        "status": document.status,
        "commit": document.commit,
        "ai_enhanced": document.ai_enhanced,
        "provider": document.provider,
        "model": document.model,
        "error": document.error,
        "created_at": document.created_at,
    }
    if content:
        data["content"] = document.content
    return data


def _owned(db: DbSession, project: m.Project, document_id: uuid.UUID) -> m.Document:
    document = db.get(m.Document, document_id)
    if document is None or document.project_id != project.id:
        raise NotFoundError("Document not found.")
    return document


@router.post("", status_code=202)
def generate(
    project: ReadyProject, user: CurrentUser, db: DbSession, body: GenerateDocsRequest | None = None
) -> dict[str, Any]:
    use_ai = body.use_ai if body else True
    if use_ai:
        rate_limiter.check(f"ai:{user.id}", get_settings().rate_limit_ai_per_minute, 60, "AI")
    return _payload(service.start_generation(db, project, user, use_ai))


@router.get("")
def list_documents(project: OwnedProject, db: DbSession) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(m.Document).where(m.Document.project_id == project.id).order_by(m.Document.created_at.desc()).limit(50)
    ).all()
    return [_payload(d, content=False) for d in rows]


@router.get("/{document_id}")
def get_document(document_id: uuid.UUID, project: OwnedProject, db: DbSession) -> dict[str, Any]:
    return _payload(_owned(db, project, document_id))


@router.get("/{document_id}/download")
def download(document_id: uuid.UUID, project: OwnedProject, db: DbSession) -> PlainTextResponse:
    document = _owned(db, project, document_id)
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", project.name).strip("-") or "documentation"
    return PlainTextResponse(
        document.content or "",
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}-docs.md"'},
    )


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, project: OwnedProject, db: DbSession) -> None:
    db.delete(_owned(db, project, document_id))
    db.commit()
