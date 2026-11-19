"""Safe wrappers around the git command line.

Repository content is untrusted. Every invocation disables hooks and other
config-driven command execution, and user-supplied refs are validated first.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import ValidationFailed

logger = logging.getLogger("legacylens.git")

_SAFE_CONFIG = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.quotepath=off",
    "-c",
    "core.symlinks=false",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.sshCommand=/bin/false",
    "-c",
    "protocol.ext.allow=never",
    "-c",
    "protocol.file.allow=never",
    "-c",
    "submodule.recurse=false",
    "-c",
    "credential.helper=",
    "-c",
    "safe.directory=*",
)
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/~^-]{0,200}$")
_COMMITTER = {
    "GIT_AUTHOR_NAME": "LegacyLens",
    "GIT_AUTHOR_EMAIL": "legacylens@localhost",
    "GIT_COMMITTER_NAME": "LegacyLens",
    "GIT_COMMITTER_EMAIL": "legacylens@localhost",
}


class GitError(Exception):
    pass


def _environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),  # noqa: S108
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/true",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "LC_ALL": "C",
        **_COMMITTER,
    }
    env.update(extra or {})
    return env


def run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 120,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> str:
    command = ["git", *_SAFE_CONFIG, *args]
    try:
        process = subprocess.run(
            command,
            cwd=cwd,
            env=_environment(env),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {int(timeout)} seconds.") from exc
    stdout = process.stdout.decode("utf-8", errors="replace")
    if check and process.returncode != 0:
        message = process.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(message[-500:] or f"git {args[0]} failed with code {process.returncode}.")
    return stdout


def validate_ref(ref: str) -> str:
    if not _REF_RE.match(ref) or ".." in ref:
        raise ValidationFailed(f"'{ref[:80]}' is not a valid commit or branch name.")
    return ref


def _token_environment(token: str | None) -> dict[str, str]:
    """Sends a token through the environment. It stays out of the process list and is
    never saved in the cloned repository's config."""
    if not token:
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


def clone(url: str, destination: Path, *, branch: str | None, token: str | None, timeout: float) -> None:
    # A full history of one branch, so commits can be compared later without network
    # access and without keeping a token for private repositories.
    args = ["clone", "--no-recurse-submodules", "--single-branch"]
    if branch:
        args += ["--branch", validate_ref(branch)]
    args += ["--", url, str(destination)]
    run_git(args, timeout=timeout, env=_token_environment(token))


def pull(repo: Path, *, token: str | None, timeout: float) -> None:
    run_git(
        ["pull", "--ff-only", "--no-recurse-submodules"],
        cwd=repo,
        timeout=timeout,
        env=_token_environment(token),
    )


def init_snapshot(repo: Path, message: str) -> str:
    """Tracks an uploaded ZIP in LegacyLens' own git repository so versions can be compared."""
    if not (repo / ".git").exists():
        run_git(["init", "--initial-branch=main"], cwd=repo)
    return commit_all(repo, message)


def commit_all(repo: Path, message: str) -> str:
    run_git(["add", "--all", "--force", "--", "."], cwd=repo, timeout=300)
    run_git(["commit", "--allow-empty", "--no-gpg-sign", "-m", message], cwd=repo, timeout=120)
    return head_commit(repo)


def head_commit(repo: Path) -> str:
    return run_git(["rev-parse", "HEAD"], cwd=repo).strip()


def current_branch(repo: Path) -> str | None:
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).strip()
    return None if branch == "HEAD" else branch


def resolve_commit(repo: Path, ref: str) -> str:
    validate_ref(ref)
    try:
        return run_git(["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"], cwd=repo).strip()
    except GitError as exc:
        raise ValidationFailed(f"Commit '{ref[:80]}' was not found in this repository.") from exc


@dataclass
class CommitInfo:
    sha: str
    short_sha: str
    author: str
    date: str
    subject: str


def list_commits(repo: Path, limit: int = 100) -> list[CommitInfo]:
    separator = "\x1f"
    output = run_git(
        ["log", f"--max-count={limit}", f"--format=%H{separator}%h{separator}%an{separator}%aI{separator}%s"],
        cwd=repo,
    )
    commits: list[CommitInfo] = []
    for line in output.splitlines():
        parts = line.split(separator)
        if len(parts) == 5:
            commits.append(CommitInfo(*parts))
    return commits


def show_file(repo: Path, commit: str, path: str, timeout: float = 60) -> str | None:
    """File content at a commit, or None when the file does not exist there."""
    try:
        return run_git(["show", f"{commit}:{path}"], cwd=repo, timeout=timeout)
    except GitError:
        return None
