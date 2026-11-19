"""Language analyzers, run on small inline samples and the fixture projects."""

from pathlib import Path

from app.services.analysis.frameworks import (
    detect_frameworks,
    parse_composer,
    parse_drupal_routing,
    parse_package_json,
    parse_prisma_models,
    parse_pyproject,
    parse_requirements,
    parse_sql_models,
)
from app.services.analysis.languages import javascript, php, python

FIXTURES = Path(__file__).parent / "fixtures"


def _names(parsed, kind=None):
    return {s.qualified_name for s in parsed.symbols if kind is None or s.kind == kind}


def _routes(parsed):
    return {(e.method, e.path, e.handler) for e in parsed.endpoints}


class TestPython:
    def test_symbols_lines_and_docstrings(self):
        parsed = python.analyze((FIXTURES / "python_app/shop/services/pricing.py").read_bytes(), "pricing.py")
        final_price = next(s for s in parsed.symbols if s.name == "final_price")
        assert (final_price.start_line, final_price.end_line) == (5, 9)
        assert final_price.docstring == "Apply tax to an amount."
        assert final_price.param_count == 2
        assert "Cart.add" in _names(parsed, "method")
        assert "Cart" in _names(parsed, "class")

    def test_complexity_counts_branches_and_boolean_operators(self):
        source = b"def f(a, b):\n    if a and b:\n        return 1\n    for x in a:\n        pass\n    return 2\n"
        symbol = python.analyze(source, "x.py").symbols[0]
        assert symbol.complexity == 4  # 1 + if + and + for

    def test_nested_function_complexity_is_not_counted_in_parent(self):
        source = b"def outer():\n    def inner(a):\n        if a:\n            pass\n    return inner\n"
        by_name = {s.name: s for s in python.analyze(source, "x.py").symbols}
        assert by_name["outer"].complexity == 1
        assert by_name["inner"].complexity == 2

    def test_imports_including_relative_and_aliases(self):
        parsed = python.analyze(b"import os.path as op\nfrom .a.b import c as d\nfrom .. import e\n", "p/q/x.py")
        assert [(i.module, i.names, i.level) for i in parsed.imports] == [
            ("os.path", {"op": "*"}, 0),
            ("a.b", {"d": "c"}, 1),
            ("", {"e": "e"}, 2),
        ]

    def test_flask_blueprint_prefix_and_methods(self):
        parsed = python.analyze((FIXTURES / "python_app/shop/views.py").read_bytes(), "shop/views.py")
        assert _routes(parsed) == {
            ("GET", "/shop/price/<int:amount>", "price"),
            ("POST", "/shop/price/<int:amount>", "price"),
            ("GET", "/shop/orders", "orders"),
        }

    def test_fastapi_router_prefix(self):
        source = b"router = APIRouter(prefix='/api')\n@router.delete('/x/{id}')\ndef remove(id):\n    pass\n"
        assert _routes(python.analyze(source, "x.py")) == {("DELETE", "/api/x/{id}", "remove")}

    def test_get_decorator_framework_comes_from_imports(self):
        flask = b"from flask import Flask\napp = Flask(__name__)\n@app.get('/a')\ndef a():\n    pass\n"
        fastapi = b"from fastapi import FastAPI\napp = FastAPI()\n@app.get('/a')\ndef a():\n    pass\n"
        assert python.analyze(flask, "x.py").endpoints[0].framework == "flask"
        assert python.analyze(fastapi, "x.py").endpoints[0].framework == "fastapi"

    def test_decorator_that_only_looks_like_a_route_is_ignored(self):
        cache = b"from flask import Flask\n@cache.get('key')\ndef f():\n    pass\n"
        assert python.analyze(cache, "x.py").endpoints == []

    def test_router_imported_from_another_file_still_gives_a_route(self):
        source = b"from .deps import router\n@router.get('/a')\ndef f():\n    pass\n"
        endpoint = python.analyze(source, "x.py").endpoints[0]
        assert (endpoint.path, endpoint.framework) == ("/a", "python-web")

    def test_import_kinds(self):
        source = b"from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from a import A\nimport b\ndef f():\n    import c\n"
        kinds = {i.module: i.kind for i in python.analyze(source, "x.py").imports}
        assert kinds == {"typing": "import", "a": "type_import", "b": "import", "c": "lazy_import"}

    def test_django_urls_and_models(self):
        urls = python.analyze((FIXTURES / "python_app/blog/urls.py").read_bytes(), "blog/urls.py")
        assert {(e.path, e.handler) for e in urls.endpoints} == {
            ("/articles/", "views.article_list"),
            ("/articles/<int:pk>/", "views.ArticleDetail"),
            ("/old/(?P<y>\\d+)/", "views.article_list"),
        }
        models = python.analyze((FIXTURES / "python_app/blog/models.py").read_bytes(), "blog/models.py").models
        article = next(x for x in models if x.name == "Article")
        assert article.framework == "django"
        assert article.relations == [{"name": "author", "type": "ForeignKey", "target": "Author"}]

    def test_path_call_outside_urls_file_is_not_a_route(self):
        assert python.analyze(b"path('a/', view)\n", "other.py").endpoints == []

    def test_sqlalchemy_model(self):
        models = python.analyze((FIXTURES / "python_app/shop/models.py").read_bytes(), "shop/models.py").models
        order = next(x for x in models if x.name == "Order")
        assert order.table == "orders"
        assert {"name": "name", "type": "String"} in order.fields
        assert order.relations[0]["target"] == "OrderItem"

    def test_python2_code_still_yields_symbols(self):
        parsed = python.analyze((FIXTURES / "python_app/legacy_py2.py").read_bytes(), "legacy_py2.py")
        assert "fetch" in _names(parsed, "function")


class TestPhp:
    def test_namespace_inheritance_and_use_resolution(self):
        parsed = php.analyze((FIXTURES / "laravel_app/app/Http/Controllers/UserController.php").read_bytes(), "c.php")
        controller = next(s for s in parsed.symbols if s.kind == "class")
        assert controller.qualified_name == "App\\Http\\Controllers\\UserController"
        assert controller.bases == ["App\\Http\\Controllers\\Controller"]
        assert controller.docstring == "Manages users."
        assert "App\\Http\\Controllers\\UserController::store" in _names(parsed, "method")

    def test_type_hints_resolve_method_call_receivers(self):
        parsed = php.analyze((FIXTURES / "laravel_app/app/Http/Controllers/UserController.php").read_bytes(), "c.php")
        charge = next(c for c in parsed.calls if c.name == "charge")
        assert charge.receiver_type == "App\\Services\\BillingService"
        assert charge.scope == "App\\Http\\Controllers\\UserController::store"
        created = next(c for c in parsed.calls if c.is_new)
        assert created.receiver_type == "App\\Models\\User"

    def test_group_use_and_alias(self):
        parsed = php.analyze(b"<?php use A\\B\\{C, D as E}; use F\\G as H; new E(); new H();", "x.php")
        assert {i.module for i in parsed.imports} == {"A\\B\\C", "A\\B\\D", "F\\G"}
        assert {c.receiver_type for c in parsed.calls if c.is_new} == {"A\\B\\D", "F\\G"}

    def test_laravel_web_routes_with_groups_and_resource(self):
        parsed = php.analyze((FIXTURES / "laravel_app/routes/web.php").read_bytes(), "routes/web.php")
        routes = _routes(parsed)
        controller = "App\\Http\\Controllers\\UserController"
        assert ("GET", "/", "<closure>") in routes
        assert ("GET", "/admin/users", f"{controller}@index") in routes
        assert ("POST", "/admin/users", f"{controller}@store") in routes
        assert ("GET", "/admin/posts", "App\\Http\\Controllers\\PostController@index") in routes
        assert ("GET", "/admin/posts/{id}", "App\\Http\\Controllers\\PostController@show") in routes
        assert not any("create" in (r[2] or "") for r in routes), "`only` must limit the resource"
        secured = next(e for e in parsed.endpoints if e.path == "/admin/users" and e.method == "GET")
        assert secured.middleware == ["auth"]

    def test_laravel_api_resource_and_legacy_string_handler(self):
        parsed = php.analyze((FIXTURES / "laravel_app/routes/api.php").read_bytes(), "routes/api.php")
        routes = _routes(parsed)
        assert len([r for r in routes if r[1].startswith("/api/users")]) == 5, "routes/api.php is served under /api"
        assert ("GET", "/api/legacy/report", "App\\Http\\Controllers\\ReportController@show") in routes

    def test_legacy_route_group_array(self):
        source = (
            b"<?php Route::group(['prefix' => 'v1', 'middleware' => 'api'], function () { Route::any('x', 'A@b'); });"
        )
        endpoint = php.analyze(source, "r.php").endpoints[0]
        assert (endpoint.method, endpoint.path) == ("ANY", "/v1/x")
        assert endpoint.middleware == ["api"]

    def test_eloquent_model(self):
        models = php.analyze((FIXTURES / "laravel_app/app/Models/User.php").read_bytes(), "User.php").models
        assert len(models) == 1
        assert models[0].framework == "eloquent"
        assert models[0].table == "app_users"
        assert {"name": "email", "type": "string"} in models[0].fields
        assert models[0].relations == [{"name": "posts", "type": "hasMany", "target": "App\\Models\\Post"}]

    def test_modern_eloquent_attributes_and_casts_method(self):
        source = (
            b"<?php namespace App\\Models; use Illuminate\\Foundation\\Auth\\User as Authenticatable;\n"
            b"use Illuminate\\Database\\Eloquent\\Attributes\\Fillable;\n"
            b"#[Fillable(['name', 'password'])]\n"
            b"class User extends Authenticatable {\n"
            b"  protected function casts(): array { return ['password' => 'hashed']; }\n}\n"
        )
        model = php.analyze(source, "User.php").models[0]
        assert model.fields == [{"name": "name", "type": "fillable"}, {"name": "password", "type": "hashed"}]

    def test_laravel_migration(self):
        path = FIXTURES / "laravel_app/database/migrations/2024_01_01_000000_create_users_table.php"
        model = php.analyze(path.read_bytes(), "m.php").models[0]
        assert model.table == "app_users"
        assert {"name": "name", "type": "string"} in model.fields

    def test_drupal7_menu_schema_hooks_and_includes(self):
        module = php.analyze(
            (FIXTURES / "drupal7_site/sites/all/modules/legacy_mod/legacy_mod.module").read_bytes(),
            "sites/all/modules/legacy_mod/legacy_mod.module",
        )
        assert _routes(module) == {("ANY", "/admin/legacy/list", "legacy_mod_list_page")}
        assert {h.hook for h in module.hooks} == {"menu"}, "helpers must not look like hooks"
        assert [i.module for i in module.imports] == ["includes/helper.inc"]
        install = php.analyze(
            (FIXTURES / "drupal7_site/sites/all/modules/legacy_mod/legacy_mod.install").read_bytes(),
            "sites/all/modules/legacy_mod/legacy_mod.install",
        )
        assert install.models[0].table == "legacy_items"
        assert {"name": "title", "type": "varchar"} in install.models[0].fields

    def test_drupal8_hooks_and_entity_annotation(self):
        base = FIXTURES / "drupal_site/modules/custom/acme_reports"
        module = php.analyze((base / "acme_reports.module").read_bytes(), "acme_reports.module")
        assert {h.hook for h in module.hooks} == {"help", "form_alter"}
        entity = php.analyze((base / "src/Entity/Report.php").read_bytes(), "Report.php")
        assert entity.models[0].framework == "drupal-entity"
        assert entity.models[0].table == "acme_report"

    def test_superglobals_only_count_at_top_level(self):
        assert php.analyze(b"<?php $a = $_POST['x'];", "x.php").request_inputs == {"_POST"}
        assert php.analyze(b"<?php function f() { return $_GET['x']; }", "x.php").request_inputs == set()

    def test_old_style_php_with_html_parses(self):
        parsed = php.analyze(b"<html><?php function f() { return 1; } ?><b>x</b></html>", "x.php")
        assert "f" in _names(parsed, "function")


class TestJavaScript:
    def test_commonjs_exports_and_requires(self):
        parsed = javascript.analyze((FIXTURES / "node_app/src/controllers/userController.js").read_bytes(), "c.js")
        assert {"list", "create"} <= _names(parsed, "function")
        assert next(s for s in parsed.symbols if s.name == "list").docstring == "Lists all users."
        assert {i.module for i in parsed.imports} == {"../models/User", "../services/mailer"}

    def test_destructured_require_records_names(self):
        parsed = javascript.analyze(b"const { a, b: c } = require('./lib');", "x.js")
        assert parsed.imports[0].names == {"a": "a", "c": "b"}

    def test_express_routes_and_middleware(self):
        parsed = javascript.analyze((FIXTURES / "node_app/src/routes/users.js").read_bytes(), "users.js")
        assert _routes(parsed) == {("GET", "/", "controller.list"), ("POST", "/", "controller.create")}
        assert next(e for e in parsed.endpoints if e.method == "POST").middleware == ["requireAuth"]

    def test_import_kinds(self):
        source = (
            b"import type { A } from './a';\nimport { b } from './b';\nfunction f() { require('./c'); }\nimport('./d');"
        )
        kinds = {i.module: i.kind for i in javascript.analyze(source, "x.ts").imports}
        assert kinds == {"./a": "type_import", "./b": "import", "./c": "lazy_import", "./d": "lazy_import"}

    def test_map_get_is_not_a_route(self):
        assert javascript.analyze(b"cache.get('key', fallback); map.get(k);", "x.js").endpoints == []

    def test_mongoose_model_takes_schema_fields(self):
        models = javascript.analyze((FIXTURES / "node_app/src/models/User.js").read_bytes(), "User.js").models
        assert [m.name for m in models] == ["User"]
        assert {"name": "email", "type": "String"} in models[0].fields

    def test_nestjs_routes_and_typeorm_entity(self):
        base = FIXTURES / "nest_app/src"
        controller = javascript.analyze((base / "cats.controller.ts").read_bytes(), "cats.controller.ts")
        assert _routes(controller) == {
            ("GET", "/cats/:id", "CatsController.findOne"),
            ("POST", "/cats", "CatsController.create"),
        }
        entity = javascript.analyze((base / "cat.entity.ts").read_bytes(), "cat.entity.ts").models[0]
        assert (entity.framework, entity.table) == ("typeorm", "cats")
        assert entity.relations[0]["type"] == "OneToMany"

    def test_typescript_interfaces_and_implements(self):
        parsed = javascript.analyze((FIXTURES / "nest_app/src/cats.service.ts").read_bytes(), "cats.service.ts")
        assert "Finder" in _names(parsed, "interface")
        assert next(s for s in parsed.symbols if s.name == "CatsService").implements == ["Finder"]

    def test_tsx_component(self):
        parsed = javascript.analyze(b"export const App = () => <div>{1}</div>;\n", "App.tsx")
        assert "App" in _names(parsed, "function")
        assert parsed.parse_errors == 0

    def test_prototype_method(self):
        parsed = javascript.analyze(b"Foo.prototype.bar = function (a) { return a; };", "x.js")
        symbol = parsed.symbols[0]
        assert (symbol.kind, symbol.qualified_name, symbol.parent) == ("method", "Foo.bar", "Foo")


class TestManifests:
    def test_composer(self):
        info = parse_composer((FIXTURES / "laravel_app/composer.json").read_text(), "composer.json")
        assert info.php_version == "^8.1"
        names = {(p.name, p.is_dev) for p in info.packages}
        assert ("laravel/framework", False) in names
        assert ("phpunit/phpunit", True) in names

    def test_package_json(self):
        info = parse_package_json((FIXTURES / "node_app/package.json").read_text(), "package.json")
        assert info.entry_points == ["src/server.js"]
        assert info.node_engine == ">=18"
        assert ("jest", True) in {(p.name, p.is_dev) for p in info.packages}

    def test_requirements(self):
        info = parse_requirements(
            "# c\nflask==2.3\n-r other.txt\ndjango>=4 ; python_version>'3'\nreq[extra]~=1.0\n", "requirements.txt"
        )
        assert [(p.name, p.version) for p in info.packages] == [("flask", "==2.3"), ("django", ">=4"), ("req", "~=1.0")]

    def test_pyproject(self):
        info = parse_pyproject(
            '[project]\nrequires-python=">=3.9"\ndependencies=["a>=1"]\n[tool.poetry.dependencies]\npython="^3.9"\nb="^2"\n',
            "pyproject.toml",
        )
        assert info.python_version == ">=3.9"
        assert {p.name for p in info.packages} == {"a", "b"}

    def test_framework_detection(self):
        assert detect_frameworks({"artisan"}, []) == ["Laravel"]
        assert "Drupal" in detect_frameworks({"m/m.info.yml"}, [])
        assert "Drupal" in detect_frameworks({"sites/all/modules/x/x.module"}, [])
        assert detect_frameworks({"manage.py"}, []) == ["Django"]

    def test_drupal_routing(self):
        path = FIXTURES / "drupal_site/modules/custom/acme_reports/acme_reports.routing.yml"
        routes = parse_drupal_routing(path.read_text())
        assert {(r.method, r.path) for r in routes} == {
            ("ANY", "/admin/reports/acme"),
            ("GET", "/admin/config/acme"),
            ("POST", "/admin/config/acme"),
        }
        first = next(r for r in routes if r.path == "/admin/reports/acme")
        assert first.handler.endswith("ReportController::list")
        assert first.middleware == ["_permission: view acme reports"]
        assert first.line == 1

    def test_invalid_yaml_is_not_fatal(self):
        assert parse_drupal_routing("a: [unclosed") == []

    def test_sql_and_prisma(self):
        sql = parse_sql_models(
            "CREATE TABLE IF NOT EXISTS `users` (\n `id` INT NOT NULL,\n name VARCHAR(10),\n PRIMARY KEY (`id`)\n) ENGINE=InnoDB;"
        )
        assert sql[0].name == "users"
        assert sql[0].fields == [{"name": "id", "type": "INT"}, {"name": "name", "type": "VARCHAR(10)"}]
        prisma = parse_prisma_models("model User {\n  id Int @id\n  name String\n}\n")
        assert prisma[0].fields[1] == {"name": "name", "type": "String"}
