"""Brings code into LegacyLens from GitHub or an uploaded ZIP. Both inputs are untrusted."""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from app.core.config import Settings
from app.core.errors import TooLargeError, ValidationFailed
from app.services import git

logger = logging.getLogger("legacylens.ingest")

# Only github.com over HTTPS. This also blocks requests to internal hosts (SSRF)
# and local paths, because nothing else ever reaches `git clone`.
_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)
_SKIPPED_ZIP_DIRS = {".git", "__MACOSX", ".hg", ".svn"}
_READ_CHUNK = 1024 * 256
_STRUCTURAL_DIRS = {
    "src",
    "app",
    "lib",
    "sites",
    "modules",
    "themes",
    "web",
    "public",
    "public_html",
    "htdocs",
    "www",
    "html",
    "docroot",
    "includes",
    "core",
    "tests",
    "packages",
    "backend",
    "server",
}


def parse_github_url(url: str) -> tuple[str, str]:
    match = _GITHUB_URL_RE.match(url.strip())
    if not match or match.group("repo") in (".", ".."):
        raise ValidationFailed("Enter a GitHub repository URL like https://github.com/owner/repository.")
    return match.group("owner"), match.group("repo")


def normalized_github_url(url: str) -> str:
    owner, repo = parse_github_url(url)
    return f"https://github.com/{owner}/{repo}.git"


def check_repository_size(owner: str, repo: str, settings: Settings, token: str | None) -> None:
    """Rejects oversized repositories before cloning, when GitHub reports a size."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers, timeout=10)
    except httpx.HTTPError as exc:
        logger.info("Could not read repository size: %s", exc)
        return
    if response.status_code == 404:
        raise ValidationFailed("Repository not found. For a private repository, provide an access token.")
    if response.status_code != 200:
        return  # Rate limited, for example. The size is checked again after cloning.
    size_mb = int(response.json().get("size", 0)) / 1024
    if size_mb > settings.max_repo_mb:
        raise TooLargeError(f"This repository is about {size_mb:.0f} MB. The limit is {settings.max_repo_mb} MB.")


def directory_size(path: Path) -> int:
    total = 0
    for directory, _, names in os.walk(path, followlinks=False):
        for name in names:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                pass
    return total


def _safe_member_path(name: str) -> PurePosixPath | None:
    """Validated relative path. Raises on traversal. None means skip this entry."""
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValidationFailed(f"The archive contains an absolute path: {name[:200]}")
    if ".." in pure.parts:
        raise ValidationFailed(f"The archive contains a path traversal entry: {name[:200]}")
    if not pure.parts or set(pure.parts) & _SKIPPED_ZIP_DIRS:
        return None
    return pure


def extract_zip(archive: Path, destination: Path, settings: Settings) -> None:
    """Extracts with protection against zip slip, symlinks and decompression bombs.

    Sizes in ZIP headers can lie, so the byte limit is enforced while writing.
    A `.git` directory inside an archive is dropped: its config and hooks could run
    commands, and LegacyLens creates its own history for uploads.
    """
    if not zipfile.is_zipfile(archive):
        raise ValidationFailed("The uploaded file is not a valid ZIP archive.")
    max_bytes = settings.max_extracted_mb * 1024 * 1024
    written = 0
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > settings.max_archive_files:
            raise TooLargeError(f"The archive has {len(members)} entries. The limit is {settings.max_archive_files}.")
        for member in members:
            if member.flag_bits & 0x1:
                raise ValidationFailed("Password protected archives are not supported.")
            pure = _safe_member_path(member.filename)
            if pure is None:
                continue
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                continue  # A symlink could point outside the project.
            if (
                member.compress_size > 0
                and member.file_size > 1024 * 1024
                and member.file_size / member.compress_size > settings.max_compression_ratio
            ):
                raise TooLargeError("The archive looks like a decompression bomb and was rejected.")
            target = (root / pure).resolve()
            if not target.is_relative_to(root):
                raise ValidationFailed(f"Unsafe path in archive: {member.filename[:200]}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, target.open("wb") as output:
                while chunk := source.read(_READ_CHUNK):
                    written += len(chunk)
                    if written > max_bytes:
                        raise TooLargeError(f"The extracted archive exceeds the {settings.max_extracted_mb} MB limit.")
                    output.write(chunk)
            target.chmod(0o644)  # Nothing extracted is executable.
    _strip_single_top_directory(destination)


def _strip_single_top_directory(destination: Path) -> None:
    """Removes the wrapper folder of archives like `repo-main/`. A conventional source
    folder is real project structure, so `sites/` or `src/` stays in place."""
    entries = list(destination.iterdir())
    if len(entries) != 1 or not entries[0].is_dir() or entries[0].name.lower() in _STRUCTURAL_DIRS:
        return
    top = entries[0]
    holding = destination / f".__unwrap_{os.getpid()}"
    top.rename(holding)
    for child in holding.iterdir():
        child.rename(destination / child.name)
    holding.rmdir()


def clear_worktree(repo: Path) -> None:
    """Removes everything except LegacyLens' own `.git`, ready for a new snapshot."""
    for child in repo.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def import_github(repo_dir: Path, url: str, branch: str | None, token: str | None, settings: Settings) -> str:
    owner, name = parse_github_url(url)
    check_repository_size(owner, name, settings, token)
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        git.clone(
            normalized_github_url(url),
            repo_dir,
            branch=branch,
            token=token,
            timeout=settings.clone_timeout_seconds,
        )
    except git.GitError as exc:
        # Hide anything that may echo the URL with credentials.
        raise ValidationFailed(f"Could not clone the repository: {clean_git_error(str(exc))}") from exc
    if directory_size(repo_dir) > settings.max_repo_mb * 1024 * 1024:
        shutil.rmtree(repo_dir, ignore_errors=True)
        raise TooLargeError(f"The repository exceeds the {settings.max_repo_mb} MB limit.")
    return git.head_commit(repo_dir)


def clean_git_error(message: str) -> str:
    return re.sub(r"https://[^@\s]+@", "https://", message)


def import_zip(repo_dir: Path, archive: Path, settings: Settings, message: str) -> str:
    """Records a new snapshot as a commit. The current code is only replaced after the new
    archive has extracted cleanly, so a bad upload cannot destroy the existing project."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    staging = repo_dir.parent / f"{repo_dir.name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        extract_zip(archive, staging, settings)
        if not any(staging.iterdir()):
            raise ValidationFailed("The archive is empty.")
        clear_worktree(repo_dir)
        for child in staging.iterdir():
            child.rename(repo_dir / child.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return git.init_snapshot(repo_dir, message)
