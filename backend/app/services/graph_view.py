"""Shapes the stored dependency graph for the interactive viewer."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict, deque
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.core.errors import ValidationFailed
from app.db import models as m
from app.services.analysis.architecture import module_of
from app.services.analysis.scanner import NON_PRODUCTION_ROLES

MAX_NODES = 1500


def _file_edges(db: Session, project_id: uuid.UUID) -> list[tuple[str, str, str, int]]:
    """Edges grouped in the database. A large project has hundreds of thousands of raw edges."""
    source = aliased(m.SourceFile)
    target = aliased(m.SourceFile)
    rows = db.execute(
        select(source.path, target.path, m.GraphEdge.kind, func.count())
        .select_from(m.GraphEdge)
        .join(source, m.GraphEdge.source_file_id == source.id)
        .join(target, m.GraphEdge.target_file_id == target.id)
        .where(m.GraphEdge.project_id == project_id, m.GraphEdge.source_file_id != m.GraphEdge.target_file_id)
        .group_by(source.path, target.path, m.GraphEdge.kind)
    ).all()
    return [(a, b, kind, count) for a, b, kind, count in rows]


def _neighbourhood(edges: list[tuple[str, str, str, int]], focus: str, depth: int) -> set[str]:
    forward: dict[str, set[str]] = defaultdict(set)
    backward: dict[str, set[str]] = defaultdict(set)
    for source, target, _, _ in edges:
        forward[source].add(target)
        backward[target].add(source)
    keep = {focus}
    for adjacency in (forward, backward):
        queue: deque[tuple[str, int]] = deque([(focus, 0)])
        seen = {focus}
        while queue:
            node, distance = queue.popleft()
            if distance >= depth:
                continue
            for neighbour in adjacency.get(node, ()):
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append((neighbour, distance + 1))
        keep |= seen
    return keep


def file_graph(
    db: Session,
    project_id: uuid.UUID,
    *,
    language: str | None,
    include_tests: bool,
    focus: str | None,
    depth: int,
    limit: int,
) -> dict[str, Any]:
    files = db.execute(
        select(
            m.SourceFile.path, m.SourceFile.language, m.SourceFile.role, m.SourceFile.loc, m.SourceFile.complexity
        ).where(m.SourceFile.project_id == project_id, m.SourceFile.is_parsed.is_(True))
    ).all()
    info = {row[0]: row for row in files}
    edges = _file_edges(db, project_id)
    fan_in: Counter[str] = Counter()
    fan_out: Counter[str] = Counter()
    for source, target in {(a, b) for a, b, _, _ in edges}:
        fan_out[source] += 1
        fan_in[target] += 1
    allowed = set(info)
    if focus is not None:
        if focus not in info:
            raise ValidationFailed("That file is not part of the dependency graph.")
        allowed &= _neighbourhood(edges, focus, depth)
    if language:
        allowed = {p for p in allowed if info[p][1] == language}
    if not include_tests:
        allowed = {p for p in allowed if info[p][2] != "test" or p == focus}
    total = len(allowed)
    if total > limit:
        # Keep the most connected files, since they matter most for understanding.
        ranked = sorted(allowed, key=lambda p: (-(fan_in[p] + fan_out[p]), p))
        allowed = set(ranked[:limit])
    combined: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for source, target, kind, count in edges:
        if source in allowed and target in allowed:
            combined[(source, target)][kind] += count
    nodes = [
        {
            "id": path,
            "label": path.rsplit("/", 1)[-1],
            "path": path,
            "language": info[path][1],
            "role": info[path][2],
            "loc": info[path][3],
            "complexity": info[path][4],
            "fan_in": fan_in[path],
            "fan_out": fan_out[path],
            "module": module_of(path),
        }
        for path in sorted(allowed)
    ]
    links = [
        {
            "id": f"{source}->{target}",
            "source": source,
            "target": target,
            "kinds": dict(kinds),
            "weight": sum(kinds.values()),
        }
        for (source, target), kinds in combined.items()
    ]
    return {"level": "file", "nodes": nodes, "edges": links, "total_nodes": total, "truncated": total > limit}


def module_graph(summary: dict[str, Any] | None) -> dict[str, Any]:
    graph = (summary or {}).get("module_graph") or {}
    modules = graph.get("modules") or []
    return {
        "level": "module",
        "nodes": [
            {
                "id": mod["name"],
                "label": mod["name"],
                "path": mod["name"],
                "language": mod.get("language"),
                "role": "module",
                "loc": mod.get("loc", 0),
                "files": mod.get("files", 0),
                "module": mod["name"],
                "fan_in": 0,
                "fan_out": 0,
                "complexity": 0,
            }
            for mod in modules
        ],
        "edges": [
            {
                "id": f"{link['source']}->{link['target']}",
                "source": link["source"],
                "target": link["target"],
                "kinds": {"reference": link["weight"]},
                "weight": link["weight"],
            }
            for link in graph.get("links") or []
        ],
        "total_nodes": len(modules),
        "truncated": False,
    }


def symbol_graph(
    db: Session, project_id: uuid.UUID, *, focus: str | None, limit: int, include_tests: bool = False
) -> dict[str, Any]:
    source_symbol = aliased(m.Symbol)
    target_symbol = aliased(m.Symbol)
    source_file = aliased(m.SourceFile)
    target_file = aliased(m.SourceFile)
    statement = (
        select(
            source_symbol.id,
            source_symbol.name,
            source_symbol.kind,
            source_symbol.qualified_name,
            source_file.path,
            source_file.language,
            source_symbol.start_line,
            source_symbol.complexity,
            target_symbol.id,
            target_symbol.name,
            target_symbol.kind,
            target_symbol.qualified_name,
            target_file.path,
            target_file.language,
            target_symbol.start_line,
            target_symbol.complexity,
            m.GraphEdge.kind,
        )
        .select_from(m.GraphEdge)
        .join(source_symbol, m.GraphEdge.source_symbol_id == source_symbol.id)
        .join(target_symbol, m.GraphEdge.target_symbol_id == target_symbol.id)
        .join(source_file, source_symbol.file_id == source_file.id)
        .join(target_file, target_symbol.file_id == target_file.id)
        .where(m.GraphEdge.project_id == project_id)
    )
    if not include_tests:
        hidden = tuple(NON_PRODUCTION_ROLES)
        statement = statement.where(source_file.role.not_in(hidden), target_file.role.not_in(hidden))
    if focus:
        statement = statement.where((source_file.path == focus) | (target_file.path == focus))
    nodes: dict[str, dict[str, Any]] = {}
    combined: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    truncated = False
    for row in db.execute(statement).all():
        source_id, target_id = f"s{row[0]}", f"s{row[8]}"
        if source_id == target_id:
            continue
        if len(nodes) >= limit and (source_id not in nodes or target_id not in nodes):
            truncated = True
            continue
        for offset, node_id in ((0, source_id), (8, target_id)):
            if node_id not in nodes:
                nodes[node_id] = {
                    "id": node_id,
                    "label": row[offset + 1],
                    "qualified_name": row[offset + 3],
                    "kind": row[offset + 2],
                    "path": row[offset + 4],
                    "language": row[offset + 5],
                    "line": row[offset + 6],
                    "complexity": row[offset + 7],
                    "role": row[offset + 2],
                    "module": module_of(row[offset + 4]),
                    "loc": 0,
                    "fan_in": 0,
                    "fan_out": 0,
                }
        combined[(source_id, target_id)][row[16]] += 1
    for source, target in combined:
        nodes[source]["fan_out"] += 1
        nodes[target]["fan_in"] += 1
    return {
        "level": "symbol",
        "nodes": list(nodes.values()),
        "edges": [
            {
                "id": f"{source}->{target}",
                "source": source,
                "target": target,
                "kinds": dict(kinds),
                "weight": sum(kinds.values()),
            }
            for (source, target), kinds in combined.items()
        ],
        "total_nodes": len(nodes),
        "truncated": truncated,
    }


def file_detail(db: Session, project_id: uuid.UUID, path: str) -> dict[str, Any] | None:
    file = db.scalars(
        select(m.SourceFile).where(m.SourceFile.project_id == project_id, m.SourceFile.path == path)
    ).first()
    if file is None:
        return None
    other = aliased(m.SourceFile)
    outgoing = db.execute(
        select(other.path, m.GraphEdge.kind, m.GraphEdge.line)
        .join(other, m.GraphEdge.target_file_id == other.id)
        .where(m.GraphEdge.source_file_id == file.id, other.id != file.id)
    ).all()
    incoming = db.execute(
        select(other.path, m.GraphEdge.kind, m.GraphEdge.line)
        .join(other, m.GraphEdge.source_file_id == other.id)
        .where(m.GraphEdge.target_file_id == file.id, other.id != file.id)
    ).all()

    def group(rows: Any) -> list[dict[str, Any]]:
        by_path: dict[str, Counter[str]] = defaultdict(Counter)
        for other_path, kind, _ in rows:
            by_path[other_path][kind] += 1
        return [{"path": p, "kinds": dict(k)} for p, k in sorted(by_path.items())]

    symbols = db.scalars(select(m.Symbol).where(m.Symbol.file_id == file.id).order_by(m.Symbol.start_line)).all()
    return {
        "path": file.path,
        "language": file.language,
        "role": file.role,
        "loc": file.loc,
        "complexity": file.complexity,
        "dependencies": group(outgoing),
        "dependents": group(incoming),
        "symbols": [
            {
                "id": s.id,
                "kind": s.kind,
                "name": s.name,
                "qualified_name": s.qualified_name,
                "start_line": s.start_line,
                "end_line": s.end_line,
                "complexity": s.complexity,
                "signature": s.signature,
            }
            for s in symbols
        ],
    }
