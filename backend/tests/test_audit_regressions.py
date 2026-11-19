"""One test for each bug found while auditing against real repositories and real browsers."""

from __future__ import annotations

import io
import time
import zipfile

import httpx
import numpy as np
import pytest
import respx
from sqlalchemy import func, select, text

from app.core.config import Settings, get_settings
from app.core.errors import TooLargeError, ValidationFailed
from app.db import models as m
from app.db.session import get_engine, session_scope
from app.services import compare, git, retrieval
from app.services.analysis.architecture import find_entry_points
from app.services.analysis.graph import GraphBuilder
from app.services.analysis.languages import javascript, php, python
from app.services.analysis.quality import scan_lines
from app.services.assistant import _search_query
from app.services.ingest import import_zip
from tests.conftest import FIXTURES, FakeEmbedder, zip_directory
from tests.test_llm import GEMINI_URL, gemini_ok


def _base(project):
    return f"/api/projects/{project['id']}"


def _zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    return buffer.getvalue()


class TestPerformance:
    def test_php_call_resolution_is_not_quadratic(self):
        """Laravel's framework took 97 seconds in this step before the fix."""
        files = {}
        for number in range(150):
            calls = " ".join(f"strlen($a{i}); helper_{number}();" for i in range(150))
            files[f"f{number}.php"] = (
                f"<?php include 'f{(number + 1) % 150}.php'; function helper_{number}() {{}} {calls}".encode()
            )
        parsed = {path: php.analyze(source, path) for path, source in files.items()}
        started = time.monotonic()
        GraphBuilder(parsed).build()
        assert time.monotonic() - started < 3

    def test_reanalysis_deletes_children_in_bulk(self, client, upload, auth):
        project = upload("laravel_app")
        response = client.post(f"{_base(project)}/reanalyze", headers=auth)
        assert response.status_code == 202
        with session_scope() as db:
            assert db.scalar(select(func.count(m.Symbol.id))) > 0
            duplicates = db.scalar(
                select(func.count()).select_from(
                    select(m.SourceFile.path)
                    .group_by(m.SourceFile.project_id, m.SourceFile.path)
                    .having(func.count() > 1)
                    .subquery()
                )
            )
        assert duplicates == 0

    def test_foreign_keys_have_indexes(self):
        """Without them every parent delete scans the child table."""
        query = text(
            """
            SELECT c.conrelid::regclass::text, a.attname
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
            WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
              AND NOT EXISTS (
                SELECT 1 FROM pg_index i
                WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1]
              )
            """
        )
        with get_engine().connect() as connection:
            assert connection.execute(query).fetchall() == []


class TestSafeUpdates:
    def test_bad_new_version_keeps_the_existing_code(self, client, upload, auth):
        project = upload("python_app")
        base = _base(project)
        response = client.post(f"{base}/versions", headers=auth, files={"file": ("v2.zip", b"not a zip")})
        assert response.status_code == 202
        after = client.get(base, headers=auth).json()
        assert after["status"] == "failed"
        assert "not a valid ZIP" in after["error"]
        source = client.get(f"{base}/source", params={"path": "shop/views.py"}, headers=auth)
        assert source.status_code == 200, "the old code must survive a bad upload"
        assert len(client.get(f"{base}/commits", headers=auth).json()) == 1

    def test_zip_slip_in_new_version_keeps_the_existing_code(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "keep.py").write_text("x = 1\n")
        git.init_snapshot(repo, "first")
        archive = tmp_path / "bad.zip"
        archive.write_bytes(_zip({"../evil.py": "x"}))
        with pytest.raises(ValidationFailed):
            import_zip(repo, archive, get_settings(), "second")
        assert (repo / "keep.py").exists()

    def test_empty_archive_is_refused(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "keep.py").write_text("x = 1\n")
        git.init_snapshot(repo, "first")
        archive = tmp_path / "empty.zip"
        archive.write_bytes(_zip({"__MACOSX/a": "x"}))
        with pytest.raises(ValidationFailed):
            import_zip(repo, archive, get_settings(), "second")
        assert (repo / "keep.py").exists()

    def test_project_can_only_be_processed_once_at_a_time(self, client, upload, auth):
        project = upload("python_app")
        with session_scope() as db:
            db.get(m.Project, __import__("uuid").UUID(project["id"])).status = "analyzing"
        assert client.post(f"{_base(project)}/reanalyze", headers=auth).status_code == 409
        conflict = client.post(
            f"{_base(project)}/versions", headers=auth, files={"file": ("v.zip", _zip({"a.py": "x"}))}
        )
        assert conflict.status_code == 409
        assert not list(get_settings().uploads_dir.glob("*.zip")), "a refused upload must not leave a file behind"
        assert client.delete(_base(project), headers=auth).status_code == 409

    def test_failed_github_import_can_be_retried(self, client, auth, monkeypatch):
        calls = []

        def clone(url, destination, *, branch, token, timeout):
            calls.append(url)
            if len(calls) == 1:
                raise git.GitError("temporary network failure")
            destination.mkdir(parents=True)
            (destination / "app.py").write_text("def main():\n    return 1\n")
            git.init_snapshot(destination, "Imported")

        monkeypatch.setattr(git, "clone", clone)
        monkeypatch.setattr("app.services.ingest.check_repository_size", lambda *a, **k: None)
        created = client.post("/api/projects/github", headers=auth, json={"url": "https://github.com/a/b"}).json()
        base = f"/api/projects/{created['id']}"
        assert client.get(base, headers=auth).json()["status"] == "failed"
        assert client.post(f"{base}/reanalyze", headers=auth, json={}).status_code == 202
        assert client.get(base, headers=auth).json()["status"] == "ready"
        assert len(calls) == 2

    def test_project_limit(self, client, upload, auth, monkeypatch):
        monkeypatch.setattr(get_settings(), "max_projects_per_user", 1)
        upload("plain_php")
        second = client.post("/api/projects/upload", headers=auth, files={"file": ("a.zip", _zip({"a.py": "x=1"}))})
        assert second.status_code == 409
        assert "limit" in second.json()["error"]["message"]

    def test_internal_errors_do_not_reach_users(self):
        from app.services.jobs import user_message

        assert "password=abc" not in user_message(ValueError("connection password=abc failed"))
        assert user_message(ValidationFailed("Clear message.")) == "Clear message."


class TestSessions:
    def test_change_password_signs_out_other_sessions(self, client, register):
        other_device = register("a@example.com")
        login = client.post("/api/auth/login", json={"email": "a@example.com", "password": "correct horse battery"})
        assert login.status_code == 200
        changed = client.post(
            "/api/auth/change-password",
            json={"current_password": "correct horse battery", "new_password": "a brand new passphrase"},
        )
        assert changed.status_code == 200, changed.text
        assert client.get("/api/auth/me").status_code == 200, "this device keeps working"
        assert client.get("/api/auth/me", headers=other_device).status_code == 401
        client.cookies.clear()
        assert (
            client.post(
                "/api/auth/login", json={"email": "a@example.com", "password": "correct horse battery"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/login", json={"email": "a@example.com", "password": "a brand new passphrase"}
            ).status_code
            == 200
        )

    def test_change_password_needs_the_current_one(self, client, auth):
        response = client.post(
            "/api/auth/change-password",
            headers=auth,
            json={"current_password": "wrong", "new_password": "a brand new passphrase"},
        )
        assert response.status_code == 401

    def test_sign_out_everywhere_invalidates_tokens(self, client, auth):
        assert client.post("/api/auth/logout-all", headers=auth).status_code == 204
        assert client.get("/api/auth/me", headers=auth).status_code == 401

    def test_profile_update(self, client, auth):
        assert client.patch("/api/auth/me", headers=auth, json={"name": "New Name"}).json()["name"] == "New Name"

    def test_delete_account_removes_everything(self, client, upload, auth):
        project = upload("python_app")
        repo = get_settings().repos_dir / project["id"]
        assert repo.exists()
        wrong = client.request("DELETE", "/api/auth/me", headers=auth, json={"password": "wrong"})
        assert wrong.status_code == 401
        assert (
            client.request(
                "DELETE", "/api/auth/me", headers=auth, json={"password": "correct horse battery"}
            ).status_code
            == 204
        )
        assert not repo.exists()
        with session_scope() as db:
            for model in (m.User, m.Project, m.SourceFile, m.Symbol, m.CodeChunk, m.GraphEdge):
                assert db.scalar(select(func.count()).select_from(model)) == 0, model.__name__

    def test_public_config(self, client):
        assert client.get("/api/auth/config").json() == {"registration_open": True}

    def test_default_secret_is_never_used(self):
        settings = Settings(_env_file=None, secret_key="change-me-to-a-long-random-string")
        assert settings.secret_key.get_secret_value() != "change-me-to-a-long-random-string"
        with pytest.raises(ValueError):
            Settings(_env_file=None, environment="production", secret_key="change-me-to-a-long-random-string")


class TestCrossSiteProtection:
    def _session(self, client):
        response = client.post(
            "/api/auth/register", json={"email": "c@example.com", "name": "C", "password": "correct horse battery"}
        )
        assert response.status_code == 201

    def test_other_origin_cannot_use_the_session_cookie(self, client):
        self._session(client)
        attack = client.post("/api/auth/logout-all", headers={"Origin": "http://localhost:8080"})
        assert attack.status_code == 403
        assert client.get("/api/auth/me").status_code == 200, "the session must still be valid"

    def test_same_origin_through_the_proxy_is_allowed(self, client):
        self._session(client)
        response = client.patch(
            "/api/auth/me",
            headers={"Origin": "https://lens.example.com", "X-Forwarded-Host": "lens.example.com"},
            json={"name": "Same"},
        )
        assert response.status_code == 200

    def test_configured_origin_is_allowed(self, client):
        self._session(client)
        response = client.patch("/api/auth/me", headers={"Origin": "http://localhost:3000"}, json={"name": "Dev"})
        assert response.status_code == 200

    def test_bearer_tokens_are_not_affected(self, client, auth):
        client.cookies.clear()
        response = client.patch(
            "/api/auth/me", headers={**auth, "Origin": "http://elsewhere.example"}, json={"name": "API"}
        )
        assert response.status_code == 200

    def test_private_responses_are_not_cached(self, client, auth):
        response = client.get("/api/auth/me", headers=auth)
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"


class TestRateLimits:
    def test_ai_requests_are_limited_per_user(self, client, upload, auth, monkeypatch, register):
        monkeypatch.setattr(get_settings(), "rate_limit_ai_per_minute", 2)
        project = upload("python_app")
        with respx.mock:
            respx.post(GEMINI_URL).mock(return_value=gemini_ok("ok"))
            codes = [
                client.post(f"{_base(project)}/chat", headers=auth, json={"message": "q"}).status_code for _ in range(3)
            ]
        assert codes == [200, 200, 429]

    def test_imports_are_limited(self, client, auth, monkeypatch):
        monkeypatch.setattr(get_settings(), "rate_limit_imports_per_hour", 1)
        data = _zip({"a.py": "x = 1\n"})
        assert client.post("/api/projects/upload", headers=auth, files={"file": ("a.zip", data)}).status_code == 202
        assert client.post("/api/projects/upload", headers=auth, files={"file": ("a.zip", data)}).status_code == 429


class TestRealWorldRoutes:
    def test_express_route_chains(self):
        source = b"const router = express.Router();\nrouter.route('/').post(auth(), create).get(list);\nrouter.route('/:id').delete(remove);"
        routes = {(e.method, e.path, e.handler) for e in javascript.analyze(source, "r.js").endpoints}
        assert routes == {("POST", "/", "create"), ("GET", "/", "list"), ("DELETE", "/:id", "remove")}
        post = next(e for e in javascript.analyze(source, "r.js").endpoints if e.method == "POST")
        assert post.middleware == ["auth()"]

    def test_mongoose_schema_without_new(self):
        source = b"const s = mongoose.Schema({ name: String });\nconst User = mongoose.model('User', s);"
        models = javascript.analyze(source, "m.js").models
        assert [(x.name, x.fields) for x in models] == [("User", [{"name": "name", "type": "String"}])]

    def test_django_includes_prefix_across_files(self):
        root = b"from django.urls import include, path\nurlpatterns = [path('api/', include('blog.urls'))]\n"
        app = b"from django.urls import include, path\nurlpatterns = [path('v1/', include('blog.api.urls')), path('home/', home)]\n"
        api = b"from django.urls import path\nurlpatterns = [path('items/', items)]\n"
        parsed = {
            "site/urls.py": python.analyze(root, "site/urls.py"),
            "blog/urls.py": python.analyze(app, "blog/urls.py"),
            "blog/api/urls.py": python.analyze(api, "blog/api/urls.py"),
        }
        builder = GraphBuilder(parsed)
        builder.build()
        builder.apply_route_prefixes()
        assert [e.path for e in parsed["blog/api/urls.py"].endpoints] == ["/api/v1/items/"]
        assert [e.path for e in parsed["blog/urls.py"].endpoints] == ["/api/home/"]
        assert parsed["site/urls.py"].endpoints == [], "an include is not itself a route"
        assert any(e.kind == "include" and e.target_path == "blog/urls.py" for e in builder.edges)

    def test_drf_router(self):
        source = b"router = DefaultRouter()\nrouter.register(r'articles', ArticleViewSet)\nurlpatterns = [path('', include(router.urls))]\n"
        parsed = {"a/urls.py": python.analyze(source, "a/urls.py")}
        builder = GraphBuilder(parsed)
        builder.build()
        builder.apply_route_prefixes()
        assert [(e.path, e.handler) for e in parsed["a/urls.py"].endpoints] == [("/articles", "ArticleViewSet")]

    def test_nest_global_prefix(self):
        parsed = {
            "main.ts": javascript.analyze(b"app.setGlobalPrefix('api');", "main.ts"),
            "c.ts": javascript.analyze(b"@Controller('cats')\nexport class C {\n  @Get()\n  all() {}\n}", "c.ts"),
        }
        builder = GraphBuilder(parsed)
        builder.build()
        builder.apply_route_prefixes()
        assert parsed["c.ts"].endpoints[0].path == "/api/cats"

    def test_django_model_with_project_base_class(self):
        source = b"class Article(TimestampedModel):\n    title = models.CharField(max_length=9)\n    author = models.ForeignKey('User', on_delete=1)\n"
        model = python.analyze(source, "models.py").models[0]
        assert (model.name, model.framework) == ("Article", "django")
        assert model.relations[0]["target"] == "User"

    def test_pydantic_and_sqlmodel_without_table_are_not_database_models(self):
        source = (
            b"class ItemBase(SQLModel):\n    title: str\n"
            b"class Message(BaseModel):\n    text: str\n"
            b"class Item(ItemBase, table=True):\n    id: int = Field(primary_key=True)\n    owner: 'User' = Relationship(back_populates='items')\n"
        )
        models = python.analyze(source, "models.py").models
        assert [(x.name, x.framework) for x in models] == [("Item", "sqlmodel")]
        assert models[0].fields == [{"name": "id", "type": "int"}]
        assert models[0].relations[0]["type"] == "Relationship"

    def test_laravel_api_file_prefix(self):
        source = b"<?php Route::get('/x', 'A@b');"
        assert php.analyze(source, "routes/api.php").endpoints[0].path == "/api/x"
        assert php.analyze(source, "routes/web.php").endpoints[0].path == "/x"

    def test_drupal_entity_base_fields(self):
        source = b"""<?php
/**
 * @ContentEntityType(
 *   id = "order",
 * )
 */
class Order extends ContentEntityBase {
  public static function baseFieldDefinitions($entity_type) {
    $fields['mail'] = BaseFieldDefinition::create('email')->setLabel(t('Mail'));
    $fields['state'] = BaseFieldDefinition::create('state');
    return $fields;
  }
}"""
        model = php.analyze(source, "Order.php").models[0]
        assert model.fields == [{"name": "mail", "type": "email"}, {"name": "state", "type": "state"}]

    def test_duplicate_tables_from_migrations_are_merged(self, client, upload, auth):
        extra = {
            "prisma/schema.prisma": "model User {\n  id Int @id\n}\n",
            "prisma/migrations/1/migration.sql": 'CREATE TABLE "User" ("id" INT);\nCREATE TABLE "Log" ("id" INT);\n',
            "prisma/migrations/2/migration.sql": 'CREATE TABLE "Log" ("id" INT);\n',
        }
        project = upload("plain_php", extra=extra)
        models = client.get(f"{_base(project)}/models", headers=auth).json()["items"]
        assert sorted((x["name"], x["framework"]) for x in models) == [("Log", "sql"), ("User", "prisma")]

    def test_barrel_files_are_not_entry_points(self):
        paths = ["src/index.js", "src/models/index.js", "public/index.php", "app/main.py"]
        assert set(find_entry_points(paths, [])) == {"src/index.js", "app/main.py", "public/index.php"}


class TestDynamicStrings:
    def test_concatenation_is_not_a_string_literal(self):
        source = b"<?php Route::get('/a' . $b . '/c', 'X@y'); Route::get('/plain', 'X@z');"
        assert [e.path for e in php.analyze(source, "routes/web.php").endpoints] == ["/plain"]

    def test_drupal_menu_path_built_at_runtime(self):
        source = (
            b"<?php function m_menu() {\n"
            b"  $items['list/' . $key . '/add'] = array('page callback' => 'a');\n"
            b"  $items[\"$path/fields\"] = array('page callback' => 'b');\n"
            b"  return $items;\n}"
        )
        paths = [e.path for e in php.analyze(source, "m/m.module").endpoints]
        assert paths == ["/list/{key}/add", "/{path}/fields"]

    def test_javascript_expression_is_not_a_route_path(self):
        assert javascript.analyze(b"router.get('/a' + b + '/c', handler);", "r.js").endpoints == []


class TestFalsePositives:
    def test_method_named_like_a_removed_function(self):
        assert scan_lines("a.php", "php", "source", "public function split(array $x) {") == []

    def test_real_call_is_still_found(self):
        assert [f.rule for f in scan_lines("a.php", "php", "source", "$p = split(',', $x);")] == ["php-removed-ereg"]

    def test_blade_directives_are_not_php(self):
        assert scan_lines("v.blade.php", "php", "view", "@include('a')\n<script>x.split(',')</script>") == []


class TestCompareParsing:
    def _repo(self, tmp_path, before, after):
        repo = tmp_path / "r"
        repo.mkdir()
        for name, content in before.items():
            (repo / name).write_text(content)
        base = git.init_snapshot(repo, "first")
        for name, content in after.items():
            if content is None:
                (repo / name).unlink()
            else:
                (repo / name).write_text(content)
        head = git.commit_all(repo, "second")
        return repo, base, head

    def test_diff_content_is_not_mistaken_for_a_file_header(self, tmp_path):
        repo, base, head = self._repo(
            tmp_path,
            {"a.sql": "-- old comment\nselect 1;\n", "b.py": "x = 1\n"},
            {"a.sql": "++ odd\nselect 1;\n", "b.py": "x = 2\n"},
        )
        files = {f.path: f for f in compare.changed_files(repo, base, head)}
        assert files["a.sql"].added_lines == {1}
        assert files["b.py"].added_lines == {1}, "ranges must not be lost after odd content lines"

    def test_rename_and_file_names_with_spaces_and_unicode(self, tmp_path):
        content = "def keep():\n    return 1\n" * 20
        repo, base, head = self._repo(
            tmp_path,
            {"old name.py": content, "é.py": "x = 1\n"},
            {"old name.py": None, "new name.py": content, "é.py": "x = 2\n"},
        )
        files = {f.path: f for f in compare.changed_files(repo, base, head)}
        assert files["new name.py"].status == "renamed"
        assert files["new name.py"].old_path == "old name.py"
        assert files["é.py"].added_lines == {1}

    def test_class_is_not_listed_when_only_its_method_changed(self, tmp_path):
        before = "class A:\n    def run(self):\n        return 1\n"
        after = "class A:\n    def run(self):\n        return 2\n"
        repo, base, head = self._repo(tmp_path, {"a.py": before}, {"a.py": after})
        file = compare.changed_files(repo, base, head)[0]
        changes, _ = compare.diff_symbols(repo, base, head, file)
        assert [c["qualified_name"] for c in changes] == ["A.run"]

    def test_same_commit_is_refused(self, client, upload, auth):
        project = upload("node_app")
        response = client.post(f"{_base(project)}/compare", headers=auth, json={"base": "HEAD", "head": "HEAD"})
        assert response.status_code == 422

    def test_endpoint_handler_match_uses_whole_names(self, client, auth):
        """A changed function named `list` must not flag a handler named `playlist`."""
        assert not {"list"} & set(compare._HANDLER_PARTS_RE.split("App\\Http\\PlaylistController@playlist"))
        assert {"list"} & set(compare._HANDLER_PARTS_RE.split("App\\Http\\UserController@list"))


class TestRetrieval:
    def test_production_code_ranks_above_tests(self, client, upload, auth):
        project = upload("python_app")
        results = client.get(f"{_base(project)}/search", params={"q": "final_price"}, headers=auth).json()
        assert results[0]["path"] == "shop/services/pricing.py"

    def test_tests_rank_normally_when_asked_for(self, client, upload, auth):
        project = upload("python_app")
        results = client.get(f"{_base(project)}/search", params={"q": "test final_price tests"}, headers=auth).json()
        assert "tests/test_pricing.py" in [r["path"] for r in results[:3]]

    def test_large_projects_skip_tests_and_respect_the_cap(self):
        rows = [
            retrieval.ChunkRow(1, "src/a.py", None, "x", "source"),
            retrieval.ChunkRow(2, "src/b.py", "b", "x", "source"),
            retrieval.ChunkRow(3, "tests/a.py", "t", "x", "test"),
            retrieval.ChunkRow(4, "src/c.py", "c", "x", "source"),
        ]
        settings = get_settings().model_copy(update={"embed_tests_below_chunks": 2, "max_embedded_chunks": 2})
        assert [row.chunk_id for row in retrieval.choose_chunks_to_embed(rows, settings)] == [2, 4]
        small = get_settings().model_copy(update={"embed_tests_below_chunks": 100, "max_embedded_chunks": 100})
        assert len(retrieval.choose_chunks_to_embed(rows, small)) == 4

    def test_unchanged_code_is_not_embedded_again(self, client, upload, auth):
        class Counting(FakeEmbedder):
            texts = 0

            def embed(self, texts):
                Counting.texts += len(texts)
                return super().embed(texts)

        retrieval.set_embedder(Counting())
        project = upload("python_app")
        first = Counting.texts
        assert first > 0
        client.post(f"{_base(project)}/reanalyze", headers=auth)
        assert Counting.texts == first, "a reanalysis of the same code must reuse its vectors"
        status = client.get(_base(project), headers=auth).json()["search_index"]
        assert status["reused"] == status["embedded"] > 0
        results = client.get(f"{_base(project)}/search", params={"q": "tax on an amount"}, headers=auth).json()
        assert results, "the rebuilt index must work"

    def test_plain_words_match_code_names(self):
        """Measured on laravel.io: hit@3 went from 8 to 15 of 16 questions with root words."""
        terms = retrieval.extract_terms("Who gets notified when someone replies, and is a user banned or published?")
        assert {"notif", "repl", "ban", "publish"} <= set(terms)
        assert retrieval._stem("articles") == "article"
        assert retrieval._stem("class") == "class"
        assert retrieval.extract_terms("Where is SendNewReply used in app/Jobs?")[0:2] == ["SendNewReply", "app/Jobs"]

    def test_follow_up_question_searches_with_the_previous_one(self):
        previous = m.Message(role="user", content="How does final_price work?")
        assert "final_price" in _search_query("Where is that used?", [previous])
        long_question = "Please explain in detail how the whole payment and invoice system of this project works"
        assert _search_query(long_question, [previous]) == long_question
        assert _search_query("Where?", []) == "Where?"

    def test_chat_does_not_hold_a_database_transaction_during_the_ai_call(self, client, upload, auth):
        project = upload("python_app")
        seen = []

        def answer(request):
            with get_engine().connect() as connection:
                seen.append(
                    connection.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction' AND datname = current_database()"
                        )
                    ).scalar()
                )
            return gemini_ok("Done.")

        with respx.mock:
            respx.post(GEMINI_URL).mock(side_effect=answer)
            assert (
                client.post(f"{_base(project)}/chat", headers=auth, json={"message": "final_price"}).status_code == 200
            )
        assert seen == [0]

    def test_vector_cache_survives_bad_file(self, tmp_path):
        (tmp_path / "embedding_cache.npz").write_text("garbage")
        assert retrieval._load_vector_cache(tmp_path, "m") == {}
        np.savez(
            tmp_path / "embedding_cache.npz",
            model=np.asarray("other"),
            hashes=np.asarray(["a"]),
            vectors=np.zeros((1, 2)),
        )
        assert retrieval._load_vector_cache(tmp_path, "m") == {}


class TestBackgroundIndexing:
    def test_project_is_usable_while_the_index_builds(self, client, auth):
        """Large projects take many minutes to embed, and that must not block the analysis."""
        seen = {}

        class Observing(FakeEmbedder):
            def embed(self, texts):
                if not seen:
                    with session_scope() as db:
                        project = db.scalars(select(m.Project)).one()
                        seen["status"] = project.status
                        seen["index"] = project.summary["search_index"]["status"]
                        seen["metrics"] = project.summary["metrics"]["source_files"]
                        seen["search"] = retrieval.search(db, project.id, "final_price")[0].path
                return super().embed(texts)

        retrieval.set_embedder(Observing())
        data = zip_directory(FIXTURES / "python_app")
        response = client.post("/api/projects/upload", headers=auth, files={"file": ("a.zip", data)})
        assert response.status_code == 202
        assert seen["status"] == "ready"
        assert seen["index"] == "building"
        assert seen["metrics"] > 0
        assert seen["search"] == "shop/services/pricing.py", "keyword search works during indexing"
        final = client.get(f"/api/projects/{response.json()['id']}", headers=auth).json()
        assert final["search_index"]["status"] == "ready"
        assert final["search_index"]["embedded"] > 0

    def test_deleting_a_project_stops_its_indexing(self, client, auth):
        calls = {"batches": 0}

        class Deleting(FakeEmbedder):
            def embed(self, texts):
                calls["batches"] += 1
                if calls["batches"] == 1:
                    with session_scope() as db:
                        project = db.scalars(select(m.Project)).one()
                        calls["id"] = str(project.id)
                        db.delete(project)
                return super().embed(texts)

        retrieval.set_embedder(Deleting())
        extra = {f"m{i}.py": f"def f{i}():\n    return {i}\n" for i in range(150)}
        data = zip_directory(FIXTURES / "python_app", extra=extra)
        assert client.post("/api/projects/upload", headers=auth, files={"file": ("a.zip", data)}).status_code == 202
        assert calls["batches"] == 1, "the next batch must not run after the project is gone"
        assert not (get_settings().indexes_dir / calls["id"]).exists()

    def test_a_newer_analysis_stops_the_older_index(self, client, upload, auth):
        project = upload("python_app")
        base = _base(project)
        calls = {"batches": 0}
        newer = __import__("datetime").datetime(2030, 1, 1, tzinfo=__import__("datetime").UTC)

        class Superseded(FakeEmbedder):
            def embed(self, texts):
                calls["batches"] += 1
                if calls["batches"] == 1:
                    with session_scope() as db:
                        db.scalars(select(m.Project)).one().analyzed_at = newer
                return super().embed(texts)

        retrieval.set_embedder(Superseded())
        extra = {f"m{i}.py": f"def f{i}():\n    return {i}\n" for i in range(150)}
        changed = zip_directory(FIXTURES / "python_app", extra=extra)
        assert client.post(f"{base}/versions", headers=auth, files={"file": ("v.zip", changed)}).status_code == 202
        assert calls["batches"] == 1
        assert client.get(base, headers=auth).json()["search_index"]["status"] == "building", "the newer job reports it"


class TestInsightsApi:
    def test_search_filters(self, client, upload, auth):
        project = upload("laravel_app")
        base = _base(project)
        users = client.get(f"{base}/endpoints", params={"q": "admin/users"}, headers=auth).json()
        assert users["total"] == 2
        assert all("admin/users" in e["path"] for e in users["items"])
        assert client.get(f"{base}/models", params={"q": "app_users"}, headers=auth).json()["total"] == 2
        assert client.get(f"{base}/dependencies", params={"q": "guzzle"}, headers=auth).json()["total"] == 1
        # Search text is data. A percent sign must not match everything.
        assert client.get(f"{base}/endpoints", params={"q": "%"}, headers=auth).json()["total"] == 0

    def test_file_tree(self, client, upload, auth):
        project = upload("laravel_app")
        base = _base(project)
        root = client.get(f"{base}/tree", headers=auth).json()
        assert "app" in {f["name"] for f in root["folders"]}
        assert "composer.json" in {f["name"] for f in root["files"]}
        app = client.get(f"{base}/tree", params={"directory": "app/Models"}, headers=auth).json()
        assert {f["name"] for f in app["files"]} == {"User.php", "Post.php"}
        assert client.get(f"{base}/tree", params={"directory": "%"}, headers=auth).json() == {
            "directory": "%",
            "folders": [],
            "files": [],
        }

    def test_issues_are_sorted_by_severity(self, client, upload, auth):
        project = upload("plain_php")
        severities = [
            i["severity"] for i in client.get(f"{_base(project)}/issues?limit=200", headers=auth).json()["items"]
        ]
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        assert severities == sorted(severities, key=order.__getitem__)

    def test_symbol_graph_hides_tests_by_default(self, client, upload, auth):
        project = upload("python_app")
        base = _base(project)
        hidden = client.get(f"{base}/graph?level=symbol", headers=auth).json()
        shown = client.get(f"{base}/graph?level=symbol&include_tests=true", headers=auth).json()
        assert not any(n["path"].startswith("tests/") for n in hidden["nodes"])
        assert any(n["path"].startswith("tests/") for n in shown["nodes"])

    def test_module_graph_describes_production_code(self, client, upload, auth):
        project = upload("node_app")
        names = {n["id"] for n in client.get(f"{_base(project)}/graph?level=module", headers=auth).json()["nodes"]}
        assert "test" not in names and names

    def test_named_import_counts_as_usage(self):
        parsed = {
            "a.js": javascript.analyze(b"const { helper } = require('./b');\nconst fn = helper;", "a.js"),
            "b.js": javascript.analyze(b"function helper() {}\nmodule.exports = { helper };", "b.js"),
        }
        edges = GraphBuilder(parsed).build()
        assert any(e.source_path == "a.js" and e.target_symbol == "helper" for e in edges)


class TestProviderErrorsFromRealResponses:
    """Bodies captured from the real Gemini and Groq APIs using invalid keys."""

    @respx.mock
    def test_gemini_invalid_key(self):
        from app.services.llm.router import build_router

        body = {
            "error": {
                "code": 400,
                "message": "API key not valid. Please pass a valid API key.",
                "status": "INVALID_ARGUMENT",
                "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}],
            }
        }
        respx.post(GEMINI_URL).mock(return_value=httpx.Response(400, json=body))
        settings = get_settings().model_copy(update={"groq_api_key": None})
        respx.post("http://ollama.test:11434/api/chat").mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(Exception) as caught:
            build_router(settings).generate(
                [__import__("app.services.llm", fromlist=["ChatMessage"]).ChatMessage("user", "hi")]
            )
        assert caught.value.attempts[0]["reason"] == "auth"


def test_oversized_extraction_message_is_clear(tmp_path):
    settings = get_settings().model_copy(update={"max_extracted_mb": 1, "max_compression_ratio": 10**9})
    archive = tmp_path / "a.zip"
    archive.write_bytes(_zip({"a.txt": "0" * (2 * 1024 * 1024), "b.php": "<?php"}))
    with pytest.raises(TooLargeError, match="limit"):
        import_zip(tmp_path / "repo", archive, settings, "x")


def test_project_delete_leaves_no_orphans(client, upload, auth):
    project = upload("laravel_app")
    base = _base(project)
    client.post(f"{base}/docs", headers=auth, json={"use_ai": False})
    client.post(f"{base}/tests/generate", headers=auth, json={"target_path": "app/Models/User.php", "use_ai": False})
    assert client.delete(base, headers=auth).status_code == 204
    with session_scope() as db:
        for model in (
            m.Project,
            m.Job,
            m.SourceFile,
            m.Symbol,
            m.ApiEndpoint,
            m.DataModel,
            m.PackageDependency,
            m.GraphEdge,
            m.CodeChunk,
            m.QualityIssue,
            m.Conversation,
            m.Message,
            m.Comparison,
            m.GeneratedTest,
            m.TestRun,
            m.Document,
        ):
            assert db.scalar(select(func.count()).select_from(model)) == 0, model.__name__


def test_migrations_run_down_and_up():
    """Downgrade and upgrade on a scratch schema, so a bad rollback is found before production."""
    import os
    import uuid

    import psycopg
    from alembic.config import Config

    from alembic import command

    url = os.environ["DATABASE_URL"]
    admin = url.replace("postgresql+psycopg://", "postgresql://").rsplit("/", 1)[0] + "/postgres"
    name = f"ll_migrations_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    backend = FIXTURES.parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = url.rsplit("/", 1)[0] + "/" + name
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    finally:
        os.environ["DATABASE_URL"] = url
        get_settings.cache_clear()
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
