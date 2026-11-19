"""Test generation and the Docker sandbox. Classes marked `docker` run real containers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.services.sandbox.runner import (
    DockerSandbox,
    SandboxUnavailable,
    _BoundedReader,
    _status,
    build_workspace_tar,
    parse_junit,
    split_output,
)
from app.services.testgen import extract_code
from tests.test_llm import GEMINI_URL, GROQ_URL, OLLAMA_URL, gemini_ok, gemini_quota


def _docker_ready() -> bool:
    try:
        DockerSandbox(get_settings()).check("python")
        DockerSandbox(get_settings()).check("javascript")
        DockerSandbox(get_settings()).check("php")
    except (SandboxUnavailable, OSError):
        return False
    return True


needs_docker = pytest.mark.skipif(not _docker_ready(), reason="Docker or the sandbox images are not available")


def _base(project):
    return f"/api/projects/{project['id']}"


class TestSandboxFlags:
    """The security boundary as code. A change here should make someone look twice."""

    def test_isolation_flags(self):
        flags = DockerSandbox(get_settings()).flags(name="n", network="none", workspace=[])
        joined = " ".join(flags)
        for expected in (
            "--log-driver none",
            "--ipc private",
            "--network none",
            "--read-only",
            "--cap-drop ALL",
            "--security-opt no-new-privileges",
            "--user 1000:1000",
            "--pids-limit 256",
            "--memory 1024m",
            "--memory-swap 1024m",
            "--cpus 1.0",
            "--rm",
        ):
            assert expected in joined, expected

    def test_never_mounts_host_paths_or_the_docker_socket(self):
        sandbox = DockerSandbox(get_settings())
        flags = sandbox.flags(name="n", network="none", workspace=sandbox._memory_workspace())
        joined = " ".join(flags)
        assert "docker.sock" not in joined
        assert "--privileged" not in joined
        assert "-v " not in joined and "--volume" not in joined
        assert "type=bind" not in joined
        assert "/workspace:rw,nosuid,nodev,size=1024m" in joined, "the default workspace is a size-limited memory disk"
        assert sandbox._volume_workspace("v", read_only=True) == [
            "--mount",
            "type=volume,source=v,target=/workspace,readonly",
        ], "tests must not be able to write to a volume on disk"

    def test_no_secrets_in_container_environment(self):
        flags = DockerSandbox(get_settings()).flags(name="n", network="none", workspace=[])
        joined = " ".join(flags).upper()
        for secret in ("GEMINI", "GROQ", "SECRET_KEY", "DATABASE_URL", "POSTGRES"):
            assert secret not in joined

    def test_docker_cli_gets_no_application_secrets(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "abc")
        monkeypatch.setenv("DOCKER_HOST", "tcp://x")
        env = DockerSandbox._env()
        assert env["DOCKER_HOST"] == "tcp://x"
        assert "GEMINI_API_KEY" not in env and "DATABASE_URL" not in env


class TestWorkspace:
    def test_tar_excludes_secrets_history_dependencies_and_symlinks(self, tmp_path):
        import tarfile

        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git/config").write_text("x")
        (repo / "node_modules/a").mkdir(parents=True)
        (repo / "node_modules/a/i.js").write_text("x")
        (repo / "src").mkdir()
        (repo / "src/a.py").write_text("x")
        (repo / ".env").write_text("SECRET=1")
        (repo / "server.pem").write_text("key")
        (tmp_path / "outside").write_text("secret")
        (repo / "link").symlink_to(tmp_path / "outside")
        archive = tmp_path / "out.tar"
        build_workspace_tar(repo, {"legacylens_tests/t.py": "pass"}, archive, 10**7)
        with tarfile.open(archive) as bundle:
            names = set(bundle.getnames())
            members = bundle.getmembers()
        assert names == {"src/a.py", "legacylens_tests/t.py"}
        assert all(m.uid == 1000 for m in members)

    def test_tar_size_limit(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "big.txt").write_text("x" * 2000)
        with pytest.raises(ValidationFailed):
            build_workspace_tar(repo, {}, tmp_path / "o.tar", 1000)


class TestResults:
    def test_junit_counts(self):
        xml = """<testsuites><testsuite>
          <testcase name="a" classname="c" time="0.1"/>
          <testcase name="b"><failure message="boom">trace</failure></testcase>
          <testcase name="c"><error>bad</error></testcase>
          <testcase name="d"><skipped/></testcase>
        </testsuite></testsuites>"""
        results = parse_junit(xml)
        assert {k: results[k] for k in ("total", "passed", "failed", "error", "skipped")} == {
            "total": 4,
            "passed": 1,
            "failed": 1,
            "error": 1,
            "skipped": 1,
        }
        assert results["cases"][1]["message"] == "boom"

    def test_hostile_xml_is_refused(self):
        bomb = '<!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><testsuite><testcase name="&b;"/></testsuite>'
        assert parse_junit(bomb) is None
        assert parse_junit("not xml") is None

    def test_output_split(self):
        console, junit = split_output("hello\n__LEGACYLENS_JUNIT_BEGIN__\n<a/>\n__LEGACYLENS_JUNIT_END__\n")
        assert console == "hello"
        assert junit == "<a/>"
        assert split_output("plain") == ("plain", None)

    @pytest.mark.parametrize(
        "code,results,expected",
        [
            (0, {"total": 1, "passed": 1, "failed": 0, "error": 0}, "passed"),
            (1, {"total": 2, "passed": 1, "failed": 1, "error": 0}, "failed"),
            (1, {"total": 1, "passed": 1, "failed": 0, "error": 0}, "failed"),
            (0, {"total": 0, "passed": 0, "failed": 0, "error": 0}, "error"),
            (0, {"total": 1, "passed": 0, "failed": 0, "error": 0}, "error"),
            (0, None, "error"),
            (137, None, "error"),
        ],
    )
    def test_run_with_no_passing_tests_is_never_green(self, code, results, expected):
        assert _status(code, results) == expected

    def test_output_reader_is_bounded_and_keeps_the_tail(self):
        import io

        reader = _BoundedReader(io.BytesIO(b"A" * 100_000 + b"THE-END"), limit=1000)
        reader.run()
        text = reader.text()
        assert len(text) < 1100
        assert text.endswith("THE-END")
        assert "dropped" in text


class TestGeneration:
    def test_extract_code(self):
        assert extract_code("Here:\n```python\nprint(1)\n```\nDone") == "print(1)\n"
        assert extract_code("print(2)") == "print(2)\n"
        assert extract_code("   ") is None

    @respx.mock
    def test_ai_generation_saves_a_draft(self, client, upload, auth):
        project = upload("python_app")
        route = respx.post(GEMINI_URL).mock(return_value=gemini_ok("```python\ndef test_x():\n    assert True\n```"))
        response = client.post(
            f"{_base(project)}/tests/generate",
            headers=auth,
            json={"target_path": "shop/services/pricing.py", "symbol": "final_price"},
        )
        assert response.status_code == 201, response.text
        test = response.json()
        assert (test["source"], test["provider"], test["framework"]) == ("ai", "gemini", "pytest")
        assert test["code"] == "def test_x():\n    assert True\n"
        assert test["test_path"] == "legacylens_tests/test_shop_services_pricing.py"
        prompt = route.calls[0].request.content.decode()
        assert "final_price" in prompt and "import shop.services.pricing" in prompt

    @respx.mock
    def test_no_ai_available_falls_back_to_baseline(self, client, upload, auth):
        project = upload("python_app")
        respx.post(GEMINI_URL).mock(return_value=gemini_quota())
        respx.post(GROQ_URL).mock(return_value=httpx.Response(429, json={"error": {"message": "x"}}))
        respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("down"))
        test = client.post(
            f"{_base(project)}/tests/generate", headers=auth, json={"target_path": "shop/services/pricing.py"}
        ).json()
        assert test["source"] == "baseline"
        assert "baseline" in test["notice"]

    def test_rejects_unknown_and_unsupported_files(self, client, upload, auth):
        project = upload("python_app")
        for path in ("../../etc/passwd", "nope.py", "requirements.txt"):
            response = client.post(
                f"{_base(project)}/tests/generate", headers=auth, json={"target_path": path, "use_ai": False}
            )
            assert response.status_code == 422, path

    @respx.mock(assert_all_called=False)
    def test_secret_like_files_are_refused(self, client, upload, auth, respx_mock):
        project = upload(
            "plain_php", extra={"settings.local.php": "<?php $db_password = 'hunter2hunter2'; function f() {}"}
        )
        route = respx_mock.post(GEMINI_URL).mock(return_value=gemini_ok("```php\n<?php\n```"))
        response = client.post(
            f"{_base(project)}/tests/generate", headers=auth, json={"target_path": "settings.local.php"}
        )
        assert response.status_code == 422
        assert not route.called

    @respx.mock
    def test_secrets_are_removed_from_the_generation_prompt(self, client, upload, auth):
        project = upload("laravel_app")
        route = respx.post(GEMINI_URL).mock(return_value=gemini_ok("```php\n<?php\n```"))
        client.post(
            f"{_base(project)}/tests/generate", headers=auth, json={"target_path": "app/Services/BillingService.php"}
        )
        prompt = route.calls[0].request.content.decode()
        assert "hunter2hunter2" not in prompt
        assert "BillingService" in prompt

    def test_edit_marks_source(self, client, upload, auth):
        project = upload("python_app")
        test = client.post(
            f"{_base(project)}/tests/generate",
            headers=auth,
            json={"target_path": "shop/views.py", "use_ai": False},
        ).json()
        updated = client.put(
            f"{_base(project)}/tests/{test['id']}", headers=auth, json={"code": "def test_a():\n    pass\n"}
        )
        assert updated.json()["source"] == "edited"

    def test_test_paths_stay_inside_the_test_directory(self, client, upload, auth):
        project = upload("drupal_site")
        test = client.post(
            f"{_base(project)}/tests/generate",
            headers=auth,
            json={"target_path": "modules/custom/acme_reports/acme_reports.module", "use_ai": False},
        ).json()
        assert test["test_path"].startswith("legacylens_tests/")
        assert ".." not in test["test_path"]

    def test_run_of_another_users_test_is_hidden(self, client, upload, auth, register):
        project = upload("python_app")
        other = register("other@example.com")
        mine = upload("python_app", headers=other)
        test = client.post(
            f"{_base(project)}/tests/generate",
            headers=auth,
            json={"target_path": "shop/views.py", "use_ai": False},
        ).json()
        assert client.post(f"{_base(mine)}/tests/{test['id']}/run", headers=other).status_code == 404
        assert client.get(f"{_base(mine)}/tests/{test['id']}/runs", headers=other).status_code == 404

    def test_clear_message_when_sandbox_is_disabled(self, client, upload, auth, monkeypatch):
        project = upload("python_app")
        monkeypatch.setattr(get_settings(), "sandbox_enabled", False)
        test = client.post(
            f"{_base(project)}/tests/generate",
            headers=auth,
            json={"target_path": "shop/views.py", "use_ai": False},
        ).json()
        response = client.post(f"{_base(project)}/tests/{test['id']}/run", headers=auth)
        assert response.status_code == 503
        assert "disabled" in response.json()["error"]["message"]
        assert client.get(f"{_base(project)}/sandbox-status", headers=auth).json()["available"] is False


def _run_code(language: str, test_path: str, code: str, repo: Path, **kwargs):
    return DockerSandbox(get_settings()).run_tests(
        language, repo, test_path, code, install_dependencies=kwargs.get("install", False)
    )


@needs_docker
@pytest.mark.docker
class TestRealContainers:
    @pytest.fixture
    def repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "mod.py").write_text("def add(a, b):\n    return a + b\n")
        return repo

    def test_python_pass_and_fail(self, repo):
        passing = _run_code(
            "python", "legacylens_tests/test_a.py", "import mod\ndef test_a():\n    assert mod.add(1, 2) == 3\n", repo
        )
        assert passing.status == "passed", passing.output
        assert passing.results["passed"] == 1
        failing = _run_code(
            "python", "legacylens_tests/test_a.py", "import mod\ndef test_a():\n    assert mod.add(1, 2) == 4\n", repo
        )
        assert failing.status == "failed", failing.output
        assert failing.results["failed"] == 1

    def test_node_runner(self, tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        (repo / "m.js").write_text("exports.add = (a, b) => a + b;\n")
        code = (
            "const { test } = require('node:test');\nconst assert = require('node:assert');\n"
            "const m = require('../m.js');\ntest('adds', () => assert.equal(m.add(1, 2), 3));\n"
        )
        result = _run_code("javascript", "legacylens_tests/m.test.cjs", code, repo)
        assert result.status == "passed", result.output
        assert result.results["total"] == 1

    def test_typescript_runner_handles_real_world_imports(self, tmp_path):
        """Extensionless imports, interfaces and decorators, which plain Node cannot run."""
        repo = tmp_path / "r"
        (repo / "src").mkdir(parents=True)
        (repo / "src/price.ts").write_text(
            "export interface Item { price: number }\n"
            "export function total(items: Item[]): number { return items.reduce((s, i) => s + i.price, 0); }\n"
        )
        (repo / "src/service.ts").write_text(
            "import { total, Item } from './price';\n"
            "function Injectable(): ClassDecorator { return () => undefined; }\n"
            "@Injectable()\nexport class Cart { constructor(private items: Item[]) {}\n  sum(): number { return total(this.items); } }\n"
        )
        code = (
            "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
            "import { Cart } from '../src/service';\n"
            "test('sums prices', () => { assert.equal(new Cart([{ price: 2 }, { price: 3 }]).sum(), 5); });\n"
        )
        result = _run_code("typescript", "legacylens_tests/cart.test.ts", code, repo)
        assert result.status == "passed", result.output
        assert result.results["passed"] == 1

    def test_php_runner(self, tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        (repo / "m.php").write_text("<?php function add($a, $b) { return $a + $b; }\n")
        code = (
            "<?php\nuse PHPUnit\\Framework\\TestCase;\nrequire_once __DIR__ . '/../m.php';\n"
            "final class MTest extends TestCase { public function testAdd(): void { $this->assertSame(3, add(1, 2)); } }\n"
        )
        result = _run_code("php", "legacylens_tests/MTest.php", code, repo)
        assert result.status == "passed", result.output
        assert result.results["passed"] == 1

    def test_no_network_inside_the_container(self, repo):
        code = (
            "import socket\ndef test_net():\n"
            "    s = socket.socket()\n    s.settimeout(3)\n"
            "    try:\n        s.connect(('1.1.1.1', 53))\n    except OSError:\n        return\n"
            "    raise AssertionError('network is reachable')\n"
        )
        result = _run_code("python", "legacylens_tests/test_net.py", code, repo)
        assert result.status == "passed", result.output

    def test_runs_unprivileged_with_read_only_root(self, repo):
        code = (
            "import os\nimport pytest\n"
            "def test_identity():\n    assert os.getuid() == 1000\n"
            "def test_root_is_read_only():\n"
            "    with pytest.raises(OSError):\n        open('/usr/evil', 'w')\n"
            "def test_cannot_write_etc():\n"
            "    with pytest.raises(OSError):\n        open('/etc/passwd', 'a')\n"
            "def test_workspace_and_tmp_are_writable():\n"
            "    open('/tmp/a', 'w').close()\n    open('/workspace/a', 'w').close()\n"
            "def test_no_docker_socket():\n    assert not os.path.exists('/var/run/docker.sock')\n"
            "def test_no_secrets_in_environment():\n"
            "    names = [k for k in os.environ if 'KEY' in k or 'SECRET' in k or 'DATABASE' in k]\n"
            # GPG_KEY is the public release key fingerprint that the Python image sets.
            "    assert [k for k in names if k != 'GPG_KEY'] == []\n"
            "def test_no_capabilities():\n"
            "    status = open('/proc/self/status').read()\n"
            "    effective = [l for l in status.splitlines() if l.startswith('CapEff')][0].split()[1]\n"
            "    assert int(effective, 16) == 0\n"
        )
        result = _run_code("python", "legacylens_tests/test_sec.py", code, repo)
        assert result.status == "passed", result.output
        assert result.results["passed"] == 7

    def test_timeout_kills_the_container(self, repo):
        settings = get_settings().model_copy(update={"sandbox_timeout_seconds": 3})
        result = DockerSandbox(settings).run_tests(
            "python",
            repo,
            "legacylens_tests/test_h.py",
            "import time\ndef test_hang():\n    time.sleep(600)\n",
            install_dependencies=False,
        )
        assert result.status == "timeout"
        leftover = subprocess.run(
            ["docker", "ps", "--filter", "label=legacylens.sandbox=1", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert leftover.stdout.strip() == ""

    def test_memory_limit_stops_the_test(self, repo):
        settings = get_settings().model_copy(update={"sandbox_memory_mb": 64})
        code = "def test_mem():\n    data = bytearray(1024 * 1024 * 512)\n    assert data\n"
        result = DockerSandbox(settings).run_tests(
            "python", repo, "legacylens_tests/test_m.py", code, install_dependencies=False
        )
        assert result.status in ("failed", "error"), result.output

    def test_fork_bomb_is_contained(self, repo):
        settings = get_settings().model_copy(update={"sandbox_timeout_seconds": 20, "sandbox_pids_limit": 64})
        code = (
            "import os\ndef test_fork():\n"
            "    made = 0\n"
            "    try:\n"
            "        while made < 100000:\n"
            "            if os.fork() == 0:\n"
            "                import time; time.sleep(60); os._exit(0)\n"
            "            made += 1\n"
            "    except OSError:\n"
            "        pass\n"
            "    assert made < 100\n"
        )
        result = DockerSandbox(settings).run_tests(
            "python", repo, "legacylens_tests/test_f.py", code, install_dependencies=False
        )
        assert result.status in ("passed", "timeout", "failed"), result.output
        assert result.status != "error" or "memory" in result.output

    def test_huge_output_is_truncated(self, repo):
        code = "def test_out():\n    for _ in range(3000):\n        print('x' * 2048, flush=True)\n"
        result = _run_code("python", "legacylens_tests/test_o.py", code + "    assert False\n", repo)
        assert len(result.output) < 700_000
        assert result.status == "failed"

    def test_volume_is_removed_after_the_run(self, repo):
        _run_code("python", "legacylens_tests/test_a.py", "def test_a():\n    pass\n", repo)
        volumes = subprocess.run(
            ["docker", "volume", "ls", "--filter", "label=legacylens.sandbox=1", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert volumes.stdout.strip() == ""

    def test_tests_can_not_read_files_left_out_of_the_workspace(self, repo):
        (repo / ".env").write_text("SECRET=1")
        code = "import os\ndef test_env():\n    assert not os.path.exists('/workspace/.env')\n"
        result = _run_code("python", "legacylens_tests/test_e.py", code, repo)
        assert result.status == "passed", result.output

    def test_install_phase_has_network_but_tests_do_not(self, tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        (repo / "requirements.txt").write_text("iniconfig==2.0.0\n")
        code = (
            "import socket\nimport iniconfig\ndef test_dep_and_no_network():\n"
            "    assert iniconfig\n"
            "    s = socket.socket()\n    s.settimeout(3)\n"
            "    try:\n        s.connect(('1.1.1.1', 53))\n    except OSError:\n        return\n"
            "    raise AssertionError('network is reachable')\n"
        )
        result = DockerSandbox(get_settings()).run_tests(
            "python", repo, "legacylens_tests/test_i.py", code, install_dependencies=True
        )
        assert result.status == "passed", result.output
        assert "Installing dependencies" in result.output


@needs_docker
@pytest.mark.docker
class TestEndToEndRuns:
    """The full API flow, with baseline tests generated for every supported stack."""

    @pytest.mark.parametrize(
        "fixture,target",
        [
            ("python_app", "shop/services/pricing.py"),
            ("node_app", "src/services/mailer.js"),
            ("nest_app", "src/cats.service.ts"),
            ("laravel_app", "app/Models/User.php"),
            ("drupal_site", "modules/custom/acme_reports/acme_reports.module"),
            ("drupal7_site", "sites/all/modules/legacy_mod/legacy_mod.module"),
            ("plain_php", "lib/helpers.php"),
        ],
    )
    def test_baseline_test_runs_green_for_each_stack(self, client, upload, auth, fixture, target):
        project = upload(fixture)
        base = _base(project)
        test = client.post(f"{base}/tests/generate", headers=auth, json={"target_path": target, "use_ai": False})
        assert test.status_code == 201, test.text
        run = client.post(f"{base}/tests/{test.json()['id']}/run", headers=auth)
        assert run.status_code == 202, run.text
        final = client.get(f"{base}/test-runs/{run.json()['id']}", headers=auth).json()
        assert final["status"] == "passed", final["output"]
        assert final["results"]["passed"] >= 1
        assert final["duration_seconds"] > 0

    def test_baseline_catches_a_removed_function(self, client, upload, auth):
        project = upload("python_app")
        base = _base(project)
        test = client.post(
            f"{base}/tests/generate",
            headers=auth,
            json={"target_path": "shop/services/pricing.py", "use_ai": False},
        ).json()
        repo = get_settings().repos_dir / project["id"]
        target = repo / "shop/services/pricing.py"
        target.write_text(target.read_text().replace("def discount(", "def renamed_discount("))
        run = client.post(f"{base}/tests/{test['id']}/run", headers=auth).json()
        final = client.get(f"{base}/test-runs/{run['id']}", headers=auth).json()
        assert final["status"] == "failed", final["output"]
        assert "discount" in final["output"]

    @respx.mock(assert_all_called=False)
    def test_ai_generated_test_runs_in_sandbox(self, client, upload, auth, respx_mock):
        project = upload("python_app")
        base = _base(project)
        ai_test = (
            "```python\nfrom shop.services.pricing import final_price\n\n"
            "def test_final_price():\n    assert final_price(100, 0.1) == 110.0\n```"
        )
        respx_mock.post(GEMINI_URL).mock(return_value=gemini_ok(ai_test))
        test = client.post(
            f"{base}/tests/generate", headers=auth, json={"target_path": "shop/services/pricing.py"}
        ).json()
        assert test["source"] == "ai"
        run = client.post(f"{base}/tests/{test['id']}/run", headers=auth).json()
        final = client.get(f"{base}/test-runs/{run['id']}", headers=auth).json()
        assert final["status"] == "passed", final["output"]
        history = client.get(f"{base}/tests", headers=auth).json()
        assert history[0]["latest_run"]["status"] == "passed"
