"""Runs generated tests inside locked-down, throwaway Docker containers.

Test code is untrusted: it comes from a language model and imports repository code
that may itself be hostile. The protections, all enforced by Docker:

- No network while tests run (`--network none`).
- Read-only root filesystem. The workspace and /tmp are size-limited memory disks, so a
  test cannot fill the server's disk. Their contents count against the memory limit.
- All Linux capabilities dropped, no privilege escalation, non-root user.
- Memory, CPU, process count, open file and file size limits, plus a hard timeout.
- Container logs are off, so endless output cannot fill the Docker engine's disk.
- No host bind mounts. Code enters through stdin as a tar stream, so a container can
  never see the host filesystem, the Docker socket or LegacyLens' secrets.
- No secrets in the environment. Files that look like secrets are left out of the tar.

Installing dependencies is optional. It is the only step with network access, it runs
with install scripts disabled, and it never runs the tests. Because it needs a volume
that outlives one container, the tests then see that volume as read-only.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from defusedxml import ElementTree

from app.core.config import Settings
from app.core.errors import ValidationFailed
from app.services.analysis.scanner import is_sensitive

logger = logging.getLogger("legacylens.sandbox")

_MARK_BEGIN = "__LEGACYLENS_JUNIT_BEGIN__"
_MARK_END = "__LEGACYLENS_JUNIT_END__"
_EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "vendor",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".cache",
    ".pytest_cache",
    "dist",
    "build",
    "coverage",
}
_DOCKER_ENV_KEYS = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_CONTEXT")
_WORKSPACE = "/workspace"
_MAX_COPIED_FILE_BYTES = 8 * 1024 * 1024
# Keeps room for the tests themselves, because the memory disk shares the memory limit.
_WORKSPACE_MEMORY_SHARE = 0.6
_TSX_LOADER = "/usr/local/lib/node_modules/tsx/dist/loader.mjs"

_TEST_SCRIPTS = {
    "python": (
        'python -m pytest -q --no-header -p no:cacheprovider --rootdir=/workspace --junitxml=/tmp/junit.xml "$1"'
    ),
    "javascript": (
        "node --test --test-reporter=spec --test-reporter-destination=stdout "
        '--test-reporter=junit --test-reporter-destination=/tmp/junit.xml "$1"'
    ),
    # tsx runs real-world TypeScript, including extensionless imports and decorators.
    "typescript": (
        f"node --import {_TSX_LOADER} --test --test-reporter=spec --test-reporter-destination=stdout "
        '--test-reporter=junit --test-reporter-destination=/tmp/junit.xml "$1"'
    ),
    "php": (
        'BOOT=""; if [ -f vendor/autoload.php ]; then BOOT="--bootstrap vendor/autoload.php"; fi; '
        'phpunit --do-not-cache-result --no-configuration $BOOT --log-junit /tmp/junit.xml "$1"'
    ),
}
_INSTALL_SCRIPTS = {
    # Wheels only: a source distribution would run setup.py during install.
    "python": (
        "if [ -f requirements.txt ]; then "
        "pip install --no-cache-dir --disable-pip-version-check --only-binary=:all: "
        "--target /workspace/.sandbox-deps -r requirements.txt; "
        'else echo "No requirements.txt, nothing to install."; fi'
    ),
    "javascript": (
        "if [ -f package.json ]; then npm install --ignore-scripts --no-audit --no-fund "
        '--cache /tmp/npm; else echo "No package.json, nothing to install."; fi'
    ),
    "php": (
        "if [ -f composer.json ]; then composer install --no-scripts --no-plugins --no-interaction "
        '--no-progress --ignore-platform-reqs; else echo "No composer.json, nothing to install."; fi'
    ),
}
_INSTALL_SCRIPTS["typescript"] = _INSTALL_SCRIPTS["javascript"]


class SandboxUnavailable(Exception):
    pass


@dataclass
class SandboxResult:
    status: str  # passed | failed | error | timeout
    exit_code: int | None
    output: str
    results: dict[str, Any] | None
    duration_seconds: float


def build_workspace_tar(repo: Path, extra_files: dict[str, str], destination: Path, max_bytes: int) -> None:
    """Project code plus test files, without history, dependencies, secrets or symlinks."""

    def normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 1000
        info.uname = info.gname = ""
        info.mode = 0o755 if info.isdir() else 0o644
        return info

    total = 0
    with tarfile.open(destination, "w") as bundle:
        for directory, subdirs, names in os.walk(repo, followlinks=False):
            subdirs[:] = [
                d for d in subdirs if d not in _EXCLUDED_DIRS and not os.path.islink(os.path.join(directory, d))
            ]
            for name in names:
                absolute = os.path.join(directory, name)
                relative = os.path.relpath(absolute, repo).replace(os.sep, "/")
                if os.path.islink(absolute) or not os.path.isfile(absolute) or is_sensitive(relative):
                    continue
                size = os.path.getsize(absolute)
                if size > _MAX_COPIED_FILE_BYTES:
                    continue  # Large assets rarely matter to tests, and they use up the memory disk.
                total += size
                if total > max_bytes:
                    raise ValidationFailed("The project is too large to copy into the sandbox.")
                bundle.add(absolute, arcname=relative, recursive=False, filter=normalize)
        for relative, content in extra_files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(relative)
            info.size = len(data)
            info.mtime = int(time.time())
            normalize(info)
            bundle.addfile(info, io.BytesIO(data))


def parse_junit(xml_text: str) -> dict[str, Any] | None:
    try:
        root = ElementTree.fromstring(xml_text)
    except Exception:  # noqa: BLE001
        return None
    cases: list[dict[str, Any]] = []
    for case in root.iter("testcase"):
        status = "passed"
        message = None
        for child in case:
            if child.tag in ("failure", "error"):
                status = "failed" if child.tag == "failure" else "error"
                message = (child.get("message") or child.text or "").strip()[:2000]
            elif child.tag == "skipped":
                status = "skipped"
                message = (child.get("message") or child.text or "").strip()[:500]
        cases.append(
            {
                "name": case.get("name"),
                "classname": case.get("classname"),
                "time": float(case.get("time") or 0),
                "status": status,
                "message": message,
            }
        )
    counts = {key: sum(1 for c in cases if c["status"] == key) for key in ("passed", "failed", "error", "skipped")}
    return {"total": len(cases), **counts, "cases": cases[:200]}


def split_output(output: str) -> tuple[str, str | None]:
    begin = output.rfind(_MARK_BEGIN)
    end = output.rfind(_MARK_END)
    if begin == -1 or end == -1 or end < begin:
        return output, None
    return (
        output[:begin].rstrip() + output[end + len(_MARK_END) :].rstrip(),
        output[begin + len(_MARK_BEGIN) : end].strip() or None,
    )


class _BoundedReader(threading.Thread):
    """Drains a pipe forever but keeps only the head and tail, so hostile output cannot
    exhaust memory. The tail matters because the JUnit report is printed last."""

    def __init__(self, stream: IO[bytes], limit: int) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.head = bytearray()
        self.tail = bytearray()
        self.limit = limit
        self.dropped = 0

    def run(self) -> None:
        half = self.limit // 2
        while chunk := self.stream.read(65536):
            room = half - len(self.head)
            if room > 0:
                self.head.extend(chunk[:room])
                chunk = chunk[room:]
            if chunk:
                self.tail.extend(chunk)
                if len(self.tail) > half:
                    excess = len(self.tail) - half
                    self.dropped += excess
                    del self.tail[:excess]

    def text(self) -> str:
        joined = bytes(self.head)
        if self.dropped:
            joined += f"\n... {self.dropped} bytes of output were dropped ...\n".encode()
        joined += bytes(self.tail)
        return joined.decode("utf-8", "replace")


class DockerSandbox:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # Docker helpers ---------------------------------------------------------
    @staticmethod
    def _env() -> dict[str, str]:
        return {key: os.environ[key] for key in _DOCKER_ENV_KEYS if key in os.environ}

    def _docker(self, args: list[str], *, timeout: float = 60) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(["docker", *args], env=self._env(), capture_output=True, timeout=timeout, check=False)

    def image_for(self, language: str) -> str:
        images = {
            "python": self.settings.sandbox_image_python,
            "javascript": self.settings.sandbox_image_node,
            "typescript": self.settings.sandbox_image_node,
            "php": self.settings.sandbox_image_php,
        }
        if language not in images:
            raise ValidationFailed(f"Cannot run tests for language '{language}'.")
        return images[language]

    def check(self, language: str | None = None) -> None:
        if not self.settings.sandbox_enabled:
            raise SandboxUnavailable("The test sandbox is disabled (SANDBOX_ENABLED=false).")
        if shutil.which("docker") is None:
            raise SandboxUnavailable("The Docker CLI is not installed on the backend.")
        try:
            info = self._docker(["info", "--format", "{{.ServerVersion}}"], timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise SandboxUnavailable("The Docker daemon did not respond in time.") from exc
        if info.returncode != 0:
            raise SandboxUnavailable("The Docker daemon is not reachable from the backend.")
        if language is not None:
            image = self.image_for(language)
            inspected = self._docker(["image", "inspect", "--format", "{{.Id}}", image], timeout=15)
            if inspected.returncode != 0:
                raise SandboxUnavailable(f"The sandbox image '{image}' is missing. Build it with: make sandbox-images")

    def flags(self, *, name: str, network: str, workspace: list[str]) -> list[str]:
        """The security boundary. tests/test_regression.py pins every one of these flags."""
        settings = self.settings
        return [
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            "legacylens.sandbox=1",
            "--log-driver",
            "none",
            "--network",
            network,
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=256m,uid=1000,gid=1000,mode=1777",  # noqa: S108
            *workspace,
            "--workdir",
            _WORKSPACE,
            "--user",
            "1000:1000",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--ipc",
            "private",
            "--pids-limit",
            str(settings.sandbox_pids_limit),
            "--memory",
            f"{settings.sandbox_memory_mb}m",
            "--memory-swap",
            f"{settings.sandbox_memory_mb}m",
            "--cpus",
            str(settings.sandbox_cpus),
            "--ulimit",
            "nofile=1024:1024",
            "--ulimit",
            "fsize=268435456:268435456",
            "--ulimit",
            "core=0:0",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONPATH=/workspace:/workspace/src:/workspace/.sandbox-deps",
            "--env",
            "COMPOSER_HOME=/tmp/composer",
            "--env",
            "NO_COLOR=1",
        ]

    def _memory_workspace(self) -> list[str]:
        size = self.settings.sandbox_memory_mb
        return ["--tmpfs", f"{_WORKSPACE}:rw,nosuid,nodev,size={size}m,uid=1000,gid=1000,mode=0755"]

    @staticmethod
    def _volume_workspace(volume: str, *, read_only: bool) -> list[str]:
        mount = f"type=volume,source={volume},target={_WORKSPACE}"
        return ["--mount", f"{mount},readonly" if read_only else mount]

    def _run_phase(
        self,
        name: str,
        args: list[str],
        *,
        timeout: float,
        stdin_path: Path | None = None,
    ) -> tuple[int | None, str, bool]:
        """Returns (exit code, output, timed out)."""
        stdin: IO[bytes] | int = subprocess.DEVNULL
        opened: IO[bytes] | None = None
        if stdin_path is not None:
            opened = stdin_path.open("rb")
            stdin = opened
        process = subprocess.Popen(
            ["docker", *args],
            env=self._env(),
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert process.stdout is not None
        reader = _BoundedReader(process.stdout, self.settings.sandbox_output_limit_kb * 1024)
        reader.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # `docker run` exiting does not stop the container, so kill it by name.
            self._docker(["kill", name], timeout=30)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        finally:
            if opened is not None:
                opened.close()
        reader.join(timeout=10)
        return process.returncode, reader.text(), timed_out

    # Main flow --------------------------------------------------------------
    def run_tests(
        self,
        language: str,
        repo: Path,
        test_path: str,
        test_code: str,
        *,
        install_dependencies: bool,
    ) -> SandboxResult:
        self.check(language)
        started = time.monotonic()
        image = self.image_for(language)
        run_id = uuid.uuid4().hex[:16]
        with tempfile.TemporaryDirectory(prefix="legacylens-sandbox-") as scratch:
            archive = Path(scratch) / "workspace.tar"
            build_workspace_tar(repo, {test_path: test_code}, archive, self.settings.max_extracted_mb * 1024 * 1024)
            if install_dependencies:
                return self._run_with_install(image, language, run_id, archive, test_path, started)
            return self._run_in_memory(image, language, run_id, archive, test_path, started)

    def _run_in_memory(
        self, image: str, language: str, run_id: str, archive: Path, test_path: str, started: float
    ) -> SandboxResult:
        """The default path: one container, no network, and only memory disks to write to."""
        limit = int(self.settings.sandbox_memory_mb * 1024 * 1024 * _WORKSPACE_MEMORY_SHARE)
        if archive.stat().st_size > limit:
            raise ValidationFailed(
                "The project is too large for the sandbox memory limit. Raise SANDBOX_MEMORY_MB to run its tests."
            )
        name = f"legacylens-test-{run_id}"
        script = f"tar -xf - -C {_WORKSPACE} || exit 3\n{self._test_script(language)}"
        code, output, timed_out = self._run_phase(
            name,
            [
                *self.flags(name=name, network="none", workspace=self._memory_workspace()),
                "--interactive",
                image,
                "sh",
                "-c",
                script,
                "sh",
                test_path,
            ],
            timeout=self.settings.sandbox_timeout_seconds,
            stdin_path=archive,
        )
        if code == 3:
            return SandboxResult(
                "error",
                code,
                f"Could not copy the project into the sandbox.\n{output}",
                None,
                time.monotonic() - started,
            )
        return self._result(code, output, timed_out, [], started)

    def _run_with_install(
        self, image: str, language: str, run_id: str, archive: Path, test_path: str, started: float
    ) -> SandboxResult:
        volume = f"legacylens-run-{run_id}"
        created = self._docker(["volume", "create", "--label", "legacylens.sandbox=1", volume])
        if created.returncode != 0:
            raise SandboxUnavailable("Could not create the sandbox volume.")
        logs: list[str] = []
        try:
            writable = self._volume_workspace(volume, read_only=False)
            prepare = f"legacylens-prep-{run_id}"
            code, output, timed_out = self._run_phase(
                prepare,
                [
                    *self.flags(name=prepare, network="none", workspace=writable),
                    "--interactive",
                    image,
                    "tar",
                    "-xf",
                    "-",
                    "-C",
                    _WORKSPACE,
                ],
                timeout=180,
                stdin_path=archive,
            )
            if code != 0 or timed_out:
                return SandboxResult(
                    "error",
                    code,
                    f"Could not copy the project into the sandbox.\n{output}",
                    None,
                    time.monotonic() - started,
                )
            phase = f"legacylens-inst-{run_id}"
            code, output, timed_out = self._run_phase(
                phase,
                [
                    *self.flags(name=phase, network="bridge", workspace=writable),
                    image,
                    "sh",
                    "-c",
                    _INSTALL_SCRIPTS[language],
                ],
                timeout=self.settings.sandbox_install_timeout_seconds,
            )
            logs.append(f"$ Installing dependencies (network on, install scripts disabled)\n{output}")
            if timed_out:
                logs.append("Dependency install timed out. Tests will run without them.")
            elif code != 0:
                logs.append(f"Dependency install exited with code {code}. Tests will still run.")
            phase = f"legacylens-test-{run_id}"
            code, output, timed_out = self._run_phase(
                phase,
                [
                    *self.flags(name=phase, network="none", workspace=self._volume_workspace(volume, read_only=True)),
                    image,
                    "sh",
                    "-c",
                    self._test_script(language),
                    "sh",
                    test_path,
                ],
                timeout=self.settings.sandbox_timeout_seconds,
            )
            return self._result(code, output, timed_out, logs, started)
        finally:
            self._docker(["volume", "rm", "--force", volume], timeout=60)

    @staticmethod
    def _test_script(language: str) -> str:
        return (
            f"{_TEST_SCRIPTS[language]}\nrc=$?\n"
            f"echo; echo {_MARK_BEGIN}; cat /tmp/junit.xml 2>/dev/null; echo; echo {_MARK_END}\nexit $rc"
        )

    def _result(self, code: int | None, output: str, timed_out: bool, logs: list[str], started: float) -> SandboxResult:
        duration = time.monotonic() - started
        console, junit = split_output(output)
        joined = "\n\n".join([*logs, f"$ Running tests (network off)\n{console}"])
        results = parse_junit(junit) if junit else None
        if timed_out:
            return SandboxResult(
                "timeout",
                code,
                joined + f"\n\nStopped after the {self.settings.sandbox_timeout_seconds} second limit.",
                results,
                duration,
            )
        if code == 137:
            joined += "\n\nThe container was killed, most likely because it reached the memory limit."
        return SandboxResult(_status(code, results), code, joined, results, duration)


def _status(code: int | None, results: dict[str, Any] | None) -> str:
    if results is None or results["total"] == 0:
        return "error"  # Nothing ran. A run with no tests must never look green.
    if results["failed"] or results["error"] or code != 0:
        return "failed"
    return "passed" if results["passed"] > 0 else "error"  # Skipped tests alone prove nothing.
