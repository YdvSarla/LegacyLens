"""Manifest parsing, framework detection and config-file based extraction."""

from __future__ import annotations

import json
import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import yaml

from app.services.analysis.parsed import EndpointRef, ModelRef

logger = logging.getLogger("legacylens.analysis")


@dataclass
class PackageRef:
    name: str
    version: str | None
    ecosystem: str
    manifest_path: str
    is_dev: bool = False


@dataclass
class ManifestInfo:
    packages: list[PackageRef] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    php_version: str | None = None
    node_engine: str | None = None
    python_version: str | None = None


_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$")


def parse_composer(content: str, path: str) -> ManifestInfo:
    info = ManifestInfo()
    data = json.loads(content)
    for section, is_dev in (("require", False), ("require-dev", True)):
        for name, version in (data.get(section) or {}).items():
            if name == "php":
                info.php_version = str(version)
            elif not name.startswith("ext-"):
                info.packages.append(PackageRef(name, str(version), "composer", path, is_dev))
    return info


def parse_package_json(content: str, path: str) -> ManifestInfo:
    info = ManifestInfo()
    data = json.loads(content)
    for section, is_dev in (("dependencies", False), ("devDependencies", True)):
        for name, version in (data.get(section) or {}).items():
            info.packages.append(PackageRef(name, str(version), "npm", path, is_dev))
    main = data.get("main")
    directory = str(PurePosixPath(path).parent)
    prefix = "" if directory == "." else f"{directory}/"
    if isinstance(main, str):
        info.entry_points.append(prefix + main.lstrip("./"))
    binary = data.get("bin")
    if isinstance(binary, str):
        info.entry_points.append(prefix + binary.lstrip("./"))
    elif isinstance(binary, dict):
        info.entry_points.extend(prefix + str(v).lstrip("./") for v in binary.values())
    engines = data.get("engines") or {}
    if isinstance(engines, dict) and engines.get("node"):
        info.node_engine = str(engines["node"])
    return info


def parse_requirements(content: str, path: str) -> ManifestInfo:
    info = ManifestInfo()
    is_dev = "dev" in PurePosixPath(path).name.lower() or "test" in PurePosixPath(path).name.lower()
    for raw in content.splitlines():
        cleaned = raw.split("#", 1)[0].strip()
        if not cleaned or cleaned.startswith(("-", "git+", "http://", "https://")):
            continue
        match = _REQUIREMENT_RE.match(cleaned.split(";", 1)[0].strip())
        if match:
            info.packages.append(PackageRef(match.group(1), match.group(3).strip() or None, "pip", path, is_dev))
    return info


def parse_pyproject(content: str, path: str) -> ManifestInfo:
    info = ManifestInfo()
    data = tomllib.loads(content)
    project = data.get("project") or {}
    info.python_version = project.get("requires-python")

    def add(requirement: str, is_dev: bool) -> None:
        match = _REQUIREMENT_RE.match(requirement.split(";", 1)[0].strip())
        if match:
            info.packages.append(PackageRef(match.group(1), match.group(3).strip() or None, "pip", path, is_dev))

    for requirement in project.get("dependencies") or []:
        add(str(requirement), False)
    for group in (project.get("optional-dependencies") or {}).values():
        for requirement in group:
            add(str(requirement), True)
    poetry = (data.get("tool") or {}).get("poetry") or {}
    for name, version in (poetry.get("dependencies") or {}).items():
        if name.lower() != "python":
            info.packages.append(PackageRef(name, version if isinstance(version, str) else None, "pip", path, False))
    return info


MANIFEST_PARSERS = {
    "composer.json": parse_composer,
    "package.json": parse_package_json,
    "pyproject.toml": parse_pyproject,
}


def parse_manifest(path: str, content: str) -> ManifestInfo | None:
    name = PurePosixPath(path).name
    try:
        if name in MANIFEST_PARSERS:
            return MANIFEST_PARSERS[name](content, path)
        if name.startswith("requirements") and name.endswith((".txt", ".in")):
            return parse_requirements(content, path)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, AttributeError, TypeError) as exc:
        logger.info("Could not parse manifest %s: %s", path, exc)
    return None


# Frameworks -----------------------------------------------------------------
_PACKAGE_FRAMEWORKS = {
    "composer": {
        "laravel/framework": "Laravel",
        "laravel/lumen-framework": "Lumen",
        "drupal/core": "Drupal",
        "drupal/core-recommended": "Drupal",
        "symfony/framework-bundle": "Symfony",
        "codeigniter4/framework": "CodeIgniter",
    },
    "npm": {
        "express": "Express",
        "@nestjs/core": "NestJS",
        "next": "Next.js",
        "react": "React",
        "vue": "Vue",
        "@angular/core": "Angular",
        "fastify": "Fastify",
        "koa": "Koa",
        "mongoose": "Mongoose",
        "sequelize": "Sequelize",
        "typeorm": "TypeORM",
        "prisma": "Prisma",
    },
    "pip": {
        "django": "Django",
        "flask": "Flask",
        "fastapi": "FastAPI",
        "sqlalchemy": "SQLAlchemy",
        "celery": "Celery",
    },
}


def detect_frameworks(paths: set[str], packages: list[PackageRef]) -> list[str]:
    found: set[str] = set()
    for package in packages:
        framework = _PACKAGE_FRAMEWORKS.get(package.ecosystem, {}).get(package.name.lower())
        if framework:
            found.add(framework)
    names = {PurePosixPath(p).name for p in paths}
    if "artisan" in names:
        found.add("Laravel")
    if "manage.py" in names:
        found.add("Django")
    if "core/lib/Drupal.php" in paths or any(p.endswith("/core/lib/Drupal.php") for p in paths):
        found.add("Drupal")
    if any(p.endswith(".info.yml") for p in paths) or any(p.endswith((".module", ".install")) for p in paths):
        found.add("Drupal")
    return sorted(found)


_CODE_FRAMEWORK_LABELS = {
    "flask": "Flask",
    "fastapi": "FastAPI",
    "django": "Django",
    "sqlalchemy": "SQLAlchemy",
    "sqlmodel": "SQLModel",
    "laravel": "Laravel",
    "laravel-migration": "Laravel",
    "eloquent": "Laravel",
    "drupal": "Drupal",
    "drupal-entity": "Drupal",
    "drupal-schema": "Drupal",
    "express": "Express",
    "nestjs": "NestJS",
    "mongoose": "Mongoose",
    "sequelize": "Sequelize",
    "typeorm": "TypeORM",
    "prisma": "Prisma",
}


def frameworks_from_code(names: set[str]) -> set[str]:
    """Frameworks seen in the code itself. Legacy manifests are often incomplete."""
    return {_CODE_FRAMEWORK_LABELS[name] for name in names if name in _CODE_FRAMEWORK_LABELS}


# Config based extraction ----------------------------------------------------
def parse_drupal_routing(content: str) -> list[EndpointRef]:
    """`*.routing.yml`: Drupal 8+ routes."""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    lines = content.splitlines()
    endpoints: list[EndpointRef] = []
    for route_name, route in data.items():
        if not isinstance(route, dict) or "path" not in route:
            continue
        defaults = route.get("defaults") or {}
        handler = next(
            (
                str(defaults[key])
                for key in ("_controller", "_form", "_entity_form", "_entity_list", "_entity_view")
                if key in defaults
            ),
            None,
        )
        methods = route.get("methods") or ["ANY"]
        requirements = route.get("requirements") or {}
        guards = [f"{k}: {v}" for k, v in requirements.items() if isinstance(k, str) and k.startswith("_")]
        line_number = next((i + 1 for i, text_line in enumerate(lines) if text_line.startswith(f"{route_name}:")), 1)
        for method in methods:
            endpoints.append(
                EndpointRef(
                    method=str(method).upper(),
                    path=str(route["path"]),
                    handler=handler,
                    framework="drupal",
                    line=line_number,
                    middleware=guards,
                )
            )
    return endpoints


_CREATE_TABLE_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"\[]?(\w+)[`\"\]]?\s*\((.*?)\)\s*(?:engine|default|comment|with|;|$)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)
_SQL_NON_COLUMNS = {
    "primary",
    "key",
    "unique",
    "index",
    "constraint",
    "foreign",
    "check",
    "fulltext",
    "spatial",
}


def parse_sql_models(content: str) -> list[ModelRef]:
    models: list[ModelRef] = []
    for match in _CREATE_TABLE_RE.finditer(content):
        table, body = match.group(1), match.group(2)
        fields: list[dict[str, str]] = []
        depth = 0
        current = ""
        parts: list[str] = []
        for char in body:
            depth += char == "("
            depth -= char == ")"
            if char == "," and depth == 0:
                parts.append(current)
                current = ""
            else:
                current += char
        parts.append(current)
        for part in parts:
            tokens = part.strip().split()
            if len(tokens) >= 2 and tokens[0].strip('`"[]').lower() not in _SQL_NON_COLUMNS:
                fields.append({"name": tokens[0].strip('`"[]'), "type": tokens[1].rstrip(",")})
        models.append(
            ModelRef(
                name=table,
                framework="sql",
                line=content.count("\n", 0, match.start()) + 1,
                table=table,
                fields=fields,
            )
        )
    return models


_PRISMA_MODEL_RE = re.compile(r"^model\s+(\w+)\s*\{(.*?)^\}", re.MULTILINE | re.DOTALL)


def parse_prisma_models(content: str) -> list[ModelRef]:
    models: list[ModelRef] = []
    for match in _PRISMA_MODEL_RE.finditer(content):
        fields: list[dict[str, str]] = []
        for raw in match.group(2).splitlines():
            parts = raw.strip().split()
            if len(parts) >= 2 and not parts[0].startswith(("@@", "//")):
                fields.append({"name": parts[0], "type": parts[1]})
        models.append(
            ModelRef(
                name=match.group(1),
                framework="prisma",
                line=content.count("\n", 0, match.start()) + 1,
                fields=fields,
            )
        )
    return models
