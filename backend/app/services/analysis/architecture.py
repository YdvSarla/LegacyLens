"""Builds the project level summary from analysis results."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import Any

from app.services.analysis.graph import Edge

_ENTRY_NAMES = {
    "index.php",
    "artisan",
    "drush",
    "manage.py",
    "wsgi.py",
    "asgi.py",
    "main.py",
    "app.py",
    "server.py",
    "index.js",
    "server.js",
    "app.js",
    "main.js",
    "index.ts",
    "server.ts",
    "main.ts",
    "app.ts",
}
_DEPTH_ROOTS = {"src", "app", "lib", "packages", "modules", "web", "public", "sites"}


def module_of(path: str) -> str:
    """Groups a file into a logical module: its top directories, or root."""
    parts = PurePosixPath(path).parts[:-1]
    if not parts:
        return "(root)"
    if parts[0] in _DEPTH_ROOTS and len(parts) > 1:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def find_entry_points(paths: list[str], declared: list[str]) -> list[str]:
    found = [p for p in declared if p in set(paths)]
    for path in sorted(paths, key=lambda p: (p.count("/"), p)):
        pure = PurePosixPath(path)
        if pure.name in _ENTRY_NAMES and pure.parts[0] not in ("tests", "test", "node_modules"):
            # A nested index file is usually a barrel of re-exports, not a place where a program starts.
            max_depth = 1 if pure.stem == "index" and pure.suffix != ".php" else 2
            if path not in found and path.count("/") <= max_depth:
                found.append(path)
    return found[:20]


def build_module_graph(files: dict[str, dict[str, Any]], edges: list[Edge]) -> dict[str, Any]:
    stats: dict[str, Counter[str]] = defaultdict(Counter)
    languages: dict[str, Counter[str]] = defaultdict(Counter)
    for path, info in files.items():
        module = module_of(path)
        stats[module]["files"] += 1
        stats[module]["loc"] += info["loc"]
        languages[module][info["language"]] += info["loc"]
    links: Counter[tuple[str, str]] = Counter()
    for edge in edges:
        source, target = module_of(edge.source_path), module_of(edge.target_path)
        if source != target:
            links[(source, target)] += 1
    modules = [
        {
            "name": name,
            "files": counts["files"],
            "loc": counts["loc"],
            "language": languages[name].most_common(1)[0][0] if languages[name] else None,
        }
        for name, counts in sorted(stats.items(), key=lambda kv: -kv[1]["loc"])
    ]
    return {
        "modules": modules,
        "links": [{"source": s, "target": t, "weight": w} for (s, t), w in links.most_common(500)],
    }


def describe_layers(roles: Counter[str]) -> list[dict[str, Any]]:
    order = [
        "route",
        "controller",
        "middleware",
        "service",
        "model",
        "view",
        "migration",
        "module",
        "config",
        "test",
        "example",
        "source",
    ]
    labels = {
        "route": "Routes",
        "controller": "Controllers",
        "middleware": "Middleware",
        "service": "Services",
        "model": "Models",
        "view": "Views and templates",
        "migration": "Migrations and schema",
        "module": "Drupal modules and themes",
        "config": "Configuration",
        "test": "Tests",
        "example": "Examples and docs",
        "source": "Other source",
    }
    return [{"role": r, "label": labels[r], "files": roles[r]} for r in order if roles[r]]
