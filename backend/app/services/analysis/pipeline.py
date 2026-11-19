"""Runs the whole analysis of one repository and stores the results."""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from psycopg.types.json import Jsonb
from sqlalchemy import delete, insert, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ValidationFailed
from app.db import models as m
from app.services import git
from app.services.analysis import architecture, frameworks, quality
from app.services.analysis.chunking import chunk_file
from app.services.analysis.graph import Edge, GraphBuilder, find_import_cycles
from app.services.analysis.parsed import EndpointRef, ModelRef, ParsedFile
from app.services.analysis.parser import parse_source
from app.services.analysis.scanner import (
    INDEXED_ONLY_LANGUAGES,
    NON_PRODUCTION_ROLES,
    PARSED_LANGUAGES,
    ScannedFile,
    count_loc,
    scan_repository,
)

logger = logging.getLogger("legacylens.analysis")

Progress = Callable[[int, str], None]


@dataclass
class _FileData:
    scanned: ScannedFile
    content: str
    loc: int
    parsed: ParsedFile | None = None
    endpoints: list[EndpointRef] = field(default_factory=list)
    models: list[ModelRef] = field(default_factory=list)


def _read(file: ScannedFile) -> str:
    return file.absolute.read_bytes().decode("utf-8", errors="replace")


def _collect(root: Path, settings: Settings, progress: Progress) -> list[_FileData]:
    scanned = scan_repository(
        root,
        max_file_bytes=settings.max_file_kb * 1024,
        max_files=settings.max_analyzed_files,
    )
    progress(10, f"Found {len(scanned)} files. Parsing source code.")
    files: list[_FileData] = []
    for position, file in enumerate(scanned):
        try:
            content = _read(file)
        except OSError:
            continue
        data = _FileData(file, content, count_loc(content))
        if file.language in PARSED_LANGUAGES and not file.path.endswith(".blade.php"):
            data.parsed = parse_source(file.language, content.encode("utf-8"), file.path)
        name = PurePosixPath(file.path).name
        if name.endswith(".routing.yml"):
            data.endpoints.extend(frameworks.parse_drupal_routing(content))
        elif file.language == "sql":
            data.models.extend(frameworks.parse_sql_models(content))
        elif file.language == "prisma":
            data.models.extend(frameworks.parse_prisma_models(content))
        files.append(data)
        if position % 50 == 0 and scanned:
            progress(10 + int(40 * position / len(scanned)), f"Parsed {position} of {len(scanned)} files.")
    return files


def _collect_manifests(files: list[_FileData]) -> frameworks.ManifestInfo:
    merged = frameworks.ManifestInfo()
    for data in files:
        # The sample apps of a library do not describe the library's own dependencies.
        if data.scanned.role in NON_PRODUCTION_ROLES:
            continue
        info = frameworks.parse_manifest(data.scanned.path, data.content)
        if info is None:
            continue
        merged.packages.extend(info.packages)
        merged.entry_points.extend(info.entry_points)
        merged.php_version = merged.php_version or info.php_version
        merged.node_engine = merged.node_engine or info.node_engine
        merged.python_version = merged.python_version or info.python_version
    return merged


def _add_legacy_php_endpoints(files: list[_FileData], detected: list[str]) -> None:
    """Plain PHP scripts that read request data at the top level are entry points."""
    if {"Laravel", "Drupal", "Symfony", "Lumen", "CodeIgniter"} & set(detected):
        return
    for data in files:
        parsed = data.parsed
        if parsed is None or parsed.language != "php" or not parsed.request_inputs:
            continue
        if data.scanned.role == "test":
            continue
        method = "POST" if parsed.request_inputs & {"_POST", "_FILES"} else "GET"
        parsed.endpoints.append(EndpointRef(method, "/" + data.scanned.path, "<script>", "php", 1))


def _remove_duplicate_sql_models(files: list[_FileData]) -> None:
    """Migrations repeat the same table many times, and ORMs already describe those tables."""
    defined = {(model.table or model.name).lower() for data in files if data.parsed for model in data.parsed.models}
    defined |= {model.name.lower() for data in files for model in data.models if model.framework != "sql"}
    seen: set[str] = set()
    for data in files:
        kept = []
        for model in data.models:
            key = model.name.lower()
            if model.framework == "sql" and (key in defined or key in seen):
                continue
            seen.add(key)
            kept.append(model)
        data.models = kept


def _detect_language_stats(files: list[_FileData]) -> list[dict[str, Any]]:
    loc: Counter[str] = Counter()
    count: Counter[str] = Counter()
    for data in files:
        if data.scanned.language in PARSED_LANGUAGES:
            loc[data.scanned.language] += data.loc
            count[data.scanned.language] += 1
    return [{"language": k, "files": count[k], "loc": v} for k, v in loc.most_common()]


def run_analysis(
    db: Session, project_id: uuid.UUID, root: Path, settings: Settings, progress: Progress
) -> dict[str, Any]:
    """Replaces all stored analysis of the project. Commit is left to the caller."""
    progress(5, "Scanning files.")
    files = _collect(root, settings, progress)
    if not any(d.scanned.language in PARSED_LANGUAGES for d in files):
        raise ValidationFailed(
            "No supported source files were found. LegacyLens analyzes PHP, Python, JavaScript and TypeScript."
        )
    manifests = _collect_manifests(files)
    all_paths = {d.scanned.path for d in files}
    detected = frameworks.detect_frameworks(all_paths, manifests.packages)
    seen_in_code = {
        item.framework
        for data in files
        if data.scanned.role not in NON_PRODUCTION_ROLES
        for item in (
            *data.endpoints,
            *data.models,
            *(data.parsed.endpoints if data.parsed else []),
            *(data.parsed.models if data.parsed else []),
        )
    }
    detected = sorted(set(detected) | frameworks.frameworks_from_code(seen_in_code))
    _add_legacy_php_endpoints(files, detected)

    progress(55, "Building the dependency graph.")
    parsed = {d.scanned.path: d.parsed for d in files if d.parsed is not None}
    builder = GraphBuilder(parsed)
    edges = builder.build()
    builder.apply_route_prefixes()
    edges = sorted(builder.edges, key=lambda e: (e.source_path, e.target_path, e.kind, e.line or 0))
    _remove_duplicate_sql_models(files)
    cycles = find_import_cycles(edges)

    progress(65, "Checking code quality.")
    findings: list[quality.Finding] = []
    for data in files:
        scanned = data.scanned
        if scanned.is_sensitive:
            continue
        findings.extend(quality.scan_lines(scanned.path, scanned.language, scanned.role, data.content))
        if data.parsed is not None:
            findings.extend(quality.structural_findings(scanned.path, data.parsed, data.loc, scanned.role))
    findings.extend(quality.cycle_findings(cycles))

    progress(70, "Saving results.")
    _clear_previous(db, project_id)
    file_ids = _store_files(db, project_id, files)
    symbol_ids = _store_symbols(db, project_id, files, file_ids)
    _store_endpoints_and_models(db, project_id, files, file_ids)
    _store_packages(db, project_id, manifests)
    _store_edges(db, project_id, edges, file_ids, symbol_ids)
    _store_findings(db, project_id, findings, file_ids)
    _store_chunks(db, project_id, files, file_ids)
    db.flush()

    return _build_summary(files, manifests, detected, edges, cycles, findings, root)


def _clear_previous(db: Session, project_id: uuid.UUID) -> None:
    # Children first, in bulk. Cascading from each file would run one delete per file.
    for model in (
        m.GraphEdge,
        m.CodeChunk,
        m.ApiEndpoint,
        m.DataModel,
        m.QualityIssue,
        m.PackageDependency,
        m.Symbol,
        m.SourceFile,
    ):
        db.execute(delete(model).where(model.project_id == project_id))


def _allocate_ids(db: Session, table: str, count: int) -> list[int]:
    """Ids from the table's own sequence, so rows can be written with COPY and still be referenced."""
    if count == 0:
        return []
    statement = text("SELECT nextval(pg_get_serial_sequence(:table, 'id')) FROM generate_series(1, :count)")
    return list(db.scalars(statement, {"table": table, "count": count}))


def _copy_rows(db: Session, table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    """Bulk load with PostgreSQL COPY, which is about a hundred times faster than INSERT for large
    projects. It runs inside the session's transaction, so a failure still rolls everything back."""
    connection = db.connection().connection.driver_connection
    column_list = ", ".join(columns)
    with connection.cursor() as cursor, cursor.copy(f"COPY {table} ({column_list}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def _store_files(db: Session, project_id: uuid.UUID, files: list[_FileData]) -> dict[str, int]:
    rows = [
        {
            "project_id": project_id,
            "path": d.scanned.path,
            "language": d.scanned.language,
            "size_bytes": d.scanned.size,
            "loc": d.loc,
            "role": d.scanned.role,
            "is_parsed": d.parsed is not None,
            "parse_errors": d.parsed.parse_errors if d.parsed else 0,
            "complexity": _file_complexity(d.parsed),
        }
        for d in files
    ]
    result = db.execute(insert(m.SourceFile).returning(m.SourceFile.id, m.SourceFile.path), rows)
    return {path: file_id for file_id, path in result.all()}


def _file_complexity(parsed: ParsedFile | None) -> int:
    if parsed is None:
        return 0
    return parsed.top_level_complexity + sum(
        max(s.complexity - 1, 0) for s in parsed.symbols if s.kind in ("function", "method")
    )


def _store_symbols(
    db: Session, project_id: uuid.UUID, files: list[_FileData], file_ids: dict[str, int]
) -> dict[tuple[str, str], int]:
    rows: list[dict[str, Any]] = []
    for data in files:
        if data.parsed is None:
            continue
        file_id = file_ids[data.scanned.path]
        for symbol in data.parsed.symbols:
            rows.append(
                {
                    "project_id": project_id,
                    "file_id": file_id,
                    "kind": symbol.kind,
                    "name": symbol.name[:512],
                    "qualified_name": symbol.qualified_name[:1024],
                    "parent_name": symbol.parent[:512] if symbol.parent else None,
                    "start_line": symbol.start_line,
                    "end_line": symbol.end_line,
                    "signature": symbol.signature,
                    "docstring": symbol.docstring,
                    "complexity": symbol.complexity,
                    "loc": symbol.loc,
                    "bases": symbol.bases + symbol.implements + symbol.traits or None,
                }
            )
    if not rows:
        return {}
    ids = _allocate_ids(db, "symbols", len(rows))
    columns = list(rows[0])
    _copy_rows(
        db,
        "symbols",
        ["id", *columns],
        (
            [identifier, *(Jsonb(row[c]) if c == "bases" and row[c] is not None else row[c] for c in columns)]
            for identifier, row in zip(ids, rows, strict=True)
        ),
    )
    path_by_id = {file_id: path for path, file_id in file_ids.items()}
    return {
        (path_by_id[row["file_id"]], row["qualified_name"]): identifier
        for identifier, row in zip(ids, rows, strict=True)
    }


def _store_endpoints_and_models(
    db: Session, project_id: uuid.UUID, files: list[_FileData], file_ids: dict[str, int]
) -> None:
    endpoints: list[dict[str, Any]] = []
    models: list[dict[str, Any]] = []
    for data in files:
        # Routes defined by test apps and samples are not part of the project's real API.
        if data.scanned.role in NON_PRODUCTION_ROLES:
            continue
        file_id = file_ids[data.scanned.path]
        all_endpoints = [*data.endpoints, *(data.parsed.endpoints if data.parsed else [])]
        all_models = [*data.models, *(data.parsed.models if data.parsed else [])]
        for endpoint in all_endpoints:
            endpoints.append(
                {
                    "project_id": project_id,
                    "file_id": file_id,
                    "method": endpoint.method[:20],
                    "path": endpoint.path[:1024],
                    "handler": endpoint.handler[:1024] if endpoint.handler else None,
                    "framework": endpoint.framework,
                    "line": endpoint.line,
                    "middleware": endpoint.middleware or None,
                }
            )
        for model in all_models:
            models.append(
                {
                    "project_id": project_id,
                    "file_id": file_id,
                    "name": model.name[:512],
                    "table_name": model.table[:512] if model.table else None,
                    "framework": model.framework,
                    "line": model.line,
                    "fields": model.fields or None,
                    "relations": model.relations or None,
                }
            )
    if endpoints:
        db.execute(insert(m.ApiEndpoint), endpoints)
    if models:
        db.execute(insert(m.DataModel), models)


def _store_packages(db: Session, project_id: uuid.UUID, manifests: frameworks.ManifestInfo) -> None:
    rows = [
        {
            "project_id": project_id,
            "name": package.name[:512],
            "version": package.version[:255] if package.version else None,
            "ecosystem": package.ecosystem,
            "manifest_path": package.manifest_path[:1024],
            "is_dev": package.is_dev,
        }
        for package in manifests.packages
    ]
    if rows:
        db.execute(insert(m.PackageDependency), rows)


def _store_edges(
    db: Session,
    project_id: uuid.UUID,
    edges: list[Edge],
    file_ids: dict[str, int],
    symbol_ids: dict[tuple[str, str], int],
) -> None:
    rows: list[dict[str, Any]] = []
    for edge in edges:
        source = file_ids.get(edge.source_path)
        target = file_ids.get(edge.target_path)
        if source is None or target is None:
            continue
        rows.append(
            {
                "project_id": project_id,
                "source_file_id": source,
                "target_file_id": target,
                "source_symbol_id": symbol_ids.get((edge.source_path, edge.source_symbol or "")),
                "target_symbol_id": symbol_ids.get((edge.target_path, edge.target_symbol or "")),
                "kind": edge.kind,
                "line": edge.line,
            }
        )
    if rows:
        columns = list(rows[0])
        _copy_rows(db, "graph_edges", columns, ([row[c] for c in columns] for row in rows))


def _store_findings(
    db: Session, project_id: uuid.UUID, findings: list[quality.Finding], file_ids: dict[str, int]
) -> None:
    rows = [
        {
            "project_id": project_id,
            "file_id": file_ids.get(finding.path) if finding.path else None,
            "rule": finding.rule,
            "category": finding.category,
            "severity": finding.severity,
            "message": finding.message,
            "line": finding.line,
        }
        for finding in findings
    ]
    if rows:
        db.execute(insert(m.QualityIssue), rows)


def _store_chunks(db: Session, project_id: uuid.UUID, files: list[_FileData], file_ids: dict[str, int]) -> None:
    rows: list[dict[str, Any]] = []
    for data in files:
        scanned = data.scanned
        # Sensitive files and data formats are never made available to the AI.
        if scanned.is_sensitive or (
            scanned.language not in PARSED_LANGUAGES and scanned.language not in INDEXED_ONLY_LANGUAGES
        ):
            continue
        symbols = data.parsed.symbols if data.parsed else []
        for chunk in chunk_file(data.content, symbols):
            rows.append(
                {
                    "project_id": project_id,
                    "file_id": file_ids[scanned.path],
                    "symbol_name": chunk.symbol_name[:1024] if chunk.symbol_name else None,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    # PostgreSQL text columns cannot hold NUL characters.
                    "content": chunk.content.replace("\x00", ""),
                }
            )
    if rows:
        columns = list(rows[0])
        _copy_rows(db, "code_chunks", columns, ([row[c] for c in columns] for row in rows))


def _build_summary(
    files: list[_FileData],
    manifests: frameworks.ManifestInfo,
    detected: list[str],
    edges: list[Edge],
    cycles: list[list[str]],
    findings: list[quality.Finding],
    root: Path,
) -> dict[str, Any]:
    source_files = [d for d in files if d.scanned.language in PARSED_LANGUAGES]
    code_loc = sum(d.loc for d in source_files)
    roles = Counter(d.scanned.role for d in source_files)
    symbols = [s for d in files if d.parsed for s in d.parsed.symbols]
    kinds = Counter(s.kind for s in symbols)
    # Hot spots and complexity describe the product, not its tests and sample code.
    production = [d for d in source_files if d.scanned.role not in NON_PRODUCTION_ROLES]
    production_paths = {d.scanned.path for d in production}
    callables = [s for d in production if d.parsed for s in d.parsed.symbols if s.kind in ("function", "method")]
    avg_complexity = round(sum(s.complexity for s in callables) / len(callables), 2) if callables else 0
    fan_in: Counter[str] = Counter()
    fan_out: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        pair = (edge.source_path, edge.target_path)
        if (
            edge.source_path != edge.target_path
            and pair not in seen
            and edge.source_path in production_paths
            and edge.target_path in production_paths
        ):
            seen.add(pair)
            fan_out[edge.source_path] += 1
            fan_in[edge.target_path] += 1
    file_info = {d.scanned.path: {"loc": d.loc, "language": d.scanned.language} for d in production}
    severity_counts = Counter(f.severity for f in findings)
    category_counts = Counter(f.category for f in findings)
    rule_counts = Counter(f.rule for f in findings)
    test_files = roles.get("test", 0)
    real = [d for d in files if d.scanned.role not in NON_PRODUCTION_ROLES]
    endpoints = sum(len(d.endpoints) + (len(d.parsed.endpoints) if d.parsed else 0) for d in real)
    data_models = sum(len(d.models) + (len(d.parsed.models) if d.parsed else 0) for d in real)
    hooks = [
        {"module": h.module, "hook": h.hook, "function": h.function, "path": d.scanned.path, "line": h.line}
        for d in files
        if d.parsed
        for h in d.parsed.hooks
    ]
    parsed_count = sum(1 for d in source_files if d.parsed is not None)
    error_files = sum(1 for d in source_files if d.parsed and d.parsed.parse_errors > 0)
    complexity_buckets = Counter(
        "1-5"
        if s.complexity <= 5
        else "6-10"
        if s.complexity <= 10
        else "11-20"
        if s.complexity <= 20
        else "21-40"
        if s.complexity <= 40
        else "41+"
        for s in callables
    )
    sorted_callables = sorted(callables, key=lambda s: s.complexity, reverse=True)
    symbol_path = {id(s): d.scanned.path for d in files if d.parsed for s in d.parsed.symbols}

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "metrics": {
            "total_files": len(files),
            "source_files": len(source_files),
            "parsed_files": parsed_count,
            "files_with_syntax_errors": error_files,
            "loc": code_loc,
            "functions": kinds.get("function", 0),
            "methods": kinds.get("method", 0),
            "classes": kinds.get("class", 0),
            "interfaces": kinds.get("interface", 0),
            "traits": kinds.get("trait", 0),
            "enums": kinds.get("enum", 0),
            "endpoints": endpoints,
            "data_models": data_models,
            "packages": len(manifests.packages),
            "edges": len(edges),
            "cycles": len(cycles),
            "test_files": test_files,
            "avg_complexity": avg_complexity,
        },
        "frameworks": detected,
        "languages": _detect_language_stats(files),
        "runtime": {
            "php": manifests.php_version,
            "node": manifests.node_engine,
            "python": manifests.python_version,
        },
        "layers": architecture.describe_layers(roles),
        "entry_points": architecture.find_entry_points([d.scanned.path for d in production], manifests.entry_points),
        "module_graph": architecture.build_module_graph(
            file_info, [e for e in edges if e.source_path in production_paths and e.target_path in production_paths]
        ),
        "drupal_hooks": hooks[:500],
        "cycles": cycles[:20],
        "quality": {
            "scores": quality.compute_scores(findings, sum(d.loc for d in production)),
            "total_issues": len(findings),
            "by_severity": {k: severity_counts.get(k, 0) for k in ("critical", "high", "medium", "low")},
            "by_category": {k: category_counts.get(k, 0) for k in ("security", "maintainability", "reliability")},
            "top_rules": [{"rule": r, "count": c} for r, c in rule_counts.most_common(12)],
        },
        "complexity_distribution": [
            {"bucket": b, "count": complexity_buckets.get(b, 0)} for b in ("1-5", "6-10", "11-20", "21-40", "41+")
        ],
        "hotspots": {
            "complex_functions": [
                {
                    "name": s.qualified_name,
                    "path": symbol_path.get(id(s)),
                    "line": s.start_line,
                    "complexity": s.complexity,
                    "loc": s.loc,
                }
                for s in sorted_callables[:10]
            ],
            "most_depended_on": [{"path": p, "dependents": c} for p, c in fan_in.most_common(10)],
            "most_dependencies": [{"path": p, "dependencies": c} for p, c in fan_out.most_common(10)],
            "largest_files": [
                {"path": d.scanned.path, "loc": d.loc, "language": d.scanned.language}
                for d in sorted(production, key=lambda d: d.loc, reverse=True)[:10]
            ],
        },
        "head_commit": _safe_head(root),
    }


def _safe_head(root: Path) -> str | None:
    try:
        return git.head_commit(root)
    except git.GitError:
        return None
