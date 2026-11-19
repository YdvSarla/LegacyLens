"""API tests through the real application, database and analysis pipeline."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from tests.conftest import FIXTURES, zip_directory
from tests.test_llm import GEMINI_URL, GROQ_URL, OLLAMA_URL, gemini_ok, gemini_quota, groq_ok, ollama_ok


def _base(project):
    return f"/api/projects/{project['id']}"


class TestAuth:
    def test_register_login_me_logout_with_cookie(self, client):
        body = {"email": "A@Example.com", "name": "A", "password": "correct horse battery"}
        assert client.post("/api/auth/register", json=body).status_code == 201
        client.cookies.clear()
        assert client.get("/api/auth/me").status_code == 401
        login = client.post("/api/auth/login", json={"email": "a@example.com", "password": body["password"]})
        assert login.status_code == 200
        cookie = login.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert client.get("/api/auth/me").json()["email"] == "a@example.com"
        assert client.post("/api/auth/logout").status_code == 204
        client.cookies.clear()
        assert client.get("/api/auth/me").status_code == 401

    def test_duplicate_email_is_rejected(self, client, register):
        register("a@example.com")
        response = client.post(
            "/api/auth/register", json={"email": "A@example.com", "name": "A", "password": "another long password"}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_weak_password_and_bad_email(self, client):
        short = client.post("/api/auth/register", json={"email": "a@example.com", "name": "A", "password": "short"})
        assert short.status_code == 422
        assert short.json()["error"]["details"]["fields"][0]["field"] == "password"
        assert (
            client.post("/api/auth/register", json={"email": "nope", "name": "A", "password": "x" * 12}).status_code
            == 422
        )

    def test_wrong_password_and_unknown_user_look_the_same(self, client, register):
        register("a@example.com")
        wrong = client.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong password"})
        unknown = client.post("/api/auth/login", json={"email": "b@example.com", "password": "wrong password"})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()

    def test_login_is_throttled(self, client, register):
        register("a@example.com")
        for _ in range(8):
            client.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong password"})
        blocked = client.post("/api/auth/login", json={"email": "a@example.com", "password": "correct horse battery"})
        assert blocked.status_code == 429

    def test_invalid_token_is_rejected(self, client):
        response = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.token"})
        assert response.status_code == 401

    def test_password_is_hashed(self, client, register):
        register("a@example.com")
        from sqlalchemy import select

        from app.db import models as m
        from app.db.session import session_scope

        with session_scope() as db:
            stored = db.scalars(select(m.User)).one().password_hash
        assert stored.startswith("$argon2") and "correct horse" not in stored

    def test_registration_can_be_closed(self, client, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "allow_registration", False)
        response = client.post("/api/auth/register", json={"email": "a@e.com", "name": "A", "password": "x" * 12})
        assert response.status_code == 403


class TestProtection:
    def test_every_project_route_requires_login(self, client):
        from app.main import app

        schema = app.openapi()
        public = {"/api/auth/register", "/api/auth/login", "/api/auth/logout", "/api/auth/config", "/api/health"}
        checked = 0
        for path, methods in schema["paths"].items():
            if path in public:
                continue
            url = path
            for name in ("project_id", "test_id", "run_id", "document_id", "comparison_id", "conversation_id"):
                url = url.replace("{" + name + "}", "00000000-0000-0000-0000-000000000000")
            url = url.replace("{name}", "gemini")
            for method in methods:
                response = client.request(method.upper(), url)
                # 422 here would mean validation ran before authentication revealed anything.
                assert response.status_code in (401, 422), f"{method} {path} gave {response.status_code}"
                checked += 1
        assert checked > 30

    def test_another_users_project_is_invisible(self, client, upload, register, auth):
        project = upload("python_app")
        other = register("other@example.com")
        base = _base(project)
        assert client.get("/api/projects", headers=other).json() == []
        for method, url in [
            ("GET", base),
            ("GET", f"{base}/dashboard"),
            ("GET", f"{base}/architecture"),
            ("GET", f"{base}/symbols"),
            ("GET", f"{base}/endpoints"),
            ("GET", f"{base}/models"),
            ("GET", f"{base}/dependencies"),
            ("GET", f"{base}/files"),
            ("GET", f"{base}/graph"),
            ("GET", f"{base}/source?path=shop/views.py"),
            ("GET", f"{base}/commits"),
            ("GET", f"{base}/tests"),
            ("GET", f"{base}/docs"),
            ("GET", f"{base}/conversations"),
            ("DELETE", base),
            ("POST", f"{base}/reanalyze"),
        ]:
            assert client.request(method, url, headers=other).status_code == 404, f"{method} {url}"
        assert client.post(f"{base}/chat", headers=other, json={"message": "x"}).status_code == 404
        assert client.get(base, headers=auth).status_code == 200

    def test_conversation_of_another_project_is_not_reachable(self, client, upload, auth):
        with respx.mock:
            respx.post(GEMINI_URL).mock(return_value=gemini_ok("hi"))
            one = upload("python_app")
            two = upload("node_app")
            chat = client.post(f"{_base(one)}/chat", headers=auth, json={"message": "pricing"}).json()
            response = client.post(
                f"{_base(two)}/chat",
                headers=auth,
                json={"message": "x", "conversation_id": chat["conversation_id"]},
            )
        assert response.status_code == 404
        assert client.get(f"{_base(two)}/conversations/{chat['conversation_id']}", headers=auth).status_code == 404


class TestUpload:
    def test_rejects_non_zip_name_and_invalid_archive(self, client, auth):
        bad_name = client.post("/api/projects/upload", headers=auth, files={"file": ("a.txt", b"x")})
        assert bad_name.status_code == 422
        bad_content = client.post("/api/projects/upload", headers=auth, files={"file": ("a.zip", b"not a zip")})
        assert bad_content.status_code == 202
        project = client.get(f"/api/projects/{bad_content.json()['id']}", headers=auth).json()
        assert project["status"] == "failed"
        assert "not a valid ZIP" in project["error"]

    def test_zip_slip_fails_the_import_with_a_message(self, client, auth):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("../../evil.php", "<?php")
        response = client.post("/api/projects/upload", headers=auth, files={"file": ("a.zip", buffer.getvalue())})
        project = client.get(f"/api/projects/{response.json()['id']}", headers=auth).json()
        assert project["status"] == "failed"
        assert "traversal" in project["error"]

    def test_project_without_supported_code_fails_clearly(self, client, auth, tmp_path):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("readme.txt", "hello")
        response = client.post("/api/projects/upload", headers=auth, files={"file": ("a.zip", buffer.getvalue())})
        project = client.get(f"/api/projects/{response.json()['id']}", headers=auth).json()
        assert project["status"] == "failed"
        assert "No supported source files" in project["error"]

    def test_oversized_request_is_rejected_before_parsing(self, client, auth, monkeypatch):
        response = client.post(
            "/api/projects/upload",
            headers={**auth, "Content-Length": str(10**11), "Content-Type": "multipart/form-data; boundary=x"},
            content=b"x",
        )
        assert response.status_code == 413

    def test_github_import_rejects_unsafe_urls(self, client, auth):
        for url in ("http://169.254.169.254/a/b", "file:///etc/passwd", "https://gitlab.com/a/b"):
            response = client.post("/api/projects/github", headers=auth, json={"url": url})
            assert response.status_code == 422, url

    def test_delete_removes_files_and_index(self, client, upload, auth):
        from app.core.config import get_settings

        project = upload("python_app")
        settings = get_settings()
        repo = settings.repos_dir / project["id"]
        index = settings.indexes_dir / project["id"]
        assert repo.exists() and index.exists()
        assert client.delete(_base(project), headers=auth).status_code == 204
        assert not repo.exists() and not index.exists()
        assert client.get(_base(project), headers=auth).status_code == 404


class TestAnalysisAcrossStacks:
    def test_laravel(self, client, upload, auth):
        project = upload("laravel_app")
        base = _base(project)
        assert "Laravel" in project["frameworks"]
        endpoints = client.get(f"{base}/endpoints", headers=auth).json()["items"]
        assert any(e["path"] == "/admin/users" and e["method"] == "POST" for e in endpoints)
        models = {m["name"]: m for m in client.get(f"{base}/models", headers=auth).json()["items"]}
        assert models["App\\Models\\User"]["table_name"] == "app_users"
        assert "app_users" in models  # From the migration.
        packages = {p["name"] for p in client.get(f"{base}/dependencies", headers=auth).json()["items"]}
        assert "laravel/framework" in packages
        issues = client.get(f"{base}/issues?limit=200", headers=auth).json()["items"]
        rules = {(i["rule"], i["path"]) for i in issues}
        assert ("php-removed-mysql", "app/Services/BillingService.php") in rules
        assert ("hardcoded-secret", "app/Services/BillingService.php") in rules
        architecture = client.get(f"{base}/architecture", headers=auth).json()
        assert "artisan" in architecture["entry_points"]
        assert {"route", "controller", "model"} <= {x["role"] for x in architecture["layers"]}
        detail = client.get(f"{base}/file-detail?path=app/Services/BillingService.php", headers=auth).json()
        assert "app/Http/Controllers/UserController.php" in {d["path"] for d in detail["dependents"]}

    def test_drupal8(self, client, upload, auth):
        project = upload("drupal_site")
        base = _base(project)
        assert "Drupal" in project["frameworks"]
        endpoints = client.get(f"{base}/endpoints", headers=auth).json()["items"]
        assert any(e["path"] == "/admin/reports/acme" and e["framework"] == "drupal" for e in endpoints)
        models = client.get(f"{base}/models", headers=auth).json()["items"]
        assert any(m["framework"] == "drupal-entity" for m in models)
        hooks = client.get(f"{base}/architecture", headers=auth).json()["drupal_hooks"]
        assert {"help", "form_alter"} <= {h["hook"] for h in hooks}
        graph = client.get(f"{base}/graph", headers=auth).json()
        assert any(
            e["source"].endswith("ReportController.php") and e["target"].endswith("ReportBuilder.php")
            for e in graph["edges"]
        )

    def test_drupal7(self, client, upload, auth):
        project = upload("drupal7_site")
        base = _base(project)
        assert "Drupal" in project["frameworks"]
        endpoints = client.get(f"{base}/endpoints", headers=auth).json()["items"]
        assert [e["path"] for e in endpoints] == ["/admin/legacy/list"]
        assert [m["table_name"] for m in client.get(f"{base}/models", headers=auth).json()["items"]] == ["legacy_items"]
        graph = client.get(f"{base}/graph", headers=auth).json()
        targets = {e["target"] for e in graph["edges"]}
        assert any(t.endswith("includes/helper.inc") for t in targets)

    def test_plain_php(self, client, upload, auth):
        project = upload("plain_php")
        base = _base(project)
        endpoints = {(e["method"], e["path"]) for e in client.get(f"{base}/endpoints", headers=auth).json()["items"]}
        assert ("GET", "/index.php") in endpoints
        assert ("POST", "/admin/save.php") in endpoints
        graph = client.get(f"{base}/graph", headers=auth).json()
        pairs = {(e["source"], e["target"]) for e in graph["edges"]}
        assert ("index.php", "lib/db.php") in pairs
        assert ("admin/save.php", "lib/db.php") in pairs
        rules = {i["rule"] for i in client.get(f"{base}/issues?limit=200", headers=auth).json()["items"]}
        assert {"php-echo-input", "php-removed-mysql", "hardcoded-secret", "debug-php"} <= rules

    def test_node_express(self, client, upload, auth):
        project = upload("node_app")
        base = _base(project)
        assert {"Express", "Mongoose"} <= set(project["frameworks"])
        endpoints = {(e["method"], e["path"]) for e in client.get(f"{base}/endpoints", headers=auth).json()["items"]}
        assert ("GET", "/api/users/") in endpoints or ("GET", "/api/users") in endpoints
        assert ("GET", "/health") in endpoints
        graph = client.get(f"{base}/graph", headers=auth).json()
        pairs = {(e["source"], e["target"]) for e in graph["edges"]}
        assert ("src/routes/users.js", "src/controllers/userController.js") in pairs
        assert client.get(f"{base}/architecture", headers=auth).json()["entry_points"][0] == "src/server.js"

    def test_nestjs_typescript(self, client, upload, auth):
        project = upload("nest_app")
        base = _base(project)
        assert "NestJS" in project["frameworks"]
        kinds = {s["kind"] for s in client.get(f"{base}/symbols", headers=auth).json()["items"]}
        assert {"class", "method", "interface"} <= kinds
        assert client.get(f"{base}/models", headers=auth).json()["items"][0]["framework"] == "typeorm"

    def test_python(self, client, upload, auth):
        project = upload("python_app")
        base = _base(project)
        assert {"Django", "Flask", "SQLAlchemy"} <= set(project["frameworks"])
        frameworks = {e["framework"] for e in client.get(f"{base}/endpoints", headers=auth).json()["items"]}
        assert {"flask", "django"} <= frameworks
        names = {m["name"] for m in client.get(f"{base}/models", headers=auth).json()["items"]}
        assert {"Order", "Article"} <= names
        symbols = client.get(f"{base}/symbols?q=final_price", headers=auth).json()
        assert symbols["total"] == 1
        assert symbols["items"][0]["path"] == "shop/services/pricing.py"


class TestInsights:
    def test_dashboard_contents(self, client, upload, auth):
        project = upload("laravel_app")
        data = client.get(f"{_base(project)}/dashboard", headers=auth).json()
        assert data["metrics"]["classes"] >= 5
        assert data["metrics"]["loc"] > 50
        assert 0 <= data["quality"]["scores"]["overall"] <= 100
        assert data["quality"]["by_category"]["security"] > 0
        assert data["top_issues"][0]["path"] and data["top_issues"][0]["line"]
        # The distribution covers production code, so it leaves out the fixture's test methods.
        counted = sum(b["count"] for b in data["complexity_distribution"])
        assert 0 < counted < data["metrics"]["functions"] + data["metrics"]["methods"]

    def test_graph_levels_focus_and_filters(self, client, upload, auth):
        project = upload("node_app")
        base = _base(project)
        full = client.get(f"{base}/graph?level=file", headers=auth).json()
        assert not any(n["role"] == "test" for n in full["nodes"])
        with_tests = client.get(f"{base}/graph?level=file&include_tests=true", headers=auth).json()
        assert any(n["role"] == "test" for n in with_tests["nodes"])
        focused = client.get(f"{base}/graph?level=file&focus=src/services/mailer.js&depth=1", headers=auth).json()
        assert {n["id"] for n in focused["nodes"]} == {"src/services/mailer.js", "src/controllers/userController.js"}
        limited = client.get(f"{base}/graph?level=file&limit=2", headers=auth).json()
        assert len(limited["nodes"]) == 2 and limited["truncated"] is True
        modules = client.get(f"{base}/graph?level=module", headers=auth).json()
        assert modules["nodes"]
        symbols = client.get(f"{base}/graph?level=symbol", headers=auth).json()
        assert any(n["label"] == "sendWelcome" for n in symbols["nodes"])
        assert client.get(f"{base}/graph?focus=nope.js", headers=auth).status_code == 422

    def test_source_viewer_blocks_traversal_and_secrets(self, client, upload, auth):
        project = upload("python_app", extra={".env": "SECRET_KEY=super-secret-value-do-not-leak"})
        base = _base(project)
        ok = client.get(f"{base}/source", params={"path": "shop/views.py"}, headers=auth)
        assert ok.status_code == 200
        assert "def price" in ok.json()["content"]
        for path in ("../../../../etc/passwd", "/etc/passwd", "shop/../../secret", ".git/config"):
            assert client.get(f"{base}/source", params={"path": path}, headers=auth).status_code == 404, path
        hidden = client.get(f"{base}/source", params={"path": ".env"}, headers=auth)
        assert hidden.status_code in (403, 404)
        assert "super-secret" not in hidden.text

    def test_secret_files_are_never_indexed(self, client, upload, auth):
        upload("python_app", extra={".env": "SECRET_KEY=super-secret-value-do-not-leak"})
        from sqlalchemy import select

        from app.db import models as m
        from app.db.session import session_scope

        with session_scope() as db:
            content = db.scalars(select(m.CodeChunk.content)).all()
        assert content
        assert not any("super-secret" in value for value in content)

    def test_reanalyze(self, client, upload, auth):
        project = upload("python_app")
        response = client.post(f"{_base(project)}/reanalyze", headers=auth)
        assert response.status_code == 202
        assert client.get(_base(project), headers=auth).json()["status"] == "ready"
        assert client.get(f"{_base(project)}/symbols?q=final_price", headers=auth).json()["total"] == 1, "no duplicates"

    def test_failed_embeddings_keep_the_project_usable(self, client, upload, auth, monkeypatch):
        from app.services import retrieval

        class Broken:
            dimension = 8

            def embed(self, texts):
                raise OSError("model download failed")

        retrieval.set_embedder(Broken())
        project = upload("python_app")
        assert project["search_index"]["status"] == "failed"
        results = client.get(f"{_base(project)}/search?q=final_price", headers=auth).json()
        assert results[0]["path"] == "shop/services/pricing.py"


class TestAssistant:
    @respx.mock
    def test_answer_has_verified_file_and_line_citations(self, client, upload, auth):
        project = upload("python_app")
        route = respx.post(GEMINI_URL).mock(
            return_value=gemini_ok("Price adds tax [shop/services/pricing.py:5-9]. Also [nope/fake.py:1-2].")
        )
        reply = client.post(f"{_base(project)}/chat", headers=auth, json={"message": "How does final_price work?"})
        assert reply.status_code == 200, reply.text
        message = reply.json()["message"]
        assert message["provider"] == "gemini"
        assert message["citations"] == [{"path": "shop/services/pricing.py", "start_line": 5, "end_line": 9}]
        assert "[shop/services/pricing.py:5-9]" in message["content"]
        assert "`nope/fake.py:1-2` (unverified reference)" in message["content"]
        assert any(s["path"] == "shop/services/pricing.py" for s in message["sources"])
        prompt = route.calls[0].request.content.decode()
        assert "final_price" in prompt
        assert "5: def final_price" in prompt, "excerpts must carry real line numbers"

    @respx.mock
    def test_answer_that_names_code_without_citing_still_gets_references(self, client, upload, auth):
        project = upload("python_app")
        respx.post(GEMINI_URL).mock(return_value=gemini_ok("The `final_price` function applies the tax."))
        message = client.post(
            f"{_base(project)}/chat", headers=auth, json={"message": "How is the price computed?"}
        ).json()["message"]
        assert {"path": "shop/services/pricing.py", "start_line": 5, "end_line": 9, "inferred": True} in message[
            "citations"
        ]

    @respx.mock
    def test_no_reference_is_invented_when_the_answer_names_nothing(self, client, upload, auth):
        project = upload("python_app")
        respx.post(GEMINI_URL).mock(return_value=gemini_ok("It depends on the configuration."))
        message = client.post(f"{_base(project)}/chat", headers=auth, json={"message": "final_price?"}).json()[
            "message"
        ]
        assert message["citations"] == []
        assert message["sources"], "the code that was searched is still listed"

    @respx.mock
    def test_prompt_asks_for_citations_in_the_expected_form(self, client, upload, auth):
        project = upload("python_app")
        route = respx.post(GEMINI_URL).mock(return_value=gemini_ok("Fine."))
        client.post(f"{_base(project)}/chat", headers=auth, json={"message": "final_price?"})
        prompt = route.calls[0].request.content.decode()
        assert "[path/to/file.ext:START-END]" in prompt
        assert "[src/cart.py:10-24]" in prompt

    @respx.mock
    def test_conversation_keeps_history(self, client, upload, auth):
        project = upload("python_app")
        route = respx.post(GEMINI_URL).mock(return_value=gemini_ok("First answer"))
        first = client.post(f"{_base(project)}/chat", headers=auth, json={"message": "What is final_price?"}).json()
        client.post(
            f"{_base(project)}/chat",
            headers=auth,
            json={"message": "And what else?", "conversation_id": first["conversation_id"]},
        )
        assert "First answer" in route.calls[1].request.content.decode()
        stored = client.get(f"{_base(project)}/conversations/{first['conversation_id']}", headers=auth).json()
        assert [x["role"] for x in stored["messages"]] == ["user", "assistant", "user", "assistant"]

    @respx.mock
    def test_automatic_fallback_is_reported(self, client, upload, auth):
        project = upload("python_app")
        respx.post(GEMINI_URL).mock(return_value=gemini_quota())
        respx.post(GROQ_URL).mock(return_value=groq_ok("Answer from groq"))
        message = client.post(f"{_base(project)}/chat", headers=auth, json={"message": "final_price"}).json()["message"]
        assert message["provider"] == "groq"
        assert message["content"] == "Answer from groq"

    @respx.mock
    def test_all_providers_down_still_returns_code_references(self, client, upload, auth):
        project = upload("python_app")
        respx.post(GEMINI_URL).mock(return_value=gemini_quota())
        respx.post(GROQ_URL).mock(return_value=httpx.Response(429, json={"error": {"message": "x"}}))
        respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("down"))
        reply = client.post(f"{_base(project)}/chat", headers=auth, json={"message": "final_price"})
        assert reply.status_code == 200
        message = reply.json()["message"]
        assert message["degraded"] is True
        assert message["provider"] is None
        assert "shop/services/pricing.py" in message["content"]
        assert message["citations"]

    @respx.mock
    def test_local_only_project_never_reaches_cloud(self, client, upload, auth):
        project = upload("python_app")
        updated = client.patch(_base(project), headers=auth, json={"local_ai_only": True})
        assert updated.json()["local_ai_only"] is True
        gemini = respx.post(GEMINI_URL).mock(return_value=gemini_ok())
        groq = respx.post(GROQ_URL).mock(return_value=groq_ok())
        respx.post(OLLAMA_URL).mock(return_value=ollama_ok("local answer"))
        message = client.post(f"{_base(project)}/chat", headers=auth, json={"message": "final_price"}).json()["message"]
        assert message["provider"] == "ollama"
        assert not gemini.called and not groq.called

    @respx.mock
    def test_local_only_project_ignores_a_request_for_a_cloud_provider(self, client, upload, auth):
        project = upload("python_app")
        client.patch(_base(project), headers=auth, json={"local_ai_only": True})
        gemini = respx.post(GEMINI_URL).mock(return_value=gemini_ok())
        reply = client.post(
            f"{_base(project)}/chat", headers=auth, json={"message": "final_price", "provider": "gemini"}
        )
        assert reply.json()["message"]["degraded"] is True
        assert "local AI only" in reply.json()["message"]["content"]
        assert not gemini.called

    @respx.mock
    def test_secrets_inside_source_files_never_reach_the_provider(self, client, upload, auth):
        extra = {
            "shop/config_values.py": 'DB_PASSWORD = "hunter2hunter2"\ndef load_config_values():\n    return DB_PASSWORD\n'
        }
        project = upload("python_app", extra=extra)
        route = respx.post(GEMINI_URL).mock(return_value=gemini_ok("It loads settings."))
        client.post(f"{_base(project)}/chat", headers=auth, json={"message": "What does config_values do?"})
        prompt = route.calls[0].request.content.decode()
        assert "config_values" in prompt
        assert "hunter2hunter2" not in prompt

    @respx.mock
    def test_provider_can_be_chosen_per_question(self, client, upload, auth):
        project = upload("python_app")
        respx.post(GROQ_URL).mock(return_value=groq_ok("g"))
        message = client.post(
            f"{_base(project)}/chat", headers=auth, json={"message": "x y z", "provider": "groq"}
        ).json()["message"]
        assert message["provider"] == "groq"
        rejected = client.post(f"{_base(project)}/chat", headers=auth, json={"message": "x", "provider": "openai"})
        assert rejected.status_code == 422

    def test_empty_message_rejected(self, client, upload, auth):
        project = upload("python_app")
        assert client.post(f"{_base(project)}/chat", headers=auth, json={"message": ""}).status_code == 422


class TestProviderApi:
    @respx.mock
    def test_status_preferences_and_no_key_exposure(self, client, auth):
        data = client.get("/api/ai/providers", headers=auth)
        assert data.status_code == 200
        assert "test-gemini-key" not in data.text and "test-groq-key" not in data.text
        body = data.json()
        assert body["order"] == ["gemini", "groq", "ollama"]
        assert body["providers"][0]["configured"] is True
        assert "improve" in body["providers"][0]["privacy"]
        update = client.put("/api/ai/preferences", headers=auth, json={"provider_order": ["ollama", "groq"]})
        assert update.json()["order"] == ["ollama", "groq", "gemini"]
        assert client.get("/api/auth/me", headers=auth).json()["provider_order"] == ["ollama", "groq"]
        assert client.put("/api/ai/preferences", headers=auth, json={"provider_order": ["openai"]}).status_code == 422
        assert (
            client.put("/api/ai/preferences", headers=auth, json={"provider_order": None}).json()["provider_order"]
            is None
        )

    @respx.mock
    def test_provider_test_endpoint(self, client, auth):
        respx.post(GROQ_URL).mock(return_value=groq_ok("ready"))
        respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("down"))
        assert client.post("/api/ai/providers/groq/test", headers=auth).json()["reply"] == "ready"
        failed = client.post("/api/ai/providers/ollama/test", headers=auth)
        assert failed.status_code == 503
        assert client.post("/api/ai/providers/openai/test", headers=auth).status_code == 422

    def test_settings_api_cannot_set_keys(self, client, auth):
        from app.main import app

        for path in app.openapi()["paths"]:
            assert "key" not in path.lower()


def _compare_setup(client, auth, changes):
    """Uploads node_app, then a second version with `changes`. Returns (base url, commits)."""
    first = client.post(
        "/api/projects/upload",
        headers=auth,
        files={"file": ("node_app.zip", zip_directory(FIXTURES / "node_app"))},
    ).json()
    base = f"/api/projects/{first['id']}"
    second = client.post(
        f"{base}/versions",
        headers=auth,
        data={"label": "Version 2"},
        files={"file": ("v2.zip", zip_directory(FIXTURES / "node_app", changes=changes))},
    )
    assert second.status_code == 202, second.text
    assert client.get(base, headers=auth).json()["status"] == "ready"
    commits = client.get(f"{base}/commits", headers=auth).json()
    assert [c["subject"] for c in commits] == ["Version 2", "Initial upload"]
    return base, commits


MAILER = Path(FIXTURES / "node_app/src/services/mailer.js").read_text()


class TestCompare:
    def test_detects_changes_impact_and_risks(self, client, auth):
        changes = {
            # Signature change on a function used by the controller.
            "src/services/mailer.js": MAILER.replace(
                "function sendWelcome(address) {", "function sendWelcome(address, locale) {"
            ).replace("function formatSubject(name) {\n  return name ? `Hello ${name}` : 'Hello';\n}\n\n", "")
            + "\neval(process.env.CODE);\n",
            "package.json": (FIXTURES / "node_app/package.json").read_text().replace("^4.18.0", "^5.0.0"),
        }
        base, commits = _compare_setup(client, auth, changes)
        response = client.post(
            f"{base}/compare", headers=auth, json={"base": commits[1]["sha"], "head": commits[0]["sha"]}
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert {f["path"] for f in result["files"]} == {"src/services/mailer.js", "package.json"}
        by_name = {s["name"]: s for s in result["symbols"]}
        assert by_name["sendWelcome"]["status"] == "modified"
        assert by_name["sendWelcome"]["signature_changed"] is True
        assert by_name["formatSubject"]["status"] == "removed"
        impacted = {i["path"]: i for i in result["impacted"]}
        assert impacted["src/controllers/userController.js"]["distance"] == 1
        assert impacted["src/routes/users.js"]["distance"] == 2
        assert impacted["src/server.js"]["distance"] == 3
        rules = {r["rule"] for r in result["risks"]}
        assert {"dependency-change", "new-dangerous-eval"} <= rules
        assert result["risk"]["score"] > 0
        assert result["risk"]["level"] in ("medium", "high", "critical")
        assert result["modules"]
        assert any(e["file"] == "src/routes/users.js" for e in result["endpoints"])

    def test_removed_function_still_in_use_is_critical(self, client, auth):
        changes = {
            "src/services/mailer.js": "function formatSubject(name) { return name; }\nmodule.exports = { formatSubject };\n"
        }
        first = client.post(
            "/api/projects/upload",
            headers=auth,
            files={"file": ("node_app.zip", zip_directory(FIXTURES / "node_app"))},
        ).json()
        base = f"/api/projects/{first['id']}"
        commits_before = client.get(f"{base}/commits", headers=auth).json()
        # Compare against the graph of the first version, where the caller is known.
        from app.core.config import get_settings
        from app.services import git

        repo = get_settings().repos_dir / first["id"]
        (repo / "src/services/mailer.js").write_text(changes["src/services/mailer.js"])
        new_head = git.commit_all(repo, "Remove sendWelcome")
        response = client.post(
            f"{base}/compare", headers=auth, json={"base": commits_before[0]["sha"], "head": new_head}
        )
        result = response.json()["result"]
        risk = next(r for r in result["risks"] if r["rule"] == "removed-symbol-in-use")
        assert risk["severity"] == "critical"
        assert risk["evidence"][0]["path"] == "src/controllers/userController.js"
        assert risk["evidence"][0]["line"] > 0
        assert result["graph_note"], "the graph comes from an older commit, and the response must say so"

    def test_invalid_refs_are_rejected(self, client, upload, auth):
        base = _base(upload("node_app"))
        for ref in ("--output=/tmp/pwned", "a;rm -rf /", "doesnotexist"):
            response = client.post(f"{base}/compare", headers=auth, json={"base": ref, "head": "HEAD"})
            assert response.status_code == 422, ref

    @respx.mock
    def test_ai_summary_optional_and_failure_safe(self, client, auth):
        base, commits = _compare_setup(client, auth, {"src/server.js": "module.exports = 1;\n"})
        payload = {"base": commits[1]["sha"], "head": commits[0]["sha"], "with_ai_summary": True}
        respx.post(GEMINI_URL).mock(return_value=gemini_ok("Summary text"))
        data = client.post(f"{base}/compare", headers=auth, json=payload).json()
        assert data["ai_summary"] == "Summary text"
        assert data["provider"] == "gemini"
        respx.post(GEMINI_URL).mock(return_value=gemini_quota())
        respx.post(GROQ_URL).mock(return_value=httpx.Response(429, json={"error": {"message": "x"}}))
        respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("down"))
        data = client.post(f"{base}/compare", headers=auth, json=payload).json()
        assert data["ai_summary"] is None
        assert data["ai_error"]
        assert data["result"]["files"], "the analysis must survive an AI failure"

    def test_history_of_comparisons(self, client, auth):
        base, commits = _compare_setup(client, auth, {"src/server.js": "module.exports = 1;\n"})
        created = client.post(
            f"{base}/compare", headers=auth, json={"base": commits[1]["sha"], "head": commits[0]["sha"]}
        ).json()
        listed = client.get(f"{base}/comparisons", headers=auth).json()
        assert listed[0]["id"] == created["id"]
        assert (
            client.get(f"{base}/comparisons/{created['id']}", headers=auth).json()["result"]["stats"]["files_changed"]
            == 1
        )

    def test_new_version_only_for_uploaded_projects(self, client, upload, auth):
        project = upload("node_app")
        response = client.post(f"{_base(project)}/sync", headers=auth)
        assert response.status_code == 422


class TestDocs:
    def test_without_ai(self, client, upload, auth):
        project = upload("laravel_app")
        base = _base(project)
        created = client.post(f"{base}/docs", headers=auth, json={"use_ai": False})
        assert created.status_code == 202
        document = client.get(f"{base}/docs/{created.json()['id']}", headers=auth).json()
        assert document["status"] == "ready"
        assert document["ai_enhanced"] is False
        content = document["content"]
        for expected in (
            "## Architecture",
            "## HTTP API reference",
            "/admin/users",
            "## Data models",
            "app_users",
            "laravel/framework",
            "## Code quality",
        ):
            assert expected in content, expected
        download = client.get(f"{base}/docs/{document['id']}/download", headers=auth)
        assert "attachment" in download.headers["content-disposition"]
        assert download.text == content

    @respx.mock
    def test_with_ai_narrative(self, client, upload, auth):
        project = upload("drupal7_site")
        base = _base(project)
        respx.post(GEMINI_URL).mock(return_value=gemini_ok("This module lists legacy items."))
        created = client.post(f"{base}/docs", headers=auth, json={"use_ai": True}).json()
        document = client.get(f"{base}/docs/{created['id']}", headers=auth).json()
        assert document["ai_enhanced"] is True
        assert document["provider"] == "gemini"
        assert "This module lists legacy items." in document["content"]
        assert "Written by AI (gemini" in document["content"], "readers must know this part may be wrong"
        assert "hook_menu" in document["content"]

    @respx.mock
    def test_ai_failure_still_produces_docs(self, client, upload, auth):
        project = upload("python_app")
        respx.post(GEMINI_URL).mock(return_value=gemini_quota())
        respx.post(GROQ_URL).mock(return_value=httpx.Response(429, json={"error": {"message": "x"}}))
        respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("down"))
        created = client.post(f"{_base(project)}/docs", headers=auth, json={"use_ai": True}).json()
        document = client.get(f"{_base(project)}/docs/{created['id']}", headers=auth).json()
        assert document["status"] == "ready"
        assert document["ai_enhanced"] is False
        assert "## Project overview" in document["content"]


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_error_format_for_unknown_route(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"


def test_restart_marks_interrupted_work_as_failed(client, upload, auth):
    """Work that was running when the server stopped must not look like it is still running."""
    from app.db import models as m
    from app.db.session import session_scope
    from app.services import jobs

    project = upload("python_app")
    with session_scope() as db:
        record = db.get(m.Project, __import__("uuid").UUID(project["id"]))
        record.status = "analyzing"
        db.add(m.Job(project_id=record.id, kind="analyze", status="running"))
    jobs.recover_interrupted()
    data = client.get(_base(project), headers=auth).json()
    assert data["status"] == "failed"
    assert "restarted" in data["error"]
    assert data["job"]["status"] == "failed"


def test_tests_and_samples_do_not_count_as_the_real_product(client, upload, auth):
    """A library's sample apps must not look like its API, dependencies or frameworks."""
    extra = {
        "examples/demo/app.js": "const express = require('express');\nconst app = express();\napp.get('/demo', (a, b) => b.end());\n",
        "examples/demo/package.json": '{"dependencies": {"koa": "^2.0.0", "@nestjs/core": "^10.0.0"}}',
        "test/server.test.js": "const app = require('express')();\napp.get('/only-in-test', (a, b) => b.end());\n",
    }
    project = upload("node_app", extra=extra)
    base = _base(project)
    paths = {e["path"] for e in client.get(f"{base}/endpoints", headers=auth).json()["items"]}
    assert "/health" in paths
    assert "/demo" not in paths and "/only-in-test" not in paths
    assert "NestJS" not in project["frameworks"] and "Koa" not in project["frameworks"]
    names = {p["name"] for p in client.get(f"{base}/dependencies", headers=auth).json()["items"]}
    assert "koa" not in names
    dashboard = client.get(f"{base}/dashboard", headers=auth).json()
    hubs = {item["path"] for item in dashboard["hotspots"]["most_depended_on"]}
    assert hubs and not any(path.startswith(("test/", "examples/")) for path in hubs)
    # The files stay browsable.
    assert client.get(f"{base}/source", params={"path": "examples/demo/app.js"}, headers=auth).status_code == 200
