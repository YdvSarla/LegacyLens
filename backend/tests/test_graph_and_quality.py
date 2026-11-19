from app.services.analysis.graph import Edge, GraphBuilder, find_import_cycles
from app.services.analysis.languages import javascript, php, python
from app.services.analysis.parser import parse_source
from app.services.analysis.quality import compute_scores, cycle_findings, scan_lines, structural_findings
from app.services.analysis.scanner import classify_role, is_sensitive, scan_repository


def _build(files, analyzer):
    parsed = {path: analyzer(source.encode(), path) for path, source in files.items()}
    builder = GraphBuilder(parsed)
    return builder.build(), builder


def _pairs(edges, kind=None):
    return {(e.source_path, e.target_path) for e in edges if kind is None or e.kind == kind}


class TestGraph:
    def test_python_relative_and_absolute_imports(self):
        edges, _ = _build(
            {
                "pkg/__init__.py": "",
                "pkg/a.py": "from .b import helper\nfrom pkg.c import Klass\ndef run():\n    helper()\n    Klass()\n",
                "pkg/b.py": "def helper():\n    pass\n",
                "pkg/c.py": "class Klass:\n    pass\n",
            },
            python.analyze,
        )
        assert _pairs(edges, "import") == {("pkg/a.py", "pkg/b.py"), ("pkg/a.py", "pkg/c.py")}
        calls = {(e.source_symbol, e.target_path, e.target_symbol) for e in edges if e.kind == "call"}
        assert ("run", "pkg/b.py", "helper") in calls
        assert ("run", "pkg/c.py", "Klass") in calls

    def test_importing_submodules_does_not_depend_on_the_package_init(self):
        edges, _ = _build(
            {
                "pkg/__init__.py": "from .a import run\n",
                "pkg/a.py": "from . import b\ndef run():\n    b.go()\n",
                "pkg/b.py": "def go():\n    pass\n",
            },
            python.analyze,
        )
        assert ("pkg/a.py", "pkg/b.py") in _pairs(edges, "import")
        assert ("pkg/a.py", "pkg/__init__.py") not in _pairs(edges)
        assert find_import_cycles(edges) == []

    def test_python_module_alias_call(self):
        edges, _ = _build(
            {"a.py": "import b as bee\ndef go():\n    bee.work()\n", "b.py": "def work():\n    pass\n"},
            python.analyze,
        )
        assert any(e.kind == "call" and e.target_symbol == "work" for e in edges)

    def test_stdlib_imports_create_no_edges(self):
        edges, _ = _build({"a.py": "import os\nfrom json import loads\n"}, python.analyze)
        assert edges == []

    def test_python_self_call_resolves_within_class(self):
        edges, _ = _build(
            {"a.py": "class A:\n    def x(self):\n        return self.y()\n    def y(self):\n        return 1\n"},
            python.analyze,
        )
        assert any(e.source_symbol == "A.x" and e.target_symbol == "A.y" for e in edges)

    def test_js_resolution_of_index_extension_and_alias(self):
        edges, _ = _build(
            {
                "src/a.js": "const b = require('./b'); const c = require('./lib'); import d from '@/d';\nb.go();",
                "src/b.js": "exports.go = function () {};",
                "src/lib/index.js": "",
                "src/d.ts": "export default 1;",
            },
            javascript.analyze,
        )
        assert _pairs(edges, "import") == {
            ("src/a.js", "src/b.js"),
            ("src/a.js", "src/lib/index.js"),
            ("src/a.js", "src/d.ts"),
        }
        assert any(e.kind == "call" and e.target_symbol == "go" for e in edges)

    def test_ts_source_imported_with_js_extension(self):
        edges, _ = _build(
            {"a.ts": "import { b } from './b.js';\nb();", "b.ts": "export function b() {}"},
            javascript.analyze,
        )
        assert ("a.ts", "b.ts") in _pairs(edges, "import")

    def test_js_packages_create_no_edges(self):
        edges, _ = _build({"a.js": "require('express'); import x from 'lodash';"}, javascript.analyze)
        assert edges == []

    def test_php_classes_inheritance_traits_and_typed_calls(self):
        edges, _ = _build(
            {
                "a.php": "<?php namespace App; use App\\S\\Svc; class A extends Base implements I { use T;"
                " public function __construct(private Svc $svc) {} public function go() { $this->svc->run(); } }",
                "base.php": "<?php namespace App; class Base {}",
                "i.php": "<?php namespace App; interface I {}",
                "t.php": "<?php namespace App; trait T {}",
                "svc.php": "<?php namespace App\\S; class Svc { public function run() {} }",
            },
            php.analyze,
        )
        assert ("a.php", "base.php") in _pairs(edges, "extends")
        assert ("a.php", "i.php") in _pairs(edges, "implements")
        assert ("a.php", "t.php") in _pairs(edges, "uses_trait")
        assert ("a.php", "svc.php") in _pairs(edges, "use")
        call = next(e for e in edges if e.kind == "call")
        assert (call.source_symbol, call.target_symbol) == ("App\\A::go", "App\\S\\Svc::run")

    def test_php_include_and_global_function(self):
        edges, _ = _build(
            {
                "index.php": "<?php require_once dirname(__FILE__) . '/lib/db.php'; include 'lib/h.php'; fetch();",
                "lib/db.php": "<?php function fetch() {}",
                "lib/h.php": "<?php",
            },
            php.analyze,
        )
        assert _pairs(edges, "include") == {("index.php", "lib/db.php"), ("index.php", "lib/h.php")}
        assert any(e.kind == "call" and e.target_path == "lib/db.php" for e in edges)

    def test_laravel_route_edges_reach_controller_methods(self):
        edges, _ = _build(
            {
                "routes/web.php": "<?php use App\\C; Route::get('/x', [C::class, 'go']);",
                "app/C.php": "<?php namespace App; class C { public function go() {} }",
            },
            php.analyze,
        )
        route = next(e for e in edges if e.kind == "route")
        assert route.target_symbol == "App\\C::go"

    def test_ambiguous_unresolved_call_creates_no_edge(self):
        edges, _ = _build(
            {"a.php": "<?php $x->save();", "b.php": "<?php class B { public function save() {} }"},
            php.analyze,
        )
        assert not [e for e in edges if e.kind == "call"], "guessing would invent dependencies"

    def test_express_mount_prefix_applies_across_files(self):
        parsed = {
            "server.js": javascript.analyze(b"const r = require('./routes');\napp.use('/api/v1', r);", "server.js"),
            "routes.js": javascript.analyze(b"const router = express.Router();\nrouter.get('/users', h);", "routes.js"),
        }
        builder = GraphBuilder(parsed)
        builder.build()
        builder.apply_express_mounts()
        assert parsed["routes.js"].endpoints[0].path == "/api/v1/users"

    def test_imports_that_do_not_run_at_load_time_are_not_cycles(self):
        edges = [
            Edge("a", "b", "import"),
            Edge("b", "a", "type_import"),
            Edge("c", "d", "use"),
            Edge("d", "c", "use"),
            Edge("e", "f", "import"),
            Edge("f", "e", "lazy_import"),
        ]
        assert find_import_cycles(edges) == []

    def test_php_classes_using_each_other_are_not_a_cycle(self):
        edges, _ = _build(
            {
                "user.php": "<?php namespace App; use App\\UserFactory; class User {}",
                "factory.php": "<?php namespace App; use App\\User; class UserFactory {}",
            },
            php.analyze,
        )
        assert ("user.php", "factory.php") in _pairs(edges, "use")
        assert find_import_cycles(edges) == []

    def test_python_type_checking_import_is_not_a_cycle(self):
        edges, _ = _build(
            {
                "a.py": "import typing as t\nif t.TYPE_CHECKING:\n    from b import B\n",
                "b.py": "from a import A\nclass B:\n    pass\n",
            },
            python.analyze,
        )
        assert ("a.py", "b.py") in _pairs(edges, "type_import")
        assert find_import_cycles(edges) == []

    def test_cycles(self):
        edges = [
            Edge("a", "b", "import"),
            Edge("b", "c", "import"),
            Edge("c", "a", "import"),
            Edge("c", "d", "import"),
            Edge("x", "y", "call"),
            Edge("y", "x", "call"),
        ]
        assert find_import_cycles(edges) == [["a", "b", "c"]]


class TestQualityRules:
    def _rules(self, language, code, role="source"):
        return {f.rule for f in scan_lines("x", language, role, code)}

    def test_php_security_rules(self):
        assert "dangerous-eval" in self._rules("php", "<?php eval($x);")
        assert "php-removed-mysql" in self._rules("php", "$r = mysql_query($q);")
        assert "php-echo-input" in self._rules("php", "echo $_GET['a'];")
        assert "sql-string-building" in self._rules("php", '$q = "SELECT * FROM t WHERE a = " . $x;')
        assert "php-request-in-include" in self._rules("php", "include $_GET['p'];")
        assert "weak-password-hash" in self._rules("php", "md5($password)")

    def test_php_methods_named_like_dangerous_functions_are_not_flagged(self):
        assert "php-shell-exec" not in self._rules("php", "$obj->system('x');")
        assert "dangerous-eval" not in self._rules("php", "$obj->eval('x');")
        assert "php-removed-ereg" not in self._rules("php", "Str::split('x');")

    def test_python_rules(self):
        assert "py-subprocess-shell" in self._rules("python", "subprocess.run(c, shell=True)")
        assert "py-pickle-load" in self._rules("python", "pickle.loads(b)")
        assert "py-yaml-unsafe" in self._rules("python", "yaml.load(f)")
        assert "py-yaml-unsafe" not in self._rules("python", "yaml.load(f, Loader=yaml.SafeLoader)")
        assert "sql-string-building" in self._rules("python", 'q = f"SELECT * FROM t WHERE a={x}"')
        assert "py2-print" in self._rules("python", 'print "hello"')
        assert "py2-print" not in self._rules("python", 'print("hello")')

    def test_js_rules(self):
        assert "dangerous-eval" in self._rules("javascript", "eval(x)")
        assert "js-inner-html" in self._rules("javascript", "el.innerHTML = x")
        assert "debug-js" in self._rules("javascript", "console.log(1)")

    def test_comments_are_ignored_except_todo(self):
        assert self._rules("php", "// eval($x);") == set()
        assert self._rules("python", "# TODO: fix") == {"todo-marker"}

    def test_hardcoded_secret_ignores_empty_and_variables(self):
        assert "hardcoded-secret" in self._rules("python", 'password = "hunter2hunter2"')
        assert "hardcoded-secret" not in self._rules("python", 'password = ""')
        assert "hardcoded-secret" not in self._rules("php", '$password = $_POST["password"];')
        assert "hardcoded-secret" not in self._rules("javascript", "password: process.env.PASSWORD")

    def test_security_rules_skip_tests_and_examples(self):
        assert self._rules("python", "eval(x)", role="test") == set()
        assert self._rules("javascript", "password: 'realvalue1'", role="example") == set()

    def test_values_that_only_look_like_secrets(self):
        for line in (
            "'password' => 'hashed',",
            "password: 'foobar'",
            "api_key = 'your-api-key'",
            'secret = "changeme"',
        ):
            assert "hardcoded-secret" not in self._rules("php", line), line
        assert "hardcoded-secret" in self._rules("php", "'password' => 'Tr0ub4dor3',")

    def test_findings_have_correct_line_numbers(self):
        findings = scan_lines("x", "python", "source", "a = 1\nb = 2\neval(a)\n")
        assert [(f.rule, f.line) for f in findings] == [("dangerous-eval", 3)]

    def test_per_file_cap_prevents_flooding(self):
        assert len(scan_lines("x", "python", "source", "eval(a)\n" * 100)) == 5

    def test_structural_rules(self):
        body = "\n".join(f"    if a{i}:\n        pass" for i in range(25))
        parsed = python.analyze(f"def big(a, b, c, d, e, f, g, h):\n{body}\n".encode(), "x.py")
        rules = {(f.rule, f.severity) for f in structural_findings("x.py", parsed, 60, "source")}
        assert ("high-complexity", "medium") in rules
        assert ("too-many-parameters", "low") in rules

    def test_cycle_findings(self):
        assert cycle_findings([["a", "b"]])[0].rule == "circular-dependency"


class TestScores:
    def test_clean_code_scores_full_marks(self):
        assert compute_scores([], 5000) == {
            "overall": 100,
            "grade": "A",
            "categories": {"maintainability": 100, "security": 100, "reliability": 100},
        }

    def test_issues_reduce_the_score_more_on_small_code(self):
        from app.services.analysis.quality import Finding

        findings = [Finding("r", "security", "critical", "m")] * 3
        small = compute_scores(findings, 1000)
        big = compute_scores(findings, 100_000)
        assert small["categories"]["security"] < big["categories"]["security"]
        assert small["categories"]["maintainability"] == 100


def test_serious_security_issues_limit_the_grade():
    from app.services.analysis.quality import Finding

    critical = compute_scores([Finding("r", "security", "critical", "m")], 100_000)
    high = compute_scores([Finding("r", "security", "high", "m")], 100_000)
    tidy = compute_scores([Finding("r", "maintainability", "high", "m")], 100_000)
    assert critical["overall"] == 59 and critical["grade"] == "D"
    assert high["overall"] == 89 and high["grade"] == "B"
    assert tidy["grade"] == "A"


class TestScanner:
    def test_roles(self):
        assert classify_role("tests/test_x.py") == "test"
        assert classify_role("src/foo.test.ts") == "test"
        assert classify_role("database/migrations/1_x.php") == "migration"
        assert classify_role("routes/web.php") == "route"
        assert classify_role("blog/urls.py") == "route"
        assert classify_role("app/Http/Controllers/A.php") == "controller"
        assert classify_role("app/Models/A.php") == "model"
        assert classify_role("resources/views/a.blade.php") == "view"
        assert classify_role("app/Services/B.php") == "service"
        assert classify_role("examples/auth/index.js") == "example"
        assert classify_role("docs/conf.py") == "example"

    def test_sensitive_files(self):
        for name in (".env", ".env.production", "a/b/id_rsa", "server.pem", "auth.json", "x/settings.local.php"):
            assert is_sensitive(name), name
        assert not is_sensitive("environment.py")

    def test_scan_skips_third_party_binary_minified_and_symlinks(self, tmp_path):
        (tmp_path / "vendor/x").mkdir(parents=True)
        (tmp_path / "vendor/x/a.php").write_text("<?php")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules/a.js").write_text("x")
        (tmp_path / "modules/contrib/views").mkdir(parents=True)
        (tmp_path / "modules/contrib/views/views.module").write_text("<?php")
        (tmp_path / "app.min.js").write_text("x")
        (tmp_path / "data.php").write_bytes(b"<?php \x00\x00")
        (tmp_path / "keep.php").write_text("<?php")
        outside = tmp_path.parent / "outside_secret.php"
        outside.write_text("<?php secret")
        (tmp_path / "link.php").symlink_to(outside)
        files = scan_repository(tmp_path, max_file_bytes=10_000, max_files=100)
        assert [f.path for f in files] == ["keep.php"]

    def test_scan_skips_drupal_core(self, tmp_path):
        (tmp_path / "core/lib").mkdir(parents=True)
        (tmp_path / "core/lib/Drupal.php").write_text("<?php")
        (tmp_path / "modules/custom").mkdir(parents=True)
        (tmp_path / "modules/custom/a.module").write_text("<?php")
        files = scan_repository(tmp_path, max_file_bytes=10_000, max_files=100)
        assert [f.path for f in files] == ["modules/custom/a.module"]


def test_parser_failure_does_not_stop_analysis(monkeypatch):
    from app.services.analysis import parser

    def boom(*_):
        raise RuntimeError("bad")

    monkeypatch.setitem(parser._ANALYZERS, "python", boom)
    assert parse_source("python", b"x", "x.py") is None
