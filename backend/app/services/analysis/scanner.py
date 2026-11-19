"""Finds analyzable files in a repository and classifies them."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".next",
    ".nuxt",
    ".cache",
    ".tox",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "bower_components",
    "vendor",
    "venv",
    "env",
    "dist",
    "build",
    "coverage",
    "htmlcov",
    "tmp",
    "temp",
    "logs",
    "site-packages",
    "__MACOSX",
}
# Third-party or generated paths that are not the project's own code.
_IGNORED_PATH_PATTERNS = (
    "storage/framework/*",
    "storage/logs/*",
    "bootstrap/cache/*",
    "public/build/*",
    "public/vendor/*",
    "*/modules/contrib/*",
    "modules/contrib/*",
    "*/themes/contrib/*",
    "themes/contrib/*",
    "*/libraries/*/dist/*",
    "sites/*/files/*",
    "*/sites/*/files/*",
    "web/core/*",
    "docroot/core/*",
)
# Never indexed or sent to an AI provider. Secrets belong to the owner, not the prompt.
_SENSITIVE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "id_rsa",
    "id_dsa",
    "id_ed25519",
    "*.keystore",
    "credentials.json",
    "secrets.yml",
    "secrets.yaml",
    "settings.local.php",
    "settings.php",
    "wp-config.php",
    "configuration.php",
    "database.yml",
    "local.settings.php",
    "*.sql.gz",
    "auth.json",
    ".npmrc",
    ".pypirc",
    ".htpasswd",
)
_MINIFIED_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".chunk.js", ".map")

LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".php": "php",
    ".phtml": "php",
    ".php5": "php",
    ".php7": "php",
    ".module": "php",
    ".inc": "php",
    ".install": "php",
    ".theme": "php",
    ".profile": "php",
    ".engine": "php",
    ".twig": "twig",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".sql": "sql",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
    ".xml": "xml",
    ".sh": "shell",
    ".prisma": "prisma",
}
PARSED_LANGUAGES = {"python", "javascript", "typescript", "php"}
# Text files that help the assistant but have no parser.
INDEXED_ONLY_LANGUAGES = {"twig", "sql", "yaml", "markdown", "shell", "prisma", "html"}
_LOCKFILES = {
    "package-lock.json",
    "composer.lock",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
}


@dataclass
class ScannedFile:
    path: str  # POSIX path relative to the repository root.
    absolute: Path
    language: str
    size: int
    role: str
    is_sensitive: bool


def is_sensitive(path: str) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch.fnmatch(name, pattern) for pattern in _SENSITIVE_PATTERNS)


# Code here demonstrates the project rather than being part of it.
_EXAMPLE_DIRS = {"examples", "example", "samples", "sample", "demo", "demos", "docs", "doc", "documentation", "stubs"}
NON_PRODUCTION_ROLES = frozenset({"test", "example"})


def classify_role(path: str) -> str:
    lower = path.lower()
    pure = PurePosixPath(lower)
    name = pure.name
    parts = set(pure.parts[:-1])
    if parts & _EXAMPLE_DIRS:
        return "example"
    if (
        parts & {"tests", "test", "__tests__", "spec", "specs"}
        or name.startswith("test_")
        or name.endswith(
            (
                "_test.py",
                "_test.php",
                "test.php",
                ".test.js",
                ".test.ts",
                ".test.tsx",
                ".spec.js",
                ".spec.ts",
                ".spec.tsx",
            )
        )
    ):
        return "test"
    if "migrations" in parts or "migration" in parts or name.endswith(".install"):
        return "migration"
    if (
        parts & {"routes", "routing"}
        or name in ("urls.py", "routes.py", "router.js", "router.ts")
        or name.endswith(".routing.yml")
    ):
        return "route"
    if (
        "controllers" in parts
        or "controller" in parts
        or name.endswith(("controller.php", "controller.js", "controller.ts", "views.py"))
    ):
        return "controller"
    if (
        "models" in parts
        or "entity" in parts
        or "entities" in parts
        or name in ("models.py", "schema.prisma")
        or name.endswith((".model.js", ".model.ts", ".entity.ts"))
    ):
        return "model"
    if parts & {"views", "templates", "resources/views"} or name.endswith(
        (".blade.php", ".twig", ".phtml", ".jsx", ".tsx", ".vue")
    ):
        return "view"
    if "middleware" in parts or "middlewares" in parts:
        return "middleware"
    if parts & {"services", "service"} or name.endswith((".service.ts", ".service.js", "service.php", ".services.yml")):
        return "service"
    if (
        parts & {"config", "conf", "settings"}
        or name in ("settings.py", "config.py", "config.js", "next.config.js")
        or name.endswith((".info.yml", ".yml", ".yaml", ".toml", ".json"))
    ):
        return "config"
    if name.endswith((".module", ".theme", ".profile")):
        return "module"
    return "source"


def _is_probably_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\0" in handle.read(4096)
    except OSError:
        return True


def _is_drupal_root(root: Path) -> bool:
    return (root / "core" / "lib" / "Drupal.php").exists()


def scan_repository(root: Path, *, max_file_bytes: int, max_files: int) -> list[ScannedFile]:
    files: list[ScannedFile] = []
    skip_core = _is_drupal_root(root)
    for directory, subdirs, names in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(directory, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        kept = []
        for sub in subdirs:
            child = f"{rel_dir}/{sub}" if rel_dir else sub
            if sub in _IGNORED_DIRS or (skip_core and child == "core"):
                continue
            if os.path.islink(os.path.join(directory, sub)):
                continue
            kept.append(sub)
        subdirs[:] = sorted(kept)
        for name in sorted(names):
            relative = f"{rel_dir}/{name}" if rel_dir else name
            absolute = Path(directory) / name
            if absolute.is_symlink() or not absolute.is_file():
                continue
            if name in _LOCKFILES or name.endswith(_MINIFIED_SUFFIXES):
                continue
            if any(fnmatch.fnmatch(relative, pattern) for pattern in _IGNORED_PATH_PATTERNS):
                continue
            language = _language_for(name)
            if language is None:
                continue
            try:
                size = absolute.stat().st_size
            except OSError:
                continue
            if size > max_file_bytes or _is_probably_binary(absolute):
                continue
            files.append(
                ScannedFile(
                    path=relative,
                    absolute=absolute,
                    language=language,
                    size=size,
                    role=classify_role(relative),
                    is_sensitive=is_sensitive(relative),
                )
            )
            if len(files) >= max_files:
                return files
    return files


def _language_for(name: str) -> str | None:
    if name in ("artisan", "drush"):
        return "php"
    if name in ("composer.json", "package.json"):
        return "json"
    if name.startswith("requirements") and name.endswith((".txt", ".in")):
        return "requirements"
    if name.endswith(".info"):
        return "drupal-info"
    return LANGUAGE_BY_EXTENSION.get(PurePosixPath(name).suffix.lower())


def count_loc(content: str) -> int:
    return sum(1 for raw in content.splitlines() if raw.strip())
