"""Read-only views of the analysis: dashboard, architecture, inventory, graph, source."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import joinedload

from app.api.deps import DbSession, ReadyProject
from app.core.config import get_settings
from app.core.errors import ForbiddenError, NotFoundError, TooLargeError
from app.db import models as m
from app.services import graph_view
from app.services.analysis.scanner import is_sensitive
from app.services.projects import repo_path
from app.services.retrieval import _escape_like

router = APIRouter(prefix="/projects/{project_id}", tags=["insights"])

Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]
MAX_VIEWER_BYTES = 2 * 1024 * 1024
_SEVERITY_ORDER = case(
    (m.QualityIssue.severity == "critical", 0),
    (m.QualityIssue.severity == "high", 1),
    (m.QualityIssue.severity == "medium", 2),
    else_=3,
)


def _page(total: int, limit: int, offset: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/dashboard")
def dashboard(project: ReadyProject, db: DbSession) -> dict[str, Any]:
    summary = project.summary or {}
    recent = db.execute(
        select(m.QualityIssue, m.SourceFile.path)
        .outerjoin(m.SourceFile, m.QualityIssue.file_id == m.SourceFile.id)
        .where(m.QualityIssue.project_id == project.id, m.QualityIssue.severity.in_(("critical", "high")))
        .order_by(m.QualityIssue.severity, m.QualityIssue.id)
        .limit(15)
    ).all()
    return {
        "metrics": summary.get("metrics"),
        "frameworks": summary.get("frameworks"),
        "languages": summary.get("languages"),
        "quality": summary.get("quality"),
        "complexity_distribution": summary.get("complexity_distribution"),
        "hotspots": summary.get("hotspots"),
        "search_index": summary.get("search_index"),
        "generated_at": summary.get("generated_at"),
        "top_issues": [
            {
                "rule": issue.rule,
                "category": issue.category,
                "severity": issue.severity,
                "message": issue.message,
                "path": path,
                "line": issue.line,
            }
            for issue, path in recent
        ],
    }


@router.get("/architecture")
def architecture(project: ReadyProject) -> dict[str, Any]:
    summary = project.summary or {}
    return {
        key: summary.get(key)
        for key in (
            "frameworks",
            "languages",
            "runtime",
            "layers",
            "entry_points",
            "module_graph",
            "drupal_hooks",
            "cycles",
        )
    }


@router.get("/issues")
def issues(
    project: ReadyProject,
    db: DbSession,
    severity: str | None = None,
    category: str | None = None,
    rule: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    filters = [m.QualityIssue.project_id == project.id]
    if severity:
        filters.append(m.QualityIssue.severity == severity)
    if category:
        filters.append(m.QualityIssue.category == category)
    if rule:
        filters.append(m.QualityIssue.rule == rule)
    total = db.scalar(select(func.count(m.QualityIssue.id)).where(*filters)) or 0
    rows = db.execute(
        select(m.QualityIssue, m.SourceFile.path)
        .outerjoin(m.SourceFile, m.QualityIssue.file_id == m.SourceFile.id)
        .where(*filters)
        .order_by(_SEVERITY_ORDER, m.QualityIssue.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return _page(
        total,
        limit,
        offset,
        [
            {
                "id": issue.id,
                "rule": issue.rule,
                "category": issue.category,
                "severity": issue.severity,
                "message": issue.message,
                "path": path,
                "line": issue.line,
            }
            for issue, path in rows
        ],
    )


@router.get("/symbols")
def symbols(
    project: ReadyProject,
    db: DbSession,
    kind: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    filters = [m.Symbol.project_id == project.id]
    if kind:
        filters.append(m.Symbol.kind == kind)
    if q:
        pattern = f"%{_escape_like(q)}%"
        filters.append(
            or_(m.Symbol.name.ilike(pattern, escape="\\"), m.Symbol.qualified_name.ilike(pattern, escape="\\"))
        )
    total = db.scalar(select(func.count(m.Symbol.id)).where(*filters)) or 0
    rows = db.scalars(
        select(m.Symbol)
        .options(joinedload(m.Symbol.file))
        .where(*filters)
        .order_by(m.Symbol.qualified_name)
        .limit(limit)
        .offset(offset)
    ).all()
    return _page(
        total,
        limit,
        offset,
        [
            {
                "id": s.id,
                "kind": s.kind,
                "name": s.name,
                "qualified_name": s.qualified_name,
                "path": s.file.path,
                "start_line": s.start_line,
                "end_line": s.end_line,
                "signature": s.signature,
                "docstring": s.docstring,
                "complexity": s.complexity,
                "loc": s.loc,
            }
            for s in rows
        ],
    )


@router.get("/endpoints")
def endpoints(
    project: ReadyProject,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Limit = 200,
    offset: Offset = 0,
) -> dict[str, Any]:
    filters = [m.ApiEndpoint.project_id == project.id]
    if q:
        pattern = f"%{_escape_like(q)}%"
        filters.append(
            or_(m.ApiEndpoint.path.ilike(pattern, escape="\\"), m.ApiEndpoint.handler.ilike(pattern, escape="\\"))
        )
    total = db.scalar(select(func.count(m.ApiEndpoint.id)).where(*filters)) or 0
    rows = db.scalars(
        select(m.ApiEndpoint)
        .options(joinedload(m.ApiEndpoint.file))
        .where(*filters)
        .order_by(m.ApiEndpoint.path, m.ApiEndpoint.method)
        .limit(limit)
        .offset(offset)
    ).all()
    return _page(
        total,
        limit,
        offset,
        [
            {
                "id": e.id,
                "method": e.method,
                "path": e.path,
                "handler": e.handler,
                "framework": e.framework,
                "file": e.file.path,
                "line": e.line,
                "middleware": e.middleware or [],
            }
            for e in rows
        ],
    )


@router.get("/models")
def data_models(
    project: ReadyProject,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Limit = 200,
    offset: Offset = 0,
) -> dict[str, Any]:
    filters = [m.DataModel.project_id == project.id]
    if q:
        pattern = f"%{_escape_like(q)}%"
        filters.append(
            or_(m.DataModel.name.ilike(pattern, escape="\\"), m.DataModel.table_name.ilike(pattern, escape="\\"))
        )
    total = db.scalar(select(func.count(m.DataModel.id)).where(*filters)) or 0
    rows = db.scalars(
        select(m.DataModel)
        .options(joinedload(m.DataModel.file))
        .where(*filters)
        .order_by(m.DataModel.name)
        .limit(limit)
        .offset(offset)
    ).all()
    return _page(
        total,
        limit,
        offset,
        [
            {
                "id": x.id,
                "name": x.name,
                "table_name": x.table_name,
                "framework": x.framework,
                "file": x.file.path,
                "line": x.line,
                "fields": x.fields or [],
                "relations": x.relations or [],
            }
            for x in rows
        ],
    )


@router.get("/dependencies")
def dependencies(
    project: ReadyProject,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Limit = 200,
    offset: Offset = 0,
) -> dict[str, Any]:
    filters = [m.PackageDependency.project_id == project.id]
    if q:
        filters.append(m.PackageDependency.name.ilike(f"%{_escape_like(q)}%", escape="\\"))
    total = db.scalar(select(func.count(m.PackageDependency.id)).where(*filters)) or 0
    rows = db.scalars(
        select(m.PackageDependency)
        .where(*filters)
        .order_by(m.PackageDependency.ecosystem, m.PackageDependency.name)
        .limit(limit)
        .offset(offset)
    ).all()
    return _page(
        total,
        limit,
        offset,
        [
            {
                "id": p.id,
                "name": p.name,
                "version": p.version,
                "ecosystem": p.ecosystem,
                "manifest_path": p.manifest_path,
                "is_dev": p.is_dev,
            }
            for p in rows
        ],
    )


@router.get("/files")
def files(
    project: ReadyProject,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=300)] = None,
    parsed_only: bool = False,
    limit: Limit = 500,
    offset: Offset = 0,
) -> dict[str, Any]:
    filters = [m.SourceFile.project_id == project.id]
    if q:
        filters.append(m.SourceFile.path.ilike(f"%{_escape_like(q)}%", escape="\\"))
    if parsed_only:
        filters.append(m.SourceFile.is_parsed.is_(True))
    total = db.scalar(select(func.count(m.SourceFile.id)).where(*filters)) or 0
    rows = db.scalars(
        select(m.SourceFile).where(*filters).order_by(m.SourceFile.path).limit(limit).offset(offset)
    ).all()
    return _page(
        total,
        limit,
        offset,
        [
            {
                "path": f.path,
                "language": f.language,
                "role": f.role,
                "loc": f.loc,
                "complexity": f.complexity,
                "is_parsed": f.is_parsed,
            }
            for f in rows
        ],
    )


@router.get("/tree")
def tree(
    project: ReadyProject, db: DbSession, directory: Annotated[str, Query(max_length=1024)] = ""
) -> dict[str, Any]:
    """One folder of the file tree, so large projects load only what is opened."""
    prefix = f"{directory.rstrip('/')}/" if directory else ""
    rows = db.execute(
        select(m.SourceFile.path, m.SourceFile.language, m.SourceFile.loc, m.SourceFile.role).where(
            m.SourceFile.project_id == project.id,
            m.SourceFile.path.like(f"{_escape_like(prefix)}%", escape="\\"),
        )
    ).all()
    folders: dict[str, int] = {}
    files: list[dict[str, Any]] = []
    for path, language, loc, role in rows:
        rest = path[len(prefix) :]
        head, separator, _ = rest.partition("/")
        if separator:
            folders[head] = folders.get(head, 0) + 1
        else:
            files.append({"name": head, "path": path, "language": language, "loc": loc, "role": role})
    return {
        "directory": directory,
        "folders": [{"name": name, "path": f"{prefix}{name}", "files": folders[name]} for name in sorted(folders)],
        "files": sorted(files, key=lambda item: item["name"]),
    }


@router.get("/source")
def source(project: ReadyProject, db: DbSession, path: Annotated[str, Query(max_length=1024)]) -> dict[str, Any]:
    """Source code for the viewer. Only files known to the analysis can be read."""
    record = db.scalars(
        select(m.SourceFile).where(m.SourceFile.project_id == project.id, m.SourceFile.path == path)
    ).first()
    if record is None:
        raise NotFoundError("File not found in this project.")
    if is_sensitive(path):
        raise ForbiddenError("This file may contain secrets, so it is not shown.")
    root = repo_path(get_settings(), project.id).resolve()
    absolute = (root / path).resolve()
    if not absolute.is_relative_to(root) or not absolute.is_file() or absolute.is_symlink():
        raise NotFoundError("File not found in this project.")
    if absolute.stat().st_size > MAX_VIEWER_BYTES:
        raise TooLargeError("This file is too large to display.")
    detail = graph_view.file_detail(db, project.id, path)
    return {
        **(detail or {}),
        "content": absolute.read_bytes().decode("utf-8", errors="replace"),
    }


@router.get("/file-detail")
def file_detail(project: ReadyProject, db: DbSession, path: Annotated[str, Query(max_length=1024)]) -> dict[str, Any]:
    detail = graph_view.file_detail(db, project.id, path)
    if detail is None:
        raise NotFoundError("File not found in this project.")
    return detail


@router.get("/graph")
def graph(
    project: ReadyProject,
    db: DbSession,
    level: Literal["module", "file", "symbol"] = "file",
    language: str | None = None,
    include_tests: bool = False,
    focus: Annotated[str | None, Query(max_length=1024)] = None,
    depth: Annotated[int, Query(ge=1, le=5)] = 2,
    limit: Annotated[int, Query(ge=1, le=graph_view.MAX_NODES)] = 400,
) -> dict[str, Any]:
    if level == "module":
        return graph_view.module_graph(project.summary)
    if level == "symbol":
        return graph_view.symbol_graph(db, project.id, focus=focus, limit=limit, include_tests=include_tests)
    return graph_view.file_graph(
        db, project.id, language=language, include_tests=include_tests, focus=focus, depth=depth, limit=limit
    )
