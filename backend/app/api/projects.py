from __future__ import annotations

import re
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, OwnedProject
from app.core.config import get_settings
from app.core.errors import TooLargeError, ValidationFailed
from app.db import models as m
from app.services import projects as service

router = APIRouter(prefix="/projects", tags=["projects"])


class GithubImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    branch: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=200)
    # Used for the clone and never stored. A later sync of a private repo needs it again.
    token: str | None = Field(default=None, max_length=500)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    local_ai_only: bool | None = None


class SyncRequest(BaseModel):
    token: str | None = Field(default=None, max_length=500)


def job_payload(job: m.Job | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }


def project_payload(project: m.Project, job: m.Job | None = None) -> dict[str, Any]:
    summary = project.summary or {}
    return {
        "id": str(project.id),
        "name": project.name,
        "source_type": project.source_type,
        "source_url": project.source_url,
        "branch": project.branch,
        "head_commit": project.head_commit,
        "status": project.status,
        "error": project.error,
        "local_ai_only": project.local_ai_only,
        "created_at": project.created_at,
        "analyzed_at": project.analyzed_at,
        "frameworks": summary.get("frameworks", []),
        "languages": summary.get("languages", []),
        "metrics": summary.get("metrics"),
        "score": (summary.get("quality") or {}).get("scores"),
        "search_index": summary.get("search_index"),
        "job": job_payload(job),
    }


def _latest_job(db: DbSession, project_id: uuid.UUID) -> m.Job | None:
    return db.scalars(
        select(m.Job)
        .where(m.Job.project_id == project_id, m.Job.kind.in_(("import", "analyze", "sync")))
        .order_by(m.Job.created_at.desc())
        .limit(1)
    ).first()


async def _save_upload(file: UploadFile) -> Path:
    """Streams an upload to disk, enforcing the size limit while reading."""
    settings = get_settings()
    filename = file.filename or ""
    if not filename.lower().endswith(".zip"):
        raise ValidationFailed("Please upload a .zip file.")
    limit = settings.max_upload_mb * 1024 * 1024
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=settings.uploads_dir, suffix=".zip", delete=False)
    path = Path(handle.name)
    written = 0
    try:
        with handle:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise TooLargeError(f"The upload exceeds the {settings.max_upload_mb} MB limit.")
                handle.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _name_from_filename(filename: str | None) -> str:
    stem = Path(filename or "project.zip").stem
    return re.sub(r"[^\w .-]", "", stem)[:200] or "Uploaded project"


@router.get("")
def list_projects(db: DbSession, user: CurrentUser) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(m.Project).where(m.Project.owner_id == user.id).order_by(m.Project.created_at.desc())
    ).all()
    return [project_payload(p, _latest_job(db, p.id)) for p in rows]


@router.post("/github", status_code=202)
def import_github(body: GithubImportRequest, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    project = service.start_github_import(db, user, body.url, body.branch or None, body.token or None, body.name)
    return project_payload(project, _latest_job(db, project.id))


@router.post("/upload", status_code=202)
async def upload_zip(
    db: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form(max_length=200)] = None,
) -> dict[str, Any]:
    service.ensure_can_create_project(db, user)
    archive = await _save_upload(file)
    project = service.start_zip_import(db, user, archive, name or _name_from_filename(file.filename))
    return project_payload(project, _latest_job(db, project.id))


@router.get("/{project_id}")
def get_project(project: OwnedProject, db: DbSession) -> dict[str, Any]:
    return project_payload(project, _latest_job(db, project.id))


@router.patch("/{project_id}")
def update_project(body: ProjectUpdate, project: OwnedProject, db: DbSession) -> dict[str, Any]:
    if body.name is not None:
        project.name = body.name.strip()
    if body.local_ai_only is not None:
        project.local_ai_only = body.local_ai_only
    db.commit()
    return project_payload(project, _latest_job(db, project.id))


@router.delete("/{project_id}", status_code=204)
def delete_project(project: OwnedProject, db: DbSession) -> None:
    service.delete_project(db, project)


@router.post("/{project_id}/reanalyze", status_code=202)
def reanalyze(project: OwnedProject, db: DbSession, body: SyncRequest | None = None) -> dict[str, Any]:
    service.start_reanalyze(db, project, body.token if body else None)
    db.refresh(project)
    return project_payload(project, _latest_job(db, project.id))


@router.post("/{project_id}/sync", status_code=202)
def sync(project: OwnedProject, db: DbSession, body: SyncRequest | None = None) -> dict[str, Any]:
    service.start_sync(db, project, body.token if body else None)
    db.refresh(project)
    return project_payload(project, _latest_job(db, project.id))


@router.post("/{project_id}/versions", status_code=202)
async def upload_version(
    project: OwnedProject,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    label: Annotated[str | None, Form(max_length=200)] = None,
) -> dict[str, Any]:
    archive = await _save_upload(file)
    service.start_new_zip_version(db, project, archive, label)
    db.refresh(project)
    return project_payload(project, _latest_job(db, project.id))
