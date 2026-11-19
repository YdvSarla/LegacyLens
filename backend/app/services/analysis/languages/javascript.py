"""JavaScript and TypeScript analyzer: symbols, imports, Express/Nest routes, models."""

from __future__ import annotations

from tree_sitter import Node

from app.services.analysis.parsed import (
    CallRef,
    EndpointRef,
    ImportRef,
    ModelRef,
    MountRef,
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
)

_DECISIONS = frozenset(
    {
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "switch_case",
        "catch_clause",
        "ternary_expression",
    }
)
_FUNCTION_TYPES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "generator_function",
        "arrow_function",
        "method_definition",
    }
)
_NESTED = _FUNCTION_TYPES | {"class_declaration", "class"}
_CLASS_TYPES = frozenset({"class_declaration", "abstract_class_declaration", "class"})
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "all", "head", "options"}
_NEST_DECORATORS = {"Get", "Post", "Put", "Patch", "Delete", "All", "Head", "Options"}


def _complexity(node: Node) -> int:
    return complexity_of(
        node,
        decisions=_DECISIONS,
        boolean_types=frozenset({"binary_expression"}),
        boolean_ops=frozenset({"&&", "||", "??"}),
        nested=_NESTED,
    )


def _grammar_for(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".tsx"):
        return "tsx"
    if lower.endswith((".ts", ".mts", ".cts")):
        return "typescript"
    return "javascript"


class _Analyzer:
    def __init__(self, source: bytes, path: str) -> None:
        self.source = source
        self.path = path
        grammar = _grammar_for(path)
        self.root = parse(grammar, source)
        language = "typescript" if grammar in ("typescript", "tsx") else "javascript"
        self.result = ParsedFile(language=language, parse_errors=count_errors(self.root))
        self.model_schema_vars: dict[int, str] = {}  # id(model) -> schema variable name.

    def run(self) -> ParsedFile:
        self._visit_children(self.root, scope=None, enclosing_class=None)
        self.result.top_level_complexity = _complexity(self.root)
        self._merge_mongoose_schemas()
        return self.result

    def _merge_mongoose_schemas(self) -> None:
        """`mongoose.model('User', userSchema)` takes the fields of `userSchema`."""
        schemas = {m.name: m for m in self.result.models if m.framework == "mongoose" and m.fields}
        for model in list(self.result.models):
            variable = self.model_schema_vars.get(id(model))
            schema = schemas.get(variable) if variable else None
            if schema is not None and schema is not model:
                model.fields = schema.fields
                if schema in self.result.models:
                    self.result.models.remove(schema)

    def _visit_children(self, node: Node, scope: str | None, enclosing_class: str | None) -> None:
        for child in node.children:
            self._visit(child, scope, enclosing_class)

    def _visit(self, node: Node, scope: str | None, enclosing_class: str | None) -> None:
        kind = node.type
        if kind in ("function_declaration", "generator_function_declaration"):
            self._function(node, text(node.child_by_field_name("name")), scope, enclosing_class, node)
        elif kind in _CLASS_TYPES:
            self._class(node, scope)
        elif kind in ("interface_declaration", "enum_declaration"):
            self._simple_type(node, scope)
        elif kind == "variable_declarator":
            self._declarator(node, scope, enclosing_class)
        elif kind == "assignment_expression":
            self._assignment(node, scope, enclosing_class)
        elif kind == "pair":
            self._pair(node, scope, enclosing_class)
        elif kind == "method_definition":
            # Object literal method: `module.exports = { login() {} }`.
            self._function(node, text(node.child_by_field_name("name")), scope, enclosing_class, node)
        elif kind == "import_statement":
            self._import(node)
        elif kind == "export_statement" and node.child_by_field_name("source") is not None:
            self._reexport(node)
            self._visit_children(node, scope, enclosing_class)
        elif kind == "call_expression":
            self._call(node, scope)
            self._visit_children(node, scope, enclosing_class)
        elif kind == "new_expression":
            constructor = node.child_by_field_name("constructor")
            name = text(constructor)
            if name:
                self.result.calls.append(CallRef(name=name.split(".")[-1], line=line(node), scope=scope, is_new=True))
            self._schema_or_model_from_new(node, name)
            self._visit_children(node, scope, enclosing_class)
        else:
            self._visit_children(node, scope, enclosing_class)

    # Symbols ----------------------------------------------------------------
    def _function(
        self,
        node: Node,
        name: str,
        scope: str | None,
        enclosing_class: str | None,
        outer: Node,
        *,
        kind: str | None = None,
    ) -> str:
        qualified = f"{scope}.{name}" if scope else name
        body = node.child_by_field_name("body")
        params = node.child_by_field_name("parameters") or node.child_by_field_name("parameter")
        end = body.start_byte if body is not None else node.end_byte
        signature = self.source[outer.start_byte : end].decode("utf-8", "replace")
        param_count = 0
        if params is not None:
            param_count = (
                1 if params.type == "identifier" else sum(1 for c in params.named_children if c.type != "comment")
            )
        self.result.symbols.append(
            ParsedSymbol(
                kind=kind or ("method" if enclosing_class else "function"),
                name=name,
                qualified_name=qualified,
                parent=enclosing_class,
                start_line=line(outer),
                end_line=end_line(outer),
                signature=one_line(signature.rstrip().rstrip("=>").rstrip()),
                docstring=_doc_comment(outer),
                complexity=_complexity(node),
                param_count=param_count,
            )
        )
        if body is not None:
            self._visit_children(body, qualified, None)
        return qualified

    def _class(self, node: Node, scope: str | None, name_override: str | None = None) -> None:
        name = name_override or text(node.child_by_field_name("name"))
        if not name:
            return
        qualified = f"{scope}.{name}" if scope else name
        bases: list[str] = []
        implements: list[str] = []
        for child in node.children:
            if child.type != "class_heritage":
                continue
            for part in child.named_children:
                if part.type == "extends_clause":
                    value = part.child_by_field_name("value")
                    if value is not None:
                        bases.append(text(value))
                elif part.type == "implements_clause":
                    implements.extend(text(t) for t in part.named_children)
                else:
                    bases.append(text(part))  # Plain JavaScript: `class A extends B`.
        decorators = _decorators(node)
        self.result.symbols.append(
            ParsedSymbol(
                kind="class",
                name=name,
                qualified_name=qualified,
                start_line=line(node),
                end_line=end_line(node),
                signature=one_line(f"class {name}" + (f" extends {bases[0]}" if bases else "")),
                docstring=_doc_comment(node),
                bases=bases,
                implements=implements,
            )
        )
        body = node.child_by_field_name("body")
        self._class_models(node, name, bases, decorators, body)
        controller_prefix = _decorator_string(decorators, "Controller")
        is_controller = any(_decorator_name(d) == "Controller" for d in decorators)
        if body is None:
            return
        pending: list[Node] = []
        for member in body.children:
            if member.type == "decorator":
                pending.append(member)
            elif member.type == "method_definition":
                member_decorators = pending + [c for c in member.children if c.type == "decorator"]
                pending = []
                method_name = text(member.child_by_field_name("name"))
                symbol = self._function(member, method_name, qualified, qualified, member)
                if is_controller:
                    self._nest_routes(member_decorators, controller_prefix or "", symbol, member)
            else:
                pending = []
                self._visit(member, qualified, qualified)

    def _simple_type(self, node: Node, scope: str | None) -> None:
        name = text(node.child_by_field_name("name"))
        if not name:
            return
        self.result.symbols.append(
            ParsedSymbol(
                kind="interface" if node.type == "interface_declaration" else "enum",
                name=name,
                qualified_name=f"{scope}.{name}" if scope else name,
                start_line=line(node),
                end_line=end_line(node),
                signature=one_line(text(node).split("{")[0]),
                docstring=_doc_comment(node),
            )
        )

    def _declarator(self, node: Node, scope: str | None, enclosing_class: str | None) -> None:
        name_node = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        name = text(name_node)
        if value is None:
            return
        if name_node is not None and name_node.type == "identifier":
            if value.type in _FUNCTION_TYPES:
                declaration = node.parent if node.parent is not None else node
                outer = declaration if len(declaration.named_children) == 1 else node
                self._function(value, name, scope, enclosing_class, outer)
                return
            if value.type in _CLASS_TYPES:
                self._class(value, scope, name_override=name)
                return
        self._require_binding(node, name_node, value, scope)
        self._visit_children(node, scope, enclosing_class)

    def _assignment(self, node: Node, scope: str | None, enclosing_class: str | None) -> None:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None:
            return
        if right.type in _FUNCTION_TYPES and left.type == "member_expression":
            target = text(left)
            parts = target.split(".")
            name = parts[-1]
            if name != "exports" or len(parts) > 2:
                parent = None
                # `Foo.prototype.bar = function () {}` is a method of Foo.
                if len(parts) >= 3 and parts[-2] == "prototype":
                    parent = parts[-3]
                qualified_scope = parent or scope
                self._function(right, name, qualified_scope, parent, node)
                return
        self._visit_children(node, scope, enclosing_class)

    def _pair(self, node: Node, scope: str | None, enclosing_class: str | None) -> None:
        key = node.child_by_field_name("key")
        value = node.child_by_field_name("value")
        if value is not None and value.type in _FUNCTION_TYPES and key is not None:
            name = string_value(key) or text(key)
            self._function(value, name, scope, enclosing_class, node)
            return
        self._visit_children(node, scope, enclosing_class)

    # Imports ----------------------------------------------------------------
    def _import(self, node: Node) -> None:
        source = string_value(node.child_by_field_name("source"))
        if source is None:
            return
        names: dict[str, str] = {}
        for clause in node.named_children:
            if clause.type != "import_clause":
                continue
            for part in clause.named_children:
                if part.type == "identifier":
                    names[text(part)] = "default"
                elif part.type == "namespace_import":
                    alias = next((c for c in part.named_children if c.type == "identifier"), None)
                    if alias is not None:
                        names[text(alias)] = "*"
                elif part.type == "named_imports":
                    for spec in part.named_children:
                        if spec.type != "import_specifier":
                            continue
                        imported = text(spec.child_by_field_name("name"))
                        alias = spec.child_by_field_name("alias")
                        names[text(alias) if alias is not None else imported] = imported
        is_type_only = text(node).startswith("import type")
        self.result.imports.append(
            ImportRef(module=source, names=names, line=line(node), kind="type_import" if is_type_only else "import")
        )

    def _reexport(self, node: Node) -> None:
        source = string_value(node.child_by_field_name("source"))
        if source is not None:
            self.result.imports.append(ImportRef(module=source, names={}, line=line(node)))

    def _require_binding(self, node: Node, name_node: Node | None, value: Node, scope: str | None) -> None:
        """`const x = require('./x')` and `const { a, b } = require('./x')`."""
        if value.type != "call_expression":
            return
        function = value.child_by_field_name("function")
        if text(function) != "require":
            return
        args = value.child_by_field_name("arguments")
        first = args.named_children[0] if args and args.named_children else None
        source = string_value(first)
        if source is None or name_node is None:
            return
        names: dict[str, str] = {}
        if name_node.type == "identifier":
            names[text(name_node)] = "*"
        elif name_node.type == "object_pattern":
            for part in name_node.named_children:
                if part.type == "shorthand_property_identifier_pattern":
                    names[text(part)] = text(part)
                elif part.type == "pair_pattern":
                    names[text(part.child_by_field_name("value"))] = text(part.child_by_field_name("key"))
        self.result.imports.append(
            ImportRef(module=source, names=names, line=line(node), kind="lazy_import" if scope else "import")
        )

    # Calls and frameworks ---------------------------------------------------
    def _call(self, node: Node, scope: str | None) -> None:
        function = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if function is None:
            return
        if function.type == "identifier":
            name = text(function)
            if name == "require" and args is not None and args.named_children:
                source = string_value(args.named_children[0])
                parent = node.parent
                bound = parent is not None and parent.type == "variable_declarator"
                if source is not None and not bound:
                    self.result.imports.append(
                        ImportRef(module=source, line=line(node), kind="lazy_import" if scope else "import")
                    )
                return
            self.result.calls.append(CallRef(name=name, line=line(node), scope=scope))
            if name == "model":
                self._mongoose_model(node, args)
        elif function.type == "import":
            first = args.named_children[0] if args and args.named_children else None
            source = string_value(first)
            if source is not None:  # Dynamic import() loads on demand.
                self.result.imports.append(ImportRef(module=source, line=line(node), kind="lazy_import"))
        elif function.type == "member_expression":
            receiver = text(function.child_by_field_name("object"))
            method = text(function.child_by_field_name("property"))
            if not method:
                return
            self.result.calls.append(CallRef(name=method, line=line(node), scope=scope, receiver=receiver))
            if method in _HTTP_METHODS:
                chained = _route_chain_path(function.child_by_field_name("object"))
                if chained is not None:
                    self._express_chained_route(node, chained, method, args)
                else:
                    self._express_route(node, receiver, method, args)
            elif method == "setGlobalPrefix" and args is not None and args.named_children:
                self.result.global_prefix = string_value(args.named_children[0])
            elif method == "Schema":
                self._schema_or_model_from_new(node, "Schema")
            elif method == "use":
                self._express_mount(node, args)
            elif method == "model":
                self._mongoose_model(node, args)
            elif method == "define":
                self._sequelize_define(node, args)

    def _express_route(self, node: Node, receiver: str, method: str, args: Node | None) -> None:
        arguments = [c for c in (args.named_children if args else []) if c.type != "comment"]
        if len(arguments) < 2:
            return
        path = string_value(arguments[0])
        # `cache.get('key', fallback)` also has this shape, so a route must look like a path.
        if path is None or not path.startswith(("/", "*")):
            return
        handler_node = arguments[-1]
        handler = "<anonymous>" if handler_node.type in _FUNCTION_TYPES else text(handler_node)
        middleware = [one_line(text(a), 80) for a in arguments[1:-1]]
        self.result.endpoints.append(
            EndpointRef(
                method=method.upper() if method != "all" else "ANY",
                path=path,
                handler=one_line(handler, 200),
                framework="express",
                line=line(node),
                middleware=middleware,
                router_var=receiver.split(".")[-1],
            )
        )

    def _express_chained_route(self, node: Node, chain: tuple[str, str], method: str, args: Node | None) -> None:
        """`router.route("/users").get(list).post(auth, create)`: the path is on the chain."""
        path, receiver = chain
        arguments = [c for c in (args.named_children if args else []) if c.type != "comment"]
        if not arguments:
            return
        handler_node = arguments[-1]
        handler = "<anonymous>" if handler_node.type in _FUNCTION_TYPES else text(handler_node)
        self.result.endpoints.append(
            EndpointRef(
                method=method.upper() if method != "all" else "ANY",
                path=path,
                handler=one_line(handler, 200),
                framework="express",
                line=line(node),
                middleware=[one_line(text(a), 80) for a in arguments[:-1]],
                router_var=receiver.split(".")[-1],
            )
        )

    def _express_mount(self, node: Node, args: Node | None) -> None:
        arguments = [c for c in (args.named_children if args else []) if c.type != "comment"]
        if len(arguments) < 2:
            return
        prefix = string_value(arguments[0])
        target = arguments[-1]
        if prefix is None:
            return
        if target.type == "identifier":
            self.result.mounts.append(MountRef(prefix, text(target), line(node)))
        elif target.type == "call_expression" and text(target.child_by_field_name("function")) == "require":
            inner = target.child_by_field_name("arguments")
            first = inner.named_children[0] if inner and inner.named_children else None
            source = string_value(first)
            if source is not None:
                self.result.mounts.append(MountRef(prefix, f"require:{source}", line(node)))

    def _nest_routes(self, decorators: list[Node], prefix: str, handler: str, member: Node) -> None:
        for decorator in decorators:
            name = _decorator_name(decorator)
            if name in _NEST_DECORATORS:
                sub = _decorator_first_string(decorator) or ""
                path = "/" + "/".join(p.strip("/") for p in (prefix, sub) if p.strip("/"))
                self.result.endpoints.append(
                    EndpointRef(
                        method="ANY" if name == "All" else name.upper(),
                        path=path,
                        handler=handler,
                        framework="nestjs",
                        line=line(member),
                    )
                )

    def _mongoose_model(self, node: Node, args: Node | None) -> None:
        arguments = args.named_children if args else []
        name = string_value(arguments[0]) if arguments else None
        if name is None or len(arguments) < 2:
            return
        model = ModelRef(name=name, framework="mongoose", line=line(node))
        self.result.models.append(model)
        if arguments[1].type == "identifier":
            self.model_schema_vars[id(model)] = text(arguments[1])

    def _sequelize_define(self, node: Node, args: Node | None) -> None:
        arguments = args.named_children if args else []
        name = string_value(arguments[0]) if arguments else None
        if name is None or len(arguments) < 2 or arguments[1].type != "object":
            return
        self.result.models.append(
            ModelRef(
                name=name,
                framework="sequelize",
                line=line(node),
                fields=_object_fields(arguments[1]),
            )
        )

    def _schema_or_model_from_new(self, node: Node, constructor: str) -> None:
        if constructor.split(".")[-1] != "Schema":
            return
        args = node.child_by_field_name("arguments")
        first = args.named_children[0] if args and args.named_children else None
        if first is None or first.type != "object":
            return
        parent = node.parent
        variable = (
            text(parent.child_by_field_name("name"))
            if parent is not None and parent.type == "variable_declarator"
            else ""
        )
        self.result.models.append(
            ModelRef(
                name=variable or "schema",
                framework="mongoose",
                line=line(node),
                fields=_object_fields(first),
            )
        )

    def _class_models(self, node: Node, name: str, bases: list[str], decorators: list[Node], body: Node | None) -> None:
        is_entity = any(_decorator_name(d) == "Entity" for d in decorators)
        extends_model = any(b.split(".")[-1] == "Model" for b in bases)
        if not is_entity and not extends_model:
            return
        fields: list[dict[str, str]] = []
        relations: list[dict[str, str]] = []
        for member in body.named_children if body else []:
            if member.type not in ("public_field_definition", "field_definition"):
                continue
            field_name = text(member.child_by_field_name("name") or member.child_by_field_name("property"))
            annotation = member.child_by_field_name("type")
            member_decorators = [_decorator_name(c) for c in member.children if c.type == "decorator"]
            relation = next(
                (d for d in member_decorators if d in ("OneToOne", "OneToMany", "ManyToOne", "ManyToMany")),
                None,
            )
            type_text = text(annotation).lstrip(": ").strip()
            if relation:
                relations.append({"name": field_name, "type": relation, "target": type_text})
            elif field_name:
                fields.append({"name": field_name, "type": type_text or "unknown"})
        self.result.models.append(
            ModelRef(
                name=name,
                framework="typeorm" if is_entity else "sequelize",
                line=line(node),
                table=_decorator_string(decorators, "Entity"),
                fields=fields,
                relations=relations,
            )
        )


def _route_chain_path(node: Node | None) -> tuple[str, str] | None:
    """Finds ("/path", "router") in `router.route("/path").get(...)`, through any chain length."""
    current = node
    while current is not None and current.type == "call_expression":
        function = current.child_by_field_name("function")
        if function is None or function.type != "member_expression":
            return None
        property_name = text(function.child_by_field_name("property"))
        receiver = function.child_by_field_name("object")
        if property_name == "route":
            args = current.child_by_field_name("arguments")
            first = args.named_children[0] if args and args.named_children else None
            path = string_value(first)
            return (path, text(receiver)) if path is not None else None
        if property_name not in _HTTP_METHODS:
            return None
        current = receiver
    return None


def _object_fields(node: Node) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    for pair in node.named_children:
        if pair.type != "pair":
            continue
        key = pair.child_by_field_name("key")
        value = pair.child_by_field_name("value")
        field_type = one_line(text(value), 60)
        if value is not None and value.type == "object":
            type_pair = next(
                (p for p in value.named_children if p.type == "pair" and text(p.child_by_field_name("key")) == "type"),
                None,
            )
            if type_pair is not None:
                field_type = one_line(text(type_pair.child_by_field_name("value")), 60)
        fields.append({"name": string_value(key) or text(key), "type": field_type})
    return fields


def _decorators(node: Node) -> list[Node]:
    found = [c for c in node.children if c.type == "decorator"]
    parent = node.parent
    if parent is not None and parent.type == "export_statement":
        found += [c for c in parent.children if c.type == "decorator"]
    return found


def _decorator_name(decorator: Node) -> str:
    expression = decorator.named_children[0] if decorator.named_children else None
    if expression is None:
        return ""
    if expression.type == "call_expression":
        return text(expression.child_by_field_name("function")).split(".")[-1]
    return text(expression).split(".")[-1]


def _decorator_first_string(decorator: Node) -> str | None:
    expression = decorator.named_children[0] if decorator.named_children else None
    if expression is None or expression.type != "call_expression":
        return None
    args = expression.child_by_field_name("arguments")
    first = args.named_children[0] if args and args.named_children else None
    return string_value(first)


def _decorator_string(decorators: list[Node], name: str) -> str | None:
    for decorator in decorators:
        if _decorator_name(decorator) == name:
            return _decorator_first_string(decorator)
    return None


def _doc_comment(node: Node) -> str | None:
    # The comment is a sibling of the whole statement, not of the expression inside it.
    anchor = node
    while anchor.parent is not None and anchor.parent.type in (
        "export_statement",
        "expression_statement",
        "lexical_declaration",
        "variable_declaration",
    ):
        anchor = anchor.parent
    previous = anchor.prev_named_sibling
    if previous is not None and previous.type == "comment":
        value = text(previous)
        if value.startswith("/**") and previous.end_point[0] + 1 >= anchor.start_point[0]:
            cleaned = [line_.strip(" */\t") for line_ in value.splitlines()]
            return one_line(" ".join(part for part in cleaned if part), 500) or None
    return None


def analyze(source: bytes, path: str) -> ParsedFile:
    return _Analyzer(source, path).run()
