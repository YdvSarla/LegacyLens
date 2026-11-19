"""Hostile input handling for uploads and repository URLs."""

import io
import stat
import zipfile

import pytest

from app.core.config import get_settings
from app.core.errors import TooLargeError, ValidationFailed
from app.services import git
from app.services.ingest import check_repository_size, extract_zip, normalized_github_url, parse_github_url


def _write_zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    return path


class TestGithubUrl:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://github.com/laravel/laravel", ("laravel", "laravel")),
            ("https://github.com/laravel/laravel.git", ("laravel", "laravel")),
            ("https://github.com/a-b/c.d_e/", ("a-b", "c.d_e")),
        ],
    )
    def test_valid(self, url, expected):
        assert parse_github_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "http://github.com/a/b",
            "https://github.com.evil.com/a/b",
            "https://evil.com/github.com/a/b",
            "https://user:pass@github.com/a/b",
            "git@github.com:a/b.git",
            "ssh://git@github.com/a/b",
            "file:///etc/passwd",
            "https://github.com/a",
            "https://github.com/a/b/c",
            "https://github.com/a/b?x=1",
            "https://github.com/../..",
            "https://169.254.169.254/latest/meta-data",
            "http://localhost:5432/a/b",
            "--upload-pack=touch /tmp/x",
            "ext::sh -c touch /tmp/x",
        ],
    )
    def test_rejected(self, url):
        with pytest.raises(ValidationFailed):
            parse_github_url(url)

    def test_normalized(self):
        assert normalized_github_url("https://github.com/a/b/") == "https://github.com/a/b.git"


class TestZip:
    def test_extracts_and_unwraps_single_top_directory(self, tmp_path):
        archive = _write_zip(tmp_path / "a.zip", {"proj-main/a.php": "<?php", "proj-main/sub/b.php": "<?php"})
        out = tmp_path / "out"
        extract_zip(archive, out, get_settings())
        assert (out / "a.php").exists()
        assert (out / "sub/b.php").exists()
        assert not (out / "proj-main").exists()

    @pytest.mark.parametrize("folder", ["sites", "src", "app"])
    def test_structural_top_directory_is_kept(self, tmp_path, folder):
        archive = _write_zip(tmp_path / "a.zip", {f"{folder}/a.php": "<?php"})
        out = tmp_path / "out"
        extract_zip(archive, out, get_settings())
        assert (out / folder / "a.php").exists()

    @pytest.mark.parametrize(
        "name", ["../evil.php", "a/../../evil.php", "/etc/evil.php", "C:/evil.php", "..\\evil.php"]
    )
    def test_zip_slip_is_rejected(self, tmp_path, name):
        archive = _write_zip(tmp_path / "a.zip", {name: "x", "ok.php": "<?php"})
        with pytest.raises(ValidationFailed):
            extract_zip(archive, tmp_path / "out", get_settings())
        assert not (tmp_path / "evil.php").exists()

    def test_symlink_entries_are_skipped(self, tmp_path):
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            link = zipfile.ZipInfo("link.php")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(link, "/etc/passwd")
            bundle.writestr("keep.php", "<?php")
            bundle.writestr("other.php", "<?php")
        out = tmp_path / "out"
        extract_zip(archive, out, get_settings())
        assert not (out / "link.php").exists()
        assert (out / "keep.php").exists()

    def test_embedded_git_directory_is_dropped(self, tmp_path):
        archive = _write_zip(
            tmp_path / "a.zip",
            {
                ".git/config": "[core]\nfsmonitor = touch /tmp/pwned\n",
                ".git/hooks/post-checkout": "x",
                "a.php": "<?php",
                "b.php": "<?php",
            },
        )
        out = tmp_path / "out"
        extract_zip(archive, out, get_settings())
        assert not (out / ".git").exists()
        assert (out / "a.php").exists()

    def test_extracted_files_are_not_executable(self, tmp_path):
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            info = zipfile.ZipInfo("run.sh")
            info.external_attr = 0o755 << 16
            bundle.writestr(info, "#!/bin/sh")
            bundle.writestr("b.php", "<?php")
        out = tmp_path / "out"
        extract_zip(archive, out, get_settings())
        assert not (out / "run.sh").stat().st_mode & 0o111

    def test_compression_bomb_is_rejected(self, tmp_path):
        archive = _write_zip(tmp_path / "a.zip", {"bomb.txt": "0" * (5 * 1024 * 1024), "b.php": "<?php"})
        with pytest.raises(TooLargeError):
            extract_zip(archive, tmp_path / "out", get_settings())

    def test_total_size_limit_is_enforced_while_writing(self, tmp_path):
        settings = get_settings().model_copy(update={"max_extracted_mb": 1, "max_compression_ratio": 10**9})
        archive = _write_zip(tmp_path / "a.zip", {"a.txt": "0" * (2 * 1024 * 1024), "b.php": "<?php"})
        with pytest.raises(TooLargeError):
            extract_zip(archive, tmp_path / "out", settings)

    def test_entry_count_limit(self, tmp_path):
        settings = get_settings().model_copy(update={"max_archive_files": 2})
        archive = _write_zip(tmp_path / "a.zip", {"a": "", "b": "", "c": ""})
        with pytest.raises(TooLargeError):
            extract_zip(archive, tmp_path / "out", settings)

    def test_not_a_zip(self, tmp_path):
        path = tmp_path / "a.zip"
        path.write_bytes(b"this is not a zip")
        with pytest.raises(ValidationFailed):
            extract_zip(path, tmp_path / "out", get_settings())

    def test_encrypted_zip_rejected(self, tmp_path):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("a.php", "<?php")
            bundle.writestr("b.php", "<?php")
        raw = bytearray(buffer.getvalue())
        # Set the encrypted flag in the central directory, as an encrypted archive would.
        marker = raw.find(b"PK\x01\x02")
        raw[marker + 8] |= 0x1
        path = tmp_path / "e.zip"
        path.write_bytes(raw)
        with pytest.raises(ValidationFailed):
            extract_zip(path, tmp_path / "out", get_settings())


class TestGitRefs:
    @pytest.mark.parametrize("ref", ["main", "feature/x", "v1.0", "abc123", "HEAD~1", "HEAD^"])
    def test_valid(self, ref):
        assert git.validate_ref(ref) == ref

    @pytest.mark.parametrize("ref", ["--upload-pack=x", "-x", "a b", "a;b", "a$(x)", "a..b", "", "a\nb", "`x`", "a|b"])
    def test_invalid(self, ref):
        with pytest.raises(ValidationFailed):
            git.validate_ref(ref)

    def test_repository_hooks_do_not_run(self, tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        (repo / "a.txt").write_text("1")
        git.init_snapshot(repo, "first")
        marker = tmp_path / "pwned"
        hook = repo / ".git/hooks/pre-commit"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
        (repo / "a.txt").write_text("2")
        git.commit_all(repo, "second")
        assert not marker.exists()


class TestGithubImport:
    @pytest.fixture
    def fake_clone(self, monkeypatch):
        """Stands in for the network: makes a real local repository with one Python file."""
        calls = {}

        def clone(url, destination, *, branch, token, timeout):
            calls.update(url=url, branch=branch, token=token)
            destination.mkdir(parents=True)
            (destination / "app.py").write_text("def main():\n    return 1\n")
            git.init_snapshot(destination, "Imported")

        monkeypatch.setattr(git, "clone", clone)
        monkeypatch.setattr("app.services.ingest.check_repository_size", lambda *a, **k: None)
        return calls

    def test_import_analyzes_and_never_stores_the_token(self, client, auth, fake_clone):
        from sqlalchemy import text

        from app.db.session import get_engine

        secret = "ghp_thisIsAVerySecretTokenValue000000000"
        response = client.post(
            "/api/projects/github",
            headers=auth,
            json={"url": "https://github.com/acme/legacy/", "branch": "main", "token": secret},
        )
        assert response.status_code == 202, response.text
        assert secret not in response.text
        project = client.get(f"/api/projects/{response.json()['id']}", headers=auth).json()
        assert project["status"] == "ready", project
        assert project["name"] == "acme/legacy"
        assert project["source_url"] == "https://github.com/acme/legacy.git"
        assert fake_clone == {"url": "https://github.com/acme/legacy.git", "branch": "main", "token": secret}
        with get_engine().connect() as connection:
            for table in ("projects", "jobs"):
                rows = connection.execute(text(f"SELECT * FROM {table}")).fetchall()
                assert rows and secret not in str(rows)

    def test_clone_failure_is_reported_without_credentials(self, client, auth, monkeypatch):
        def clone(*args, **kwargs):
            raise git.GitError("fatal: unable to access 'https://user:ghp_secret@github.com/a/b.git/': 403")

        monkeypatch.setattr(git, "clone", clone)
        monkeypatch.setattr("app.services.ingest.check_repository_size", lambda *a, **k: None)
        created = client.post("/api/projects/github", headers=auth, json={"url": "https://github.com/a/b"}).json()
        project = client.get(f"/api/projects/{created['id']}", headers=auth).json()
        assert project["status"] == "failed"
        assert "ghp_secret" not in project["error"]
        assert "Could not clone" in project["error"]

    def test_sync_pulls_and_reanalyzes(self, client, auth, fake_clone, monkeypatch):
        created = client.post("/api/projects/github", headers=auth, json={"url": "https://github.com/a/b"}).json()

        def pull(repo, *, token, timeout):
            (repo / "new.py").write_text("def added():\n    return 2\n")
            git.commit_all(repo, "New commit")

        monkeypatch.setattr(git, "pull", pull)
        base = f"/api/projects/{created['id']}"
        assert client.post(f"{base}/sync", headers=auth, json={}).status_code == 202
        assert client.get(f"{base}/symbols?q=added", headers=auth).json()["total"] == 1
        assert len(client.get(f"{base}/commits", headers=auth).json()) == 2

    def test_token_is_not_passed_on_the_command_line(self):
        env = git._token_environment("abc")
        assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
        assert "abc" not in env["GIT_CONFIG_VALUE_0"], "only the encoded header is passed"
        assert git._token_environment(None) == {}

    def test_oversized_repository_is_refused_before_cloning(self):
        import httpx
        import respx

        with respx.mock:
            respx.get("https://api.github.com/repos/a/b").mock(
                return_value=httpx.Response(200, json={"size": 10 * 1024 * 1024})
            )
            with pytest.raises(TooLargeError):
                check_repository_size("a", "b", get_settings(), None)

    def test_unknown_repository_gets_a_clear_message(self):
        import httpx
        import respx

        with respx.mock:
            respx.get("https://api.github.com/repos/a/b").mock(return_value=httpx.Response(404))
            with pytest.raises(ValidationFailed, match="not found"):
                check_repository_size("a", "b", get_settings(), None)
