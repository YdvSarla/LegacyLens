from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, OwnedProject, ReadyProject
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.security import rate_limiter
from app.db import models as m
from app.services import assistant, retrieval

router = APIRouter(prefix="/projects/{project_id}", tags=["assistant"])


class ChatRequest(BaseModel):
    message: Annotated[str, Field(min_length=1, max_length=8000)]
    conversation_id: uuid.UUID | None = None
    provider: Literal["gemini", "groq", "ollama"] | None = None


def _message_payload(message: m.Message) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "role": message.role,
        "content": message.content,
        "citations": message.citations or [],
        "sources": message.sources or [],
        "provider": message.provider,
        "model": message.model,
        "degraded": message.degraded,
        "created_at": message.created_at,
    }


@router.post("/chat")
def chat(body: ChatRequest, project: ReadyProject, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    rate_limiter.check(f"ai:{user.id}", get_settings().rate_limit_ai_per_minute, 60, "AI")
    answer = assistant.ask(
        db, project, user, body.message, conversation_id=body.conversation_id, provider=body.provider
    )
    return {"conversation_id": str(answer.conversation_id), "message": _message_payload(answer.message)}


@router.get("/search")
def search(project: ReadyProject, db: DbSession, q: str) -> list[dict[str, Any]]:
    """Code search that needs no AI provider."""
    return [
        {
            "path": c.path,
            "start_line": c.start_line,
            "end_line": c.end_line,
            "symbol": c.symbol,
            "score": c.score,
            "preview": c.content[:400],
        }
        for c in retrieval.search(db, project.id, q[:500], top_k=20)
    ]


@router.get("/conversations")
def conversations(project: OwnedProject, db: DbSession) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(m.Conversation)
        .where(m.Conversation.project_id == project.id)
        .order_by(m.Conversation.created_at.desc())
        .limit(100)
    ).all()
    return [{"id": str(c.id), "title": c.title, "created_at": c.created_at} for c in rows]


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: uuid.UUID, project: OwnedProject, db: DbSession) -> dict[str, Any]:
    record = db.get(m.Conversation, conversation_id)
    if record is None or record.project_id != project.id:
        raise NotFoundError("Conversation not found.")
    messages = db.scalars(
        select(m.Message).where(m.Message.conversation_id == record.id).order_by(m.Message.created_at)
    ).all()
    return {"id": str(record.id), "title": record.title, "messages": [_message_payload(x) for x in messages]}


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: uuid.UUID, project: OwnedProject, db: DbSession) -> None:
    record = db.get(m.Conversation, conversation_id)
    if record is None or record.project_id != project.id:
        raise NotFoundError("Conversation not found.")
    db.delete(record)
    db.commit()
