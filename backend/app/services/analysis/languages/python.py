"""Python analyzer: symbols, imports, calls, Flask/FastAPI/Django routes and models."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from tree_sitter import Node

from app.services.analysis.parsed import (
    CallRef,
    EndpointRef,
    ImportRef,
    ModelRef,
    ParsedFile,
    ParsedSymbol,
)
from app.services.analysis.treesitter import (
    complexity_of,
    count_errors,
    end_line,
    line,
    one_line,
    parse,
    string_value,
    text,
    walk,
)

_DECISIONS = frozenset(
    {
        "if_statement",
        "elif_clause",
        "for_statement",
        "while_statement",
        "except_clause",
        "conditional_expression",
        "for_in_clause",
        "if_clause",
        "case_clause",
    }
)
_NESTED = frozenset({"function_definition", "class_definition", "lambda"})
_HTTP_DECORATORS = {"get", "post", "put", "patch", "delete", "head", "options", "websocket"}
_DJANGO_FIELD_RELATIONS = {"ForeignKey", "OneToOneField", "ManyToManyField"}
_WEB_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+(flask|fastapi)\b", re.MULTILINE)
_SQLALCHEMY_BASES = {"Base", "db.Model", "DeclarativeBase"}
_DJANGO_FIELD_TYPES = {
    "AutoField",
    "BigAutoField",
    "BigIntegerField",
    "BinaryField",
    "BooleanField",
    "CharField",
    "DateField",
    "DateTimeField",
    "DecimalField",
    "DurationField",
    "EmailField",
    "FileField",
    "FloatField",
    "ForeignKey",
    "ImageField",
    "IntegerField",
    "JSONField",
    "ManyToManyField",
    "OneToOneField",
    "PositiveIntegerField",
    "SlugField",
    "SmallIntegerField",
    "TextField",
    "TimeField",
    "URLField",
    "UUIDField",
}


class _Analyzer:
    def __init__(self, source: bytes, path: str) -> None:
        self.path = path
        self.root = parse("python", source)
        self.result = ParsedFile(language="python", parse_errors=count_errors(self.root))
        self.prefixes: dict[str, str] = {}  # Router variable -> URL prefix.
        self.router_kinds: dict[str, str] = {}  # Router variable -> framework.

    def run(self) -> ParsedFile:
        self._collect_router_prefixes()
        self._visit_children(self.root, scope=None, enclosing_class=None)
        self.result.top_level_complexity = complexity_of(
            self.root,
            decisions=_DECISIONS,
            boolean_types=frozenset({"boolean_operator"}),
            boolean_ops=frozenset(),
            nested=_NESTED,
        )
        return self.result

    # Traversal --------------------------------------------------------------
    def _visit_children(self, node: Node, scope: str | None, enclosing_class: str | None) -> None:
        for child in node.children:
            self._visit(child, scope, enclosing_class)

    def _visit(self, node: Node, scope: str | None, enclosing_class: str | None) -> None:
        kind = node.type
        if kind == "decorated_definition":
            decorators = [c for c in node.children if c.type == "decorator"]
            definition = node.child_by_field_name("definition")
            for decorator in decorators:
                self._visit_children(decorator, scope, enclosing_class)
            if definition is not None:
                self._visit_definition(definition, scope, enclosing_class, decorators, node)
        elif kind in ("function_definition", "class_definition"):
            self._visit_definition(node, scope, enclosing_class, [], node)
        elif kind == "import_statement":
            self._import(node, scope)
        elif kind == "import_from_statement":
            self._import_from(node, scope)
        elif kind == "call":
            self._call(node, scope, enclosing_class)
            self._visit_children(node, scope, enclosing_class)
        else:
            self._visit_children(node, scope, enclosing_class)

    def _visit_definition(
        self,
        node: Node,
        scope: str | None,
        enclosing_class: str | None,
        decorators: list[Node],
        outer: Node,
    ) -> None:
        if node.type == "function_definition":
            self._function(node, scope, enclosing_class, decorators, outer)
        elif node.type == "class_definition":
            self._class(node, scope, outer)

    def _function(
        self,
        node: Node,
        scope: str | None,
        enclosing_class: str | None,
        decorators: list[Node],
        outer: Node,
    ) -> None:
        name = text(node.child_by_field_name("name"))
        if not name:
            return
        qualified = f"{scope}.{name}" if scope else name
        body = node.child_by_field_name("body")
        params = node.child_by_field_name("parameters")
        signature_end = body.start_byte if body is not None else node.end_byte
        source = self.root.text or b""
        signature = source[node.start_byte : signature_end].decode("utf-8", "replace")
        param_names = [c for c in (params.named_children if params else []) if c.type != "comment"]
        param_count = sum(1 for c in param_names if text(c) not in ("self", "cls"))
        self.result.symbols.append(
            ParsedSymbol(
                kind="method" if enclosing_class else "function",
                name=name,
                qualified_name=qualified,
                parent=enclosing_class,
                start_line=line(outer),
                end_line=end_line(outer),
                signature=one_line(signature.rstrip().rstrip(":")),
                docstring=self._docstring(body),
                complexity=complexity_of(
                    node,
                    decisions=_DECISIONS,
                    boolean_types=frozenset({"boolean_operator"}),
                    boolean_ops=frozenset(),
                    nested=_NESTED,
                ),
                param_count=param_count,
            )
        )
        for decorator in decorators:
            self._route_decorator(decorator, qualified)
        if body is not None:
            self._visit_children(body, qualified, None)

    def _class(self, node: Node, scope: str | None, outer: Node) -> None:
        name = text(node.child_by_field_name("name"))
        if not name:
            return
        qualified = f"{scope}.{name}" if scope else name
        supers = node.child_by_field_name("superclasses")
        bases = [
            text(arg) for arg in (supers.named_children if supers else []) if arg.type in ("identifier", "attribute")
        ]
        body = node.child_by_field_name("body")
        self.result.symbols.append(
            ParsedSymbol(
                kind="class",
                name=name,
                qualified_name=qualified,
                start_line=line(outer),
                end_line=end_line(outer),
                signature=one_line(f"class {name}({', '.join(bases)})" if bases else f"class {name}"),
                docstring=self._docstring(body),
                bases=bases,
            )
        )
        self._model(node, name, bases, supers, body)
        if body is not None:
            self._visit_children(body, qualified, qualified)

    @staticmethod
    def _docstring(body: Node | None) -> str | None:
        if body is None or not body.named_children:
            return None
        first = body.named_children[0]
        if first.type == "expression_statement" and first.named_children:
            first = first.named_children[0]
        if first.type == "string":
            value = string_value(first)
            return one_line(value, 500) if value else None
        return None

    # Imports and calls ------------------------------------------------------
    @staticmethod
    def _import_kind(node: Node, scope: str | None) -> str:
        """Imports under `if TYPE_CHECKING:` or inside a function cannot form a real cycle."""
        current = node.parent
        while current is not None:
            if current.type == "if_statement":
                condition = text(current.child_by_field_name("condition"))
                if "TYPE_CHECKING" in condition:
                    return "type_import"
            current = current.parent
        return "lazy_import" if scope else "import"

    def _import(self, node: Node, scope: str | None) -> None:
        kind = self._import_kind(node, scope)
        for child in node.named_children:
            if child.type == "dotted_name":
                module = text(child)
                top = module.split(".")[0]
                self.result.imports.append(ImportRef(module=module, names={top: "*"}, line=line(node), kind=kind))
            elif child.type == "aliased_import":
                module = text(child.child_by_field_name("name"))
                alias = text(child.child_by_field_name("alias"))
                self.result.imports.append(ImportRef(module=module, names={alias: "*"}, line=line(node), kind=kind))

    def _import_from(self, node: Node, scope: str | None) -> None:
        module_node = node.child_by_field_name("module_name")
        raw = text(module_node)
        level = len(raw) - len(raw.lstrip("."))
        names: dict[str, str] = {}
        for child in node.children_by_field_name("name"):
            if child.type == "aliased_import":
                names[text(child.child_by_field_name("alias"))] = text(child.child_by_field_name("name"))
            else:
                names[text(child)] = text(child)
        self.result.imports.append(
            ImportRef(
                module=raw.lstrip("."),
                names=names,
                line=line(node),
                level=level,
                kind=self._import_kind(node, scope),
            )
        )

    def _call(self, node: Node, scope: str | None, enclosing_class: str | None) -> None:
        function = node.child_by_field_name("function")
        if function is None:
            return
        if function.type == "identifier":
            name = text(function)
            self.result.calls.append(CallRef(name=name, line=line(node), scope=scope))
            if name in ("path", "re_path", "url"):
                self._django_url(node, name)
        elif function.type == "attribute":
            receiver = function.child_by_field_name("object")
            attribute = text(function.child_by_field_name("attribute"))
            if receiver is None or not attribute:
                return
            receiver_text = text(receiver)
            if attribute == "register" and PurePosixPath(self.path).name == "urls.py":
                self._drf_route(node)
            self.result.calls.append(CallRef(name=attribute, line=line(node), scope=scope, receiver=receiver_text))

    # Frameworks -------------------------------------------------------------
    def _collect_router_prefixes(self) -> None:
        """`router = APIRouter(prefix="/x")` and `bp = Blueprint(..., url_prefix="/x")`."""
        for node in walk(self.root):
            if node.type != "assignment":
                continue
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if right is None or right.type != "call":
                continue
            callee = text(right.child_by_field_name("function")).split(".")[-1]
            if callee not in ("APIRouter", "Blueprint"):
                continue
            self.router_kinds[text(left)] = "fastapi" if callee == "APIRouter" else "flask"
            args = right.child_by_field_name("arguments")
            for arg in args.named_children if args else []:
                if arg.type == "keyword_argument" and text(arg.child_by_field_name("name")) in (
                    "prefix",
                    "url_prefix",
                ):
                    value = string_value(arg.child_by_field_name("value"))
                    if value:
                        self.prefixes[text(left)] = value

    def _route_decorator(self, decorator: Node, handler: str) -> None:
        call = next((c for c in decorator.named_children if c.type == "call"), None)
        if call is None:
            return
        function = call.child_by_field_name("function")
        if function is None or function.type != "attribute":
            return
        attribute = text(function.child_by_field_name("attribute"))
        receiver = text(function.child_by_field_name("object"))
        args = call.child_by_field_name("arguments")
        positional = [a for a in (args.named_children if args else []) if a.type != "keyword_argument"]
        path = string_value(positional[0]) if positional else None
        if path is None:
            # FastAPI allows @router.get("", ...) and path=... as a keyword.
            for arg in args.named_children if args else []:
                if arg.type == "keyword_argument" and text(arg.child_by_field_name("name")) == "path":
                    path = string_value(arg.child_by_field_name("value"))
        if path is None:
            return
        full_path = _join(self.prefixes.get(receiver, ""), path)
        if attribute == "route":
            methods = ["GET"]
            for arg in args.named_children if args else []:
                if arg.type == "keyword_argument" and text(arg.child_by_field_name("name")) == "methods":
                    value = arg.child_by_field_name("value")
                    found = [string_value(v) for v in (value.named_children if value else [])]
                    methods = [m.upper() for m in found if m] or methods
            for method in methods:
                self.result.endpoints.append(EndpointRef(method, full_path, handler, "flask", line(decorator)))
        elif attribute in _HTTP_DECORATORS:
            # `cache.get("key")` has the same shape, so it needs a router or a real path.
            if receiver not in self.prefixes and not path.startswith("/"):
                return
            framework = self.router_kinds.get(receiver) or self._web_framework()
            self.result.endpoints.append(EndpointRef(attribute.upper(), full_path, handler, framework, line(decorator)))

    def _web_framework(self) -> str:
        """Flask 2 and FastAPI share `@app.get`, so the imports decide which one this is.

        A file may import its router from elsewhere and name no framework at all.
        """
        found = _WEB_IMPORT_RE.search(text(self.root))
        return found.group(1) if found else "python-web"

    def _drf_route(self, node: Node) -> None:
        """Django REST framework: `router.register(r"articles", ArticleViewSet)`."""
        args = node.child_by_field_name("arguments")
        positional = [a for a in (args.named_children if args else []) if a.type != "keyword_argument"]
        if len(positional) >= 2:
            prefix = string_value(positional[0])
            if prefix is not None:
                self.result.endpoints.append(
                    EndpointRef("ANY", "/" + prefix.lstrip("^/"), text(positional[1]), "django", line(node))
                )

    def _django_url(self, node: Node, function_name: str) -> None:
        if PurePosixPath(self.path).name != "urls.py":
            return
        args = node.child_by_field_name("arguments")
        positional = [a for a in (args.named_children if args else []) if a.type != "keyword_argument"]
        if len(positional) < 2:
            return
        route = string_value(positional[0])
        if route is None:
            return
        target = positional[1]
        handler = text(target)
        if target.type == "call":
            callee = text(target.child_by_field_name("function"))
            if callee.endswith("as_view"):
                handler = callee.rsplit(".", 1)[0]
            elif callee == "include":
                inner = target.child_by_field_name("arguments")
                included = inner.named_children[0] if inner and inner.named_children else None
                handler = f"include({string_value(included) or text(included)})"
        cleaned = re.sub(r"^\^|\$$", "", route) if function_name != "path" else route
        self.result.endpoints.append(EndpointRef("ANY", "/" + cleaned.lstrip("/"), handler, "django", line(node)))

    def _model(self, node: Node, name: str, bases: list[str], supers: Node | None, body: Node | None) -> None:
        if body is None:
            return
        statements = [
            statement.named_children[0]
            for statement in body.named_children
            if statement.type == "expression_statement"
            and statement.named_children
            and statement.named_children[0].type == "assignment"
        ]
        framework = self._model_framework(bases, supers, statements)
        if framework is None:
            return
        fields: list[dict[str, str]] = []
        relations: list[dict[str, str]] = []
        table: str | None = None
        for assignment in statements:
            field_name = text(assignment.child_by_field_name("left"))
            right = assignment.child_by_field_name("right")
            annotation = assignment.child_by_field_name("type")
            if field_name == "__tablename__":
                table = string_value(right)
                continue
            if field_name.startswith("__") or field_name == "model_config":
                continue
            if right is None or right.type != "call":
                if annotation is not None and (framework == "sqlmodel" or "Mapped[" in text(annotation)):
                    fields.append({"name": field_name, "type": text(annotation)})
                continue
            callee = text(right.child_by_field_name("function")).split(".")[-1]
            args = right.child_by_field_name("arguments")
            first = args.named_children[0] if args and args.named_children else None
            if callee in _DJANGO_FIELD_RELATIONS or callee == "relationship":
                target = string_value(first) or text(first)
                relations.append({"name": field_name, "type": callee, "target": target})
            elif callee == "Relationship":
                relations.append({"name": field_name, "type": callee, "target": text(annotation)})
            elif framework == "sqlmodel" and annotation is not None:
                fields.append({"name": field_name, "type": text(annotation)})
            elif callee.endswith("Field") or callee in ("Column", "mapped_column"):
                column_type = callee
                if callee in ("Column", "mapped_column"):
                    column_type = text(first) or (text(annotation) if annotation else callee)
                fields.append({"name": field_name, "type": column_type})
        self.result.models.append(
            ModelRef(
                name=name,
                framework=framework,
                line=line(node),
                table=table,
                fields=fields,
                relations=relations,
            )
        )

    @staticmethod
    def _model_framework(bases: list[str], supers: Node | None, statements: list[Node]) -> str | None:
        """By base class, or by field types when the base is the project's own abstract model."""
        for argument in supers.named_children if supers else []:
            if (
                argument.type == "keyword_argument"
                and text(argument.child_by_field_name("name")) == "table"
                and text(argument.child_by_field_name("value")) == "True"
            ):
                return "sqlmodel"
        if any(base == "models.Model" or base.endswith(".models.Model") for base in bases):
            return "django"
        if any(base in _SQLALCHEMY_BASES for base in bases):
            return "sqlalchemy"
        for assignment in statements:
            right = assignment.child_by_field_name("right")
            if right is None or right.type != "call":
                continue
            callee = text(right.child_by_field_name("function"))
            if callee.startswith("models.") and callee.split(".")[-1] in _DJANGO_FIELD_TYPES:
                return "django"
            if callee in ("Column", "mapped_column", "db.Column"):
                return "sqlalchemy"
        return None


def _join(prefix: str, path: str) -> str:
    joined = f"{prefix.rstrip('/')}/{path.lstrip('/')}" if prefix else path
    if not joined:
        return "/"
    return joined if joined.startswith("/") else f"/{joined}"


def analyze(source: bytes, path: str) -> ParsedFile:
    return _Analyzer(source, path).run()
