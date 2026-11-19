"""PHP analyzer with support for plain PHP, Laravel and Drupal (7 and 8+)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath

from tree_sitter import Node

from app.services.analysis.parsed import (
    CallRef,
    EndpointRef,
    HookRef,
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
        "else_if_clause",
        "for_statement",
        "foreach_statement",
        "while_statement",
        "do_statement",
        "case_statement",
        "catch_clause",
        "conditional_expression",
        "match_conditional_expression",
    }
)
_NESTED = frozenset(
    {
        "function_definition",
        "method_declaration",
        "anonymous_function",
        "arrow_function",
        "class_declaration",
    }
)
_BOOLEAN_OPS = frozenset({"&&", "||", "and", "or", "xor", "??"})
_CLASS_LIKE = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "trait_declaration": "trait",
    "enum_declaration": "enum",
}
_ELOQUENT_BASES = {"Model", "Authenticatable", "Pivot", "MorphPivot", "User"}
_ELOQUENT_RELATIONS = {
    "hasOne",
    "hasMany",
    "belongsTo",
    "belongsToMany",
    "hasManyThrough",
    "hasOneThrough",
    "morphTo",
    "morphOne",
    "morphMany",
    "morphToMany",
    "morphedByMany",
}
_ROUTE_VERBS = {"get", "post", "put", "patch", "delete", "options"}
_ROUTE_MODIFIERS = {"prefix", "middleware", "controller", "only", "except", "name", "namespace"}
_RESOURCE_ACTIONS = {
    "index": ("GET", ""),
    "create": ("GET", "/create"),
    "store": ("POST", ""),
    "show": ("GET", "/{id}"),
    "edit": ("GET", "/{id}/edit"),
    "update": ("PUT", "/{id}"),
    "destroy": ("DELETE", "/{id}"),
}
_API_RESOURCE_ACTIONS = ("index", "store", "show", "update", "destroy")
_MIGRATION_SKIP_COLUMNS = {
    "index",
    "unique",
    "primary",
    "foreign",
    "dropColumn",
    "dropIndex",
    "dropForeign",
    "renameColumn",
    "engine",
    "charset",
    "collation",
}
_DRUPAL_FILE_EXTENSIONS = {".module", ".install", ".theme", ".inc", ".profile", ".engine"}
_DRUPAL_HOOK_RE = re.compile(
    r"^(menu(_\w+)?|theme(_\w+)?|schema|install|uninstall|enable|disable|requirements|"
    r"update_\d+|update_N|cron|init|boot|help|permission|perm|form_alter|form_\w+_alter|"
    r"\w+_alter|block_\w+|node_\w+|entity_\w+|user_\w+|preprocess(_\w+)?|process(_\w+)?|"
    r"page_\w+|mail|token_\w+|tokens|views_\w+|field_\w+|library_info_\w+|cache_flush|"
    r"rebuild|modules_\w+|query_\w+|taxonomy_\w+|comment_\w+|file_\w+)$"
)
# The annotation sits in a doc block, so lines may begin with a `*`.
_DRUPAL_ENTITY_RE = re.compile(r"@(ContentEntityType|ConfigEntityType)\s*\([\s*]*id\s*=\s*\"([^\"]+)\"")
_DRUPAL_ENTITY_ATTR_RE = re.compile(r"#\[(ContentEntityType|ConfigEntityType)\(\s*id:\s*['\"]([^'\"]+)['\"]")
_SUPERGLOBALS = {"_GET", "_POST", "_REQUEST", "_FILES"}


@dataclass
class _RouteContext:
    prefix: str = ""
    middleware: list[str] = field(default_factory=list)
    controller: str | None = None
    namespace: str | None = None


def _argument_values(arguments: Node | None) -> list[Node]:
    values: list[Node] = []
    for argument in arguments.named_children if arguments is not None else []:
        if argument.type == "argument":
            inner = argument.named_children[-1] if argument.named_children else None
            if inner is not None:
                values.append(inner)
    return values


def _array_strings(node: Node | None) -> list[str]:
    if node is None:
        return []
    if node.type == "string" or node.type == "encapsed_string":
        value = string_value(node)
        return [value] if value is not None else []
    found: list[str] = []
    if node.type == "array_creation_expression":
        for element in node.named_children:
            if element.type == "array_element_initializer" and element.named_children:
                value = string_value(element.named_children[-1])
                if value is not None:
                    found.append(value)
    return found


def _array_pairs(node: Node | None) -> dict[str, Node]:
    pairs: dict[str, Node] = {}
    if node is None or node.type != "array_creation_expression":
        return pairs
    for element in node.named_children:
        if element.type == "array_element_initializer" and len(element.named_children) == 2:
            key = string_value(element.named_children[0])
            if key is not None:
                pairs[key] = element.named_children[1]
    return pairs


def _dynamic_path(node: Node) -> str | None:
    """A path built at runtime, such as `'list/' . $key . '/add'`, shown as `list/{key}/add`."""
    if node.type == "binary_expression" and text(node.child_by_field_name("operator")) == ".":
        parts = [_dynamic_path(side) for side in (node.child_by_field_name("left"), node.child_by_field_name("right"))]
        return None if None in parts else "".join(part for part in parts if part)
    if node.type == "variable_name":
        return "{" + text(node).lstrip("$") + "}"
    value = string_value(node)
    return re.sub(r"\$(\w+)", r"{\1}", value) if value is not None else None


def _join_url(*parts: str) -> str:
    cleaned = [p.strip("/") for p in parts if p and p.strip("/")]
    return "/" + "/".join(cleaned)


class _Analyzer:
    def __init__(self, source: bytes, path: str) -> None:
        self.source = source
        self.path = path
        self.pure = PurePosixPath(path)
        self.root = parse("php", source)
        self.result = ParsedFile(language="php", parse_errors=count_errors(self.root))
        self.namespace: str | None = None
        self.use_map: dict[str, str] = {}
        self.module_name = self.pure.stem if self.pure.suffix in _DRUPAL_FILE_EXTENSIONS else None

    def run(self) -> ParsedFile:
        self._visit_children(self.root, _Scope())
        self.result.namespace = self.namespace
        self.result.top_level_complexity = complexity_of(
            self.root,
            decisions=_DECISIONS,
            boolean_types=frozenset({"binary_expression"}),
            boolean_ops=_BOOLEAN_OPS,
            nested=_NESTED,
        )
        return self.result

    # Name resolution --------------------------------------------------------
    def _resolve_class(self, name: str) -> str:
        name = name.strip()
        if name.startswith("\\"):
            return name.lstrip("\\")
        head, _, tail = name.partition("\\")
        if head in self.use_map:
            return f"{self.use_map[head]}\\{tail}" if tail else self.use_map[head]
        return f"{self.namespace}\\{name}" if self.namespace else name

    # Traversal --------------------------------------------------------------
    def _visit_children(self, node: Node, scope: _Scope) -> None:
        for child in node.children:
            self._visit(child, scope)

    def _visit(self, node: Node, scope: _Scope) -> None:
        kind = node.type
        if kind == "namespace_definition":
            self.namespace = text(node.child_by_field_name("name")) or None
            self._visit_children(node, scope)
        elif kind == "namespace_use_declaration":
            self._use_declaration(node)
        elif kind in _CLASS_LIKE:
            self._class_like(node, scope)
        elif kind == "function_definition":
            self._function(node, scope)
        elif kind == "expression_statement":
            expression = node.named_children[0] if node.named_children else None
            if expression is not None and self._laravel_route_statement(expression, self._root_route_context()):
                return
            self._visit_children(node, scope)
        elif kind in (
            "include_expression",
            "include_once_expression",
            "require_expression",
            "require_once_expression",
        ):
            self._include(node)
        elif kind == "function_call_expression":
            self._function_call(node, scope)
            self._visit_children(node, scope)
        elif kind == "scoped_call_expression":
            self._scoped_call(node, scope)
            self._visit_children(node, scope)
        elif kind in ("member_call_expression", "nullsafe_member_call_expression"):
            self._member_call(node, scope)
            self._visit_children(node, scope)
        elif kind == "object_creation_expression":
            self._new(node, scope)
            self._visit_children(node, scope)
        elif kind == "variable_name":
            if scope.symbol is None and text(node).lstrip("$") in _SUPERGLOBALS:
                self.result.request_inputs.add(text(node).lstrip("$"))
        else:
            self._visit_children(node, scope)

    def _use_declaration(self, node: Node) -> None:
        prefix = ""
        for child in node.named_children:
            if child.type == "namespace_name":
                prefix = text(child)
            elif child.type == "namespace_use_clause":
                self._use_clause(child, "")
            elif child.type == "namespace_use_group":
                for clause in child.named_children:
                    if clause.type == "namespace_use_clause":
                        self._use_clause(clause, prefix)

    def _use_clause(self, clause: Node, prefix: str) -> None:
        names = [c for c in clause.named_children if c.type in ("qualified_name", "name")]
        if not names:
            return
        target = text(names[0]).lstrip("\\")
        fqn = f"{prefix}\\{target}" if prefix else target
        alias = text(names[1]) if len(names) > 1 else fqn.rsplit("\\", 1)[-1]
        self.use_map[alias] = fqn
        self.result.imports.append(ImportRef(module=fqn, names={alias: fqn}, line=line(clause), kind="use"))

    # Symbols ----------------------------------------------------------------
    def _doc(self, node: Node) -> tuple[str | None, str]:
        """Returns the cleaned doc comment and its raw text."""
        previous = node.prev_named_sibling
        if previous is not None and previous.type == "comment":
            raw = text(previous)
            if raw.startswith("/**"):
                parts = [part.strip(" */\t") for part in raw.splitlines()]
                return one_line(" ".join(p for p in parts if p), 500) or None, raw
        return None, ""

    def _signature(self, node: Node, body: Node | None) -> str:
        end = body.start_byte if body is not None else node.end_byte
        return one_line(self.source[node.start_byte : end].decode("utf-8", "replace"))

    def _class_like(self, node: Node, scope: _Scope) -> None:
        short = text(node.child_by_field_name("name"))
        if not short:
            return
        fqn = f"{self.namespace}\\{short}" if self.namespace else short
        bases: list[str] = []
        implements: list[str] = []
        traits: list[str] = []
        for child in node.children:
            if child.type == "base_clause":
                bases.extend(
                    self._resolve_class(text(n)) for n in child.named_children if n.type in ("name", "qualified_name")
                )
            elif child.type == "class_interface_clause":
                implements.extend(
                    self._resolve_class(text(n)) for n in child.named_children if n.type in ("name", "qualified_name")
                )
        body = node.child_by_field_name("body")
        doc, raw_doc = self._doc(node)
        property_types: dict[str, str] = {}
        for member in body.named_children if body is not None else []:
            if member.type == "use_declaration":
                traits.extend(
                    self._resolve_class(text(n)) for n in member.named_children if n.type in ("name", "qualified_name")
                )
            elif member.type == "property_declaration":
                self._property_types(member, property_types)
            elif member.type == "method_declaration" and text(member.child_by_field_name("name")) == "__construct":
                self._constructor_property_types(member, property_types)
        self.result.symbols.append(
            ParsedSymbol(
                kind=_CLASS_LIKE[node.type],
                name=short,
                qualified_name=fqn,
                start_line=line(node),
                end_line=end_line(node),
                signature=self._signature(node, body),
                docstring=doc,
                bases=bases,
                implements=implements,
                traits=traits,
            )
        )
        self._class_models(node, fqn, short, bases, body, raw_doc)
        class_scope = _Scope(symbol=fqn, class_name=fqn, property_types=property_types)
        for member in body.named_children if body is not None else []:
            if member.type == "method_declaration":
                self._method(member, fqn, class_scope)
            elif member.type != "use_declaration":
                self._visit(member, class_scope)

    def _property_types(self, node: Node, into: dict[str, str]) -> None:
        declared = next((c for c in node.named_children if c.type in ("named_type", "optional_type")), None)
        if declared is None:
            return
        for element in node.named_children:
            if element.type == "property_element" and element.named_children:
                name = text(element.named_children[0]).lstrip("$")
                into[name] = self._resolve_class(text(declared).lstrip("?"))

    def _constructor_property_types(self, node: Node, into: dict[str, str]) -> None:
        params = self._param_types(node)
        parameters = node.child_by_field_name("parameters")
        for parameter in parameters.named_children if parameters is not None else []:
            if parameter.type == "property_promotion_parameter":
                name = text(parameter.child_by_field_name("name")).lstrip("$")
                if name in params:
                    into[name] = params[name]
        body = node.child_by_field_name("body")
        if body is None:
            return
        # `$this->svc = $svc;` where $svc has a type hint.
        for current in walk(body):
            if current.type != "assignment_expression":
                continue
            left, right = current.child_by_field_name("left"), current.child_by_field_name("right")
            if left is None or right is None or left.type != "member_access_expression":
                continue
            if text(left.child_by_field_name("object")) != "$this":
                continue
            value = text(right).lstrip("$")
            if value in params:
                into[text(left.child_by_field_name("name"))] = params[value]

    def _param_types(self, node: Node) -> dict[str, str]:
        types: dict[str, str] = {}
        parameters = node.child_by_field_name("parameters")
        for parameter in parameters.named_children if parameters is not None else []:
            if parameter.type not in ("simple_parameter", "property_promotion_parameter"):
                continue
            declared = parameter.child_by_field_name("type")
            name = text(parameter.child_by_field_name("name")).lstrip("$")
            if declared is not None and declared.type in ("named_type", "optional_type") and name:
                types[name] = self._resolve_class(text(declared).lstrip("?"))
        return types

    def _complexity(self, node: Node) -> int:
        return complexity_of(
            node.child_by_field_name("body") or node,
            decisions=_DECISIONS,
            boolean_types=frozenset({"binary_expression"}),
            boolean_ops=_BOOLEAN_OPS,
            nested=_NESTED,
        )

    @staticmethod
    def _param_count(node: Node) -> int:
        parameters = node.child_by_field_name("parameters")
        return sum(1 for c in parameters.named_children if c.type != "comment") if parameters is not None else 0

    def _method(self, node: Node, class_fqn: str, class_scope: _Scope) -> None:
        name = text(node.child_by_field_name("name"))
        if not name:
            return
        qualified = f"{class_fqn}::{name}"
        body = node.child_by_field_name("body")
        doc, _ = self._doc(node)
        self.result.symbols.append(
            ParsedSymbol(
                kind="method",
                name=name,
                qualified_name=qualified,
                parent=class_fqn,
                start_line=line(node),
                end_line=end_line(node),
                signature=self._signature(node, body),
                docstring=doc,
                complexity=self._complexity(node),
                param_count=self._param_count(node),
            )
        )
        if body is not None:
            self._visit_children(
                body,
                replace(class_scope, symbol=qualified, param_types=self._param_types(node)),
            )

    def _function(self, node: Node, scope: _Scope) -> None:
        name = text(node.child_by_field_name("name"))
        if not name:
            return
        qualified = f"{self.namespace}\\{name}" if self.namespace else name
        body = node.child_by_field_name("body")
        doc, _ = self._doc(node)
        self.result.symbols.append(
            ParsedSymbol(
                kind="function",
                name=name,
                qualified_name=qualified,
                start_line=line(node),
                end_line=end_line(node),
                signature=self._signature(node, body),
                docstring=doc,
                complexity=self._complexity(node),
                param_count=self._param_count(node),
            )
        )
        self._drupal_function(node, name, body)
        if body is not None:
            self._visit_children(body, _Scope(symbol=qualified, param_types=self._param_types(node)))

    # Includes and calls -----------------------------------------------------
    def _include(self, node: Node) -> None:
        strings = [string_value(c) for c in walk(node) if c.type in ("string", "encapsed_string")]
        target = next((s for s in reversed(strings) if s), None)
        if target:
            self.result.imports.append(ImportRef(module=target, line=line(node), kind="include"))

    def _function_call(self, node: Node, scope: _Scope) -> None:
        function = node.child_by_field_name("function")
        if function is None or function.type not in ("name", "qualified_name"):
            return
        name = text(function).rsplit("\\", 1)[-1]
        self.result.calls.append(CallRef(name=name, line=line(node), scope=scope.symbol))
        if name == "module_load_include":
            args = _argument_values(node.child_by_field_name("arguments"))
            if len(args) >= 3:
                extension, include = string_value(args[0]), string_value(args[2])
                if extension and include:
                    self.result.imports.append(
                        ImportRef(module=f"{include}.{extension}", line=line(node), kind="include")
                    )

    def _scoped_call(self, node: Node, scope: _Scope) -> None:
        scope_node = node.child_by_field_name("scope")
        name = text(node.child_by_field_name("name"))
        if scope_node is None or not name:
            return
        target = text(scope_node)
        if target in ("self", "static") and scope.class_name:
            self.result.calls.append(
                CallRef(name=name, line=line(node), scope=scope.symbol, receiver_type=scope.class_name)
            )
        elif target == "parent":
            self.result.calls.append(CallRef(name=name, line=line(node), scope=scope.symbol, receiver="parent"))
        elif scope_node.type in ("name", "qualified_name"):
            self.result.calls.append(
                CallRef(
                    name=name,
                    line=line(node),
                    scope=scope.symbol,
                    receiver=target,
                    receiver_type=self._resolve_class(target),
                )
            )
            if target.rsplit("\\", 1)[-1] == "Schema" and name == "create":
                self._laravel_migration(node)

    def _member_call(self, node: Node, scope: _Scope) -> None:
        receiver = node.child_by_field_name("object")
        name = text(node.child_by_field_name("name"))
        if receiver is None or not name:
            return
        receiver_type: str | None = None
        receiver_text = text(receiver)
        if receiver_text == "$this":
            receiver_type = scope.class_name
        elif receiver.type == "variable_name":
            receiver_type = scope.param_types.get(receiver_text.lstrip("$"))
        elif receiver.type == "member_access_expression":
            if text(receiver.child_by_field_name("object")) == "$this":
                receiver_type = scope.property_types.get(text(receiver.child_by_field_name("name")))
        elif receiver.type == "parenthesized_expression" and "new " in receiver_text:
            created = next((c for c in walk(receiver) if c.type == "object_creation_expression"), None)
            if created is not None:
                target = next((c for c in created.named_children if c.type in ("name", "qualified_name")), None)
                if target is not None:
                    receiver_type = self._resolve_class(text(target))
        self.result.calls.append(
            CallRef(
                name=name,
                line=line(node),
                scope=scope.symbol,
                receiver=receiver_text[:80],
                receiver_type=receiver_type,
            )
        )

    def _new(self, node: Node, scope: _Scope) -> None:
        target = next((c for c in node.named_children if c.type in ("name", "qualified_name")), None)
        if target is None:
            return
        fqn = self._resolve_class(text(target))
        self.result.calls.append(
            CallRef(
                name=fqn.rsplit("\\", 1)[-1],
                line=line(node),
                scope=scope.symbol,
                receiver_type=fqn,
                is_new=True,
            )
        )

    # Laravel ----------------------------------------------------------------
    @staticmethod
    def _route_chain(expression: Node) -> list[tuple[str, Node | None]] | None:
        chain: list[tuple[str, Node | None]] = []
        current: Node | None = expression
        while current is not None and current.type in (
            "member_call_expression",
            "nullsafe_member_call_expression",
        ):
            chain.append((text(current.child_by_field_name("name")), current.child_by_field_name("arguments")))
            current = current.child_by_field_name("object")
        if current is None or current.type != "scoped_call_expression":
            return None
        if text(current.child_by_field_name("scope")).lstrip("\\").rsplit("\\", 1)[-1] != "Route":
            return None
        chain.append((text(current.child_by_field_name("name")), current.child_by_field_name("arguments")))
        chain.reverse()
        return chain

    def _root_route_context(self) -> _RouteContext:
        """Laravel serves everything in routes/api.php under /api by default."""
        return _RouteContext(prefix="/api" if self.path.endswith("routes/api.php") else "")

    def _laravel_route_statement(self, expression: Node, context: _RouteContext) -> bool:
        chain = self._route_chain(expression)
        if chain is None:
            return False
        current = replace(context, middleware=list(context.middleware))
        only: list[str] | None = None
        excluded: list[str] = []
        for name, arguments in chain:
            values = _argument_values(arguments)
            if name == "prefix" and values:
                current.prefix = _join_url(current.prefix, string_value(values[0]) or "")
            elif name == "middleware" and values:
                current.middleware.extend(_array_strings(values[0]))
            elif name == "controller" and values:
                current.controller = self._class_constant(values[0])
            elif name == "namespace" and values:
                current.namespace = string_value(values[0])
            elif name == "only" and values:
                only = _array_strings(values[0])
            elif name == "except" and values:
                excluded = _array_strings(values[0])
        for name, arguments in chain:
            values = _argument_values(arguments)
            if name == "group":
                self._route_group(values, current)
            elif name in _ROUTE_VERBS and len(values) >= 2:
                self._emit_route([name.upper()], values[0], values[1], current)
            elif name == "any" and len(values) >= 2:
                self._emit_route(["ANY"], values[0], values[1], current)
            elif name == "match" and len(values) >= 3:
                methods = [m.upper() for m in _array_strings(values[0])] or ["ANY"]
                self._emit_route(methods, values[1], values[2], current)
            elif name == "view" and len(values) >= 2:
                path = string_value(values[0]) or ""
                self._add_endpoint(
                    "GET",
                    _join_url(current.prefix, path),
                    f"view:{string_value(values[1])}",
                    current,
                    values[0],
                )
            elif name in ("resource", "apiResource") and len(values) >= 2:
                self._emit_resource(name, values, current, only, excluded)
        return True

    def _route_group(self, values: list[Node], context: _RouteContext) -> None:
        current = replace(context, middleware=list(context.middleware))
        closure = next((v for v in values if v.type in ("anonymous_function", "arrow_function")), None)
        # Legacy form: Route::group(['prefix' => 'x', 'middleware' => [...]], function () {...})
        options = _array_pairs(next((v for v in values if v.type == "array_creation_expression"), None))
        if "prefix" in options:
            current.prefix = _join_url(current.prefix, string_value(options["prefix"]) or "")
        if "middleware" in options:
            current.middleware.extend(_array_strings(options["middleware"]))
        if "namespace" in options:
            current.namespace = string_value(options["namespace"])
        if closure is None:
            return
        body = closure.child_by_field_name("body")
        for statement in body.named_children if body is not None else []:
            if statement.type == "expression_statement" and statement.named_children:
                self._laravel_route_statement(statement.named_children[0], current)

    def _class_constant(self, node: Node) -> str | None:
        if node.type == "class_constant_access_expression" and node.named_children:
            return self._resolve_class(text(node.named_children[0]))
        return None

    def _route_handler(self, node: Node, context: _RouteContext) -> str | None:
        if node.type in ("anonymous_function", "arrow_function"):
            return "<closure>"
        constant = self._class_constant(node)
        if constant:
            return constant  # Invokable controller.
        if node.type == "array_creation_expression":
            elements = [
                e.named_children[-1]
                for e in node.named_children
                if e.type == "array_element_initializer" and e.named_children
            ]
            if len(elements) == 2:
                controller = self._class_constant(elements[0]) or string_value(elements[0])
                action = string_value(elements[1])
                if controller and action:
                    return f"{controller}@{action}"
            return None
        value = string_value(node)
        if value is None:
            return None
        if "@" in value:
            controller, _, action = value.partition("@")
            return f"{self._controller_class(controller, context)}@{action}"
        if context.controller:
            return f"{context.controller}@{value}"
        return value

    def _controller_class(self, name: str, context: _RouteContext) -> str:
        if name.startswith("\\"):
            return name.lstrip("\\")
        if name.split("\\")[0] in self.use_map:
            return self._resolve_class(name)
        # Laravel 7 and older resolve string handlers against this namespace.
        base = "App\\Http\\Controllers"
        if context.namespace:
            base = f"{base}\\{context.namespace.strip(chr(92))}"
        return f"{base}\\{name}"

    def _emit_route(self, methods: list[str], path_node: Node, handler_node: Node, context: _RouteContext) -> None:
        path = string_value(path_node)
        if path is None:
            return
        handler = self._route_handler(handler_node, context)
        for method in methods:
            self._add_endpoint(method, _join_url(context.prefix, path), handler, context, path_node)

    def _emit_resource(
        self,
        kind: str,
        values: list[Node],
        context: _RouteContext,
        only: list[str] | None,
        excluded: list[str],
    ) -> None:
        name = string_value(values[0])
        controller = self._class_constant(values[1]) or string_value(values[1])
        if name is None or controller is None:
            return
        actions = _API_RESOURCE_ACTIONS if kind == "apiResource" else tuple(_RESOURCE_ACTIONS)
        for action in actions:
            if (only is not None and action not in only) or action in excluded:
                continue
            method, suffix = _RESOURCE_ACTIONS[action]
            self._add_endpoint(
                method,
                _join_url(context.prefix, name) + suffix,
                f"{controller}@{action}",
                context,
                values[0],
            )

    def _add_endpoint(self, method: str, path: str, handler: str | None, context: _RouteContext, node: Node) -> None:
        self.result.endpoints.append(
            EndpointRef(
                method=method,
                path=path,
                handler=handler,
                framework="laravel",
                line=line(node),
                middleware=list(context.middleware),
            )
        )

    def _class_models(
        self,
        node: Node,
        fqn: str,
        short: str,
        bases: list[str],
        body: Node | None,
        raw_doc: str,
    ) -> None:
        # Drupal entities are declared in an annotation or a PHP 8 attribute.
        source_before = raw_doc + " " + text(node.child(0)) if node.child_count else raw_doc
        match = _DRUPAL_ENTITY_RE.search(source_before) or _DRUPAL_ENTITY_ATTR_RE.search(source_before)
        if match:
            self.result.models.append(
                ModelRef(
                    name=short,
                    framework="drupal-entity",
                    line=line(node),
                    table=match.group(2),
                    fields=self._drupal_base_fields(body),
                )
            )
            return
        if not any(base.rsplit("\\", 1)[-1] in _ELOQUENT_BASES for base in bases) or body is None:
            return
        table: str | None = None
        fields: list[dict[str, str]] = []
        relations: list[dict[str, str]] = []
        casts: dict[str, str] = {}
        fillable: list[str] = []
        for member in body.named_children:
            if member.type == "property_declaration":
                for element in member.named_children:
                    if element.type != "property_element" or len(element.named_children) < 2:
                        continue
                    name = text(element.named_children[0]).lstrip("$")
                    value = element.named_children[-1]
                    if name == "table":
                        table = string_value(value)
                    elif name == "fillable":
                        fillable = _array_strings(value)
                    elif name == "casts":
                        casts = {key: string_value(val) or text(val) for key, val in _array_pairs(value).items()}
            elif member.type == "method_declaration":
                for current in walk(member):
                    if current.type != "member_call_expression":
                        continue
                    if text(current.child_by_field_name("object")) != "$this":
                        continue
                    relation = text(current.child_by_field_name("name"))
                    if relation in _ELOQUENT_RELATIONS:
                        args = _argument_values(current.child_by_field_name("arguments"))
                        target = self._class_constant(args[0]) if args else None
                        relations.append(
                            {
                                "name": text(member.child_by_field_name("name")),
                                "type": relation,
                                "target": target or (text(args[0]) if args else ""),
                            }
                        )
                        break
        # Laravel 11 and newer: `#[Fillable([...])]` on the class and a `casts()` method.
        for attribute in walk(node.child_by_field_name("attributes") or node.children[0]):
            if attribute.type == "attribute" and text(attribute.named_children[0]).rsplit("\\", 1)[-1] == "Fillable":
                values = _argument_values(next((c for c in attribute.named_children if c.type == "arguments"), None))
                if values:
                    fillable = fillable or _array_strings(values[0])
        for member in body.named_children:
            if member.type == "method_declaration" and text(member.child_by_field_name("name")) == "casts":
                returned = next((c for c in walk(member) if c.type == "return_statement"), None)
                array = (
                    next((c for c in walk(returned) if c.type == "array_creation_expression"), None)
                    if returned
                    else None
                )
                casts = casts or {key: string_value(val) or text(val) for key, val in _array_pairs(array).items()}
        for name in fillable:
            fields.append({"name": name, "type": casts.get(name, "fillable")})
        self.result.models.append(
            ModelRef(
                name=fqn,
                framework="eloquent",
                line=line(node),
                table=table,
                fields=fields,
                relations=relations,
            )
        )

    @staticmethod
    def _drupal_base_fields(body: Node | None) -> list[dict[str, str]]:
        """`$fields['name'] = BaseFieldDefinition::create('string')` in baseFieldDefinitions()."""
        fields: list[dict[str, str]] = []
        for member in body.named_children if body is not None else []:
            if (
                member.type != "method_declaration"
                or text(member.child_by_field_name("name")) != "baseFieldDefinitions"
            ):
                continue
            for current in walk(member):
                if current.type != "assignment_expression":
                    continue
                left = current.child_by_field_name("left")
                right = current.child_by_field_name("right")
                if left is None or right is None or not text(left).startswith("$fields["):
                    continue
                name = string_value(left.named_children[-1]) if left.named_children else None
                create = next(
                    (
                        c
                        for c in (right, *walk(right))
                        if c.type == "scoped_call_expression" and text(c.child_by_field_name("name")) == "create"
                    ),
                    None,
                )
                if name is None or create is None:
                    continue
                values = _argument_values(create.child_by_field_name("arguments"))
                fields.append({"name": name, "type": (string_value(values[0]) if values else None) or "unknown"})
        return fields

    def _laravel_migration(self, node: Node) -> None:
        args = _argument_values(node.child_by_field_name("arguments"))
        if len(args) < 2:
            return
        table = string_value(args[0])
        if table is None:
            return
        fields: list[dict[str, str]] = []
        for current in walk(args[1]):
            if current.type != "member_call_expression":
                continue
            receiver = current.child_by_field_name("object")
            if receiver is None or text(receiver) != "$table":
                continue
            column_type = text(current.child_by_field_name("name"))
            if column_type in _MIGRATION_SKIP_COLUMNS:
                continue
            values = _argument_values(current.child_by_field_name("arguments"))
            column = string_value(values[0]) if values else None
            fields.append({"name": column or column_type, "type": column_type})
        self.result.models.append(
            ModelRef(name=table, framework="laravel-migration", line=line(node), table=table, fields=fields)
        )

    # Drupal -----------------------------------------------------------------
    def _drupal_function(self, node: Node, name: str, body: Node | None) -> None:
        module = self.module_name
        if module is None or not name.startswith(f"{module}_"):
            return
        hook = name[len(module) + 1 :]
        if not _DRUPAL_HOOK_RE.match(hook):
            return
        self.result.hooks.append(HookRef(module, hook, name, line(node)))
        if body is None:
            return
        if hook == "menu":
            self._drupal_menu(body)
        elif hook == "schema":
            self._drupal_schema(body)

    def _drupal_menu(self, body: Node) -> None:
        for current in walk(body):
            if current.type != "assignment_expression":
                continue
            left, right = current.child_by_field_name("left"), current.child_by_field_name("right")
            if left is None or right is None or left.type != "subscript_expression":
                continue
            if not text(left).startswith("$items["):
                continue
            path = _dynamic_path(left.named_children[-1]) if left.named_children else None
            if path is None:
                continue
            options = _array_pairs(right)
            callback = string_value(options.get("page callback")) if "page callback" in options else None
            self.result.endpoints.append(EndpointRef("ANY", "/" + path.lstrip("/"), callback, "drupal", line(current)))

    def _drupal_schema(self, body: Node) -> None:
        for current in walk(body):
            if current.type != "assignment_expression":
                continue
            left, right = current.child_by_field_name("left"), current.child_by_field_name("right")
            if left is None or right is None or left.type != "subscript_expression":
                continue
            if not text(left).startswith("$schema["):
                continue
            table = string_value(left.named_children[-1]) if left.named_children else None
            if table is None:
                continue
            definition = _array_pairs(right)
            fields: list[dict[str, str]] = []
            for field_name, spec in _array_pairs(definition.get("fields")).items():
                spec_pairs = _array_pairs(spec)
                field_type = string_value(spec_pairs.get("type")) if "type" in spec_pairs else None
                fields.append({"name": field_name, "type": field_type or "unknown"})
            self.result.models.append(
                ModelRef(name=table, framework="drupal-schema", line=line(current), table=table, fields=fields)
            )


@dataclass
class _Scope:
    symbol: str | None = None
    class_name: str | None = None
    property_types: dict[str, str] = field(default_factory=dict)
    param_types: dict[str, str] = field(default_factory=dict)


def analyze(source: bytes, path: str) -> ParsedFile:
    return _Analyzer(source, path).run()
