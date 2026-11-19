"""Project lifecycle: import, analyze, index, sync and delete."""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.core.security import rate_limiter
from app.db import models as m
from app.db.session import session_scope
from app.services import git, ingest, jobs, retrieval
from app.services.analysis.pipeline import run_analysis

logger = logging.getLogger("legacylens.projects")

BUSY_STATES = ("pending", "importing", "analyzing")


def repo_path(settings: Settings, project_id: uuid.UUID) -> Path:
    return settings.repos_dir / str(project_id)


def get_owned_project(db: Session, project_id: uuid.UUID, user: m.User) -> m.Project:
    """404 rather than 403, so other users' project ids cannot be probed."""
    project = db.get(m.Project, project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Project not found.")
    return project


def _set_status(project_id: uuid.UUID, status: str, **values: Any) -> None:
    with session_scope() as db:
        db.execute(update(m.Project).where(m.Project.id == project_id).values(status=status, **values))


def _analyze_and_index(project_id: uuid.UUID, settings: Settings, progress: jobs.Progress) -> None:
    """Analysis first, then the search index.

    Embedding a large project takes many minutes on a CPU, so the project becomes usable as
    soon as its analysis is saved. Keyword search works while the index is being built.
    """
    root = repo_path(settings, project_id)
    _set_status(project_id, "analyzing", error=None)
    try:
        with session_scope() as db:
            summary = run_analysis(db, project_id, root, settings, progress)
        head = None
        branch = None
        try:
            head, branch = git.head_commit(root), git.current_branch(root)
        except git.GitError:
            logger.warning("Project %s has no readable git head.", project_id)
        analyzed_at = datetime.now(UTC)
        summary["search_index"] = {"status": "building"}
        _set_status(
            project_id,
            "ready",
            summary=summary,
            head_commit=head,
            branch=branch,
            analyzed_at=analyzed_at,
            error=None,
        )
    except Exception as exc:
        _set_status(project_id, "failed", error=jobs.user_message(exc))
        raise
    _build_search_index(project_id, settings, progress, analyzed_at)


def _is_current_analysis(project_id: uuid.UUID, analyzed_at: datetime) -> bool:
    """False once the project was deleted or analyzed again, so this index is not needed."""
    with session_scope() as db:
        project = db.get(m.Project, project_id)
        return project is not None and project.status == "ready" and project.analyzed_at == analyzed_at


def _build_search_index(
    project_id: uuid.UUID, settings: Settings, progress: jobs.Progress, analyzed_at: datetime
) -> None:
    progress(75, "Building the search index with local embeddings.")

    def report(done: int, total: int) -> None:
        if not _is_current_analysis(project_id, analyzed_at):
            raise retrieval.IndexingCancelled
        progress(75 + int(24 * done / max(total, 1)), f"Embedded {done:,} of {total:,} code sections.")

    search_index: dict[str, Any] = {"status": "ready"}
    try:
        with session_scope() as db:
            search_index.update(retrieval.build_index(db, project_id, settings, report))
    except retrieval.IndexingCancelled:
        logger.info("Stopped building an outdated search index for project %s.", project_id)
        if not _project_exists(project_id):
            retrieval.delete_index(settings, project_id)
        return
    except Exception as exc:  # noqa: BLE001
        # Keyword search still works, so this does not fail the whole analysis.
        logger.exception("Embedding failed for project %s", project_id)
        search_index = {
            "status": "failed",
            "error": f"Semantic search is unavailable: {type(exc).__name__}. Keyword search still works.",
        }
    with session_scope() as db:
        project = db.get(m.Project, project_id, with_for_update=True)
        if project is None:
            retrieval.delete_index(settings, project_id)
        elif project.analyzed_at == analyzed_at:
            project.summary = {**(project.summary or {}), "search_index": search_index}


def _project_exists(project_id: uuid.UUID) -> bool:
    with session_scope() as db:
        return db.get(m.Project, project_id) is not None


def start_github_import(
    db: Session, user: m.User, url: str, branch: str | None, token: str | None, name: str | None
) -> m.Project:
    ensure_can_create_project(db, user)
    owner, repo = ingest.parse_github_url(url)
    if branch:
        git.validate_ref(branch)
    project = m.Project(
        owner_id=user.id,
        name=(name or f"{owner}/{repo}")[:200],
        source_type="github",
        source_url=ingest.normalized_github_url(url),
        branch=branch,
        status="pending",
    )
    db.add(project)
    db.commit()
    project_id = project.id
    settings = get_settings()
    normalized = project.source_url

    def work(progress: jobs.Progress) -> None:
        # The token exists only in this closure. It is never written to the database.
        _set_status(project_id, "importing")
        progress(2, "Cloning the repository.")
        try:
            ingest.import_github(repo_path(settings, project_id), normalized, branch, token, settings)
        except Exception as exc:
            _set_status(project_id, "failed", error=jobs.user_message(exc))
            raise
        _analyze_and_index(project_id, settings, progress)

    jobs.submit(project_id, "import", work)
    return project


def start_zip_import(db: Session, user: m.User, archive: Path, name: str) -> m.Project:
    project = m.Project(owner_id=user.id, name=name[:200] or "Uploaded project", source_type="zip", status="pending")
    db.add(project)
    db.commit()
    project_id = project.id
    settings = get_settings()

    def work(progress: jobs.Progress) -> None:
        _set_status(project_id, "importing")
        progress(2, "Extracting the archive.")
        try:
            ingest.import_zip(repo_path(settings, project_id), archive, settings, "Initial upload")
        except Exception as exc:
            _set_status(project_id, "failed", error=jobs.user_message(exc))
            raise
        finally:
            archive.unlink(missing_ok=True)
        _analyze_and_index(project_id, settings, progress)

    jobs.submit(project_id, "import", work)
    return project


def _claim(db: Session, project: m.Project) -> None:
    """Marks the project as busy, atomically, so two jobs can never process it at once."""
    result = db.execute(
        update(m.Project)
        .where(m.Project.id == project.id, m.Project.status.not_in(BUSY_STATES))
        .values(status="pending", error=None)
    )
    db.commit()
    if result.rowcount == 0:
        raise ConflictError("This project is still being processed. Please wait for it to finish.")
    db.refresh(project)


def start_new_zip_version(db: Session, project: m.Project, archive: Path, label: str | None) -> None:
    try:
        if project.source_type != "zip":
            raise ValidationFailed("This project comes from GitHub. Use sync to update it.")
        _claim(db, project)
    except Exception:
        archive.unlink(missing_ok=True)
        raise
    project_id = project.id
    settings = get_settings()
    message = (label or "New upload").replace("\n", " ")[:200]

    def work(progress: jobs.Progress) -> None:
        _set_status(project_id, "importing")
        progress(2, "Extracting the new version.")
        try:
            ingest.import_zip(repo_path(settings, project_id), archive, settings, message)
        except Exception as exc:
            # The old code and analysis are intact, so the project stays usable with the error shown.
            _set_status(project_id, "failed", error=jobs.user_message(exc))
            raise
        finally:
            archive.unlink(missing_ok=True)
        _analyze_and_index(project_id, settings, progress)

    jobs.submit(project_id, "import", work)


def start_sync(db: Session, project: m.Project, token: str | None) -> None:
    if project.source_type != "github":
        raise ValidationFailed("This project was uploaded. Upload a new ZIP version to update it.")
    settings = get_settings()
    if not repo_path(settings, project.id).exists():
        raise ValidationFailed("The source code is missing. Use retry to import it again.")
    _claim(db, project)
    project_id = project.id

    def work(progress: jobs.Progress) -> None:
        _set_status(project_id, "importing")
        progress(2, "Pulling the latest commits.")
        try:
            git.pull(repo_path(settings, project_id), token=token, timeout=settings.clone_timeout_seconds)
        except git.GitError as exc:
            message = f"Could not update the repository: {ingest.clean_git_error(str(exc))}"
            _set_status(project_id, "failed", error=message)
            raise ValidationFailed(message) from exc
        _analyze_and_index(project_id, settings, progress)

    jobs.submit(project_id, "sync", work)


def start_reanalyze(db: Session, project: m.Project, token: str | None = None) -> None:
    """Analyzes again. A GitHub import that never finished cloning is cloned again first."""
    settings = get_settings()
    project_id = project.id
    repo = repo_path(settings, project_id)
    if repo.exists() and any(repo.iterdir()):
        _claim(db, project)
        jobs.submit(project_id, "analyze", lambda progress: _analyze_and_index(project_id, settings, progress))
        return
    if project.source_type != "github" or not project.source_url:
        raise ValidationFailed("The source code of this project is missing. Upload it again as a new project.")
    _claim(db, project)
    url, branch = project.source_url, project.branch

    def work(progress: jobs.Progress) -> None:
        _set_status(project_id, "importing")
        progress(2, "Cloning the repository.")
        try:
            ingest.import_github(repo, url, branch, token, settings)
        except Exception as exc:
            _set_status(project_id, "failed", error=jobs.user_message(exc))
            raise
        _analyze_and_index(project_id, settings, progress)

    jobs.submit(project_id, "import", work)


def delete_project(db: Session, project: m.Project) -> None:
    if project.status in BUSY_STATES:
        raise ConflictError("This project is still being processed. Please wait for it to finish.")
    settings = get_settings()
    project_id = project.id
    db.delete(project)
    db.commit()
    shutil.rmtree(repo_path(settings, project_id), ignore_errors=True)
    retrieval.delete_index(settings, project_id)


def ensure_can_create_project(db: Session, user: m.User) -> None:
    settings = get_settings()
    rate_limiter.check(f"import:{user.id}", settings.rate_limit_imports_per_hour, 3600, "import")
    count = db.scalar(select(func.count(m.Project.id)).where(m.Project.owner_id == user.id)) or 0
    if count >= settings.max_projects_per_user:
        raise ConflictError(
            f"You have reached the limit of {settings.max_projects_per_user} projects. Delete one to add another."
        )
