"""Resolves imports and calls across files into graph edges."""

from __future__ import annotations

import posixpath
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.services.analysis.parsed import CallRef, ImportRef, ParsedFile, ParsedSymbol

_JS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".json")
_JS_INDEXES = tuple(f"/index{ext}" for ext in (".js", ".jsx", ".ts", ".tsx", ".mjs"))
# Aliases commonly configured in tsconfig or bundlers.
_JS_ALIASES = {"@/": ("src/", "", "app/"), "~/": ("src/", "", "app/"), "@app/": ("src/app/", "app/")}


@dataclass(frozen=True)
class Edge:
    source_path: str
    target_path: str
    # import | include | use | type_import | lazy_import | call | extends | implements | uses_trait | route
    kind: str
    line: int | None = None
    source_symbol: str | None = None
    target_symbol: str | None = None


@dataclass
class _SymbolIndex:
    by_path: dict[str, dict[str, ParsedSymbol]] = field(default_factory=lambda: defaultdict(dict))
    # Top level name -> (path, symbol) per file, used for local lookups.
    top_level: dict[str, dict[str, ParsedSymbol]] = field(default_factory=lambda: defaultdict(dict))
    php_classes: dict[str, tuple[str, ParsedSymbol]] = field(default_factory=dict)
    php_short_classes: dict[str, list[tuple[str, ParsedSymbol]]] = field(default_factory=lambda: defaultdict(list))
    php_functions: dict[str, list[tuple[str, ParsedSymbol]]] = field(default_factory=lambda: defaultdict(list))
    methods: dict[tuple[str, str], tuple[str, ParsedSymbol]] = field(default_factory=dict)


class GraphBuilder:
    def __init__(self, parsed: dict[str, ParsedFile]) -> None:
        self.parsed = parsed
        self.paths = set(parsed)
        self.index = self._build_index()
        self.python_modules = self._python_module_map()
        self.resolved_imports: dict[str, dict[str, str]] = defaultdict(dict)  # path -> alias -> file
        self.included_by: dict[str, set[str]] = defaultdict(set)  # PHP file -> files it includes.
        self.paths_by_name: dict[str, list[str]] = defaultdict(list)
        for known in self.paths:
            self.paths_by_name[posixpath.basename(known)].append(known)
        self.edges: set[Edge] = set()

    # Public -----------------------------------------------------------------
    def build(self) -> list[Edge]:
        for path, parsed in self.parsed.items():
            for reference in parsed.imports:
                self._resolve_import(path, parsed, reference)
        for path, parsed in self.parsed.items():
            self._inheritance(path, parsed)
            for call in parsed.calls:
                self._resolve_call(path, parsed, call)
            self._routes(path, parsed)
        return sorted(self.edges, key=lambda e: (e.source_path, e.target_path, e.kind, e.line or 0))

    def apply_express_mounts(self) -> None:
        """Adds `app.use('/prefix', router)` prefixes to the routes of the mounted file."""
        for path, parsed in self.parsed.items():
            for mount in parsed.mounts:
                target: str | None = None
                if mount.identifier.startswith("require:"):
                    target = self._resolve_js(path, mount.identifier.removeprefix("require:"))
                    router_var = None
                else:
                    target = self.resolved_imports[path].get(mount.identifier)
                    router_var = mount.identifier
                if target and target != path:
                    for endpoint in self.parsed[target].endpoints:
                        if endpoint.framework == "express":
                            endpoint.path = _join_paths(mount.prefix, endpoint.path)
                elif target == path or target is None:
                    for endpoint in parsed.endpoints:
                        if endpoint.framework == "express" and endpoint.router_var == router_var:
                            endpoint.path = _join_paths(mount.prefix, endpoint.path)

    def apply_route_prefixes(self) -> None:
        """Resolves routes whose final path is decided in another file."""
        self.apply_express_mounts()
        self._apply_django_includes()
        self._apply_nest_prefix()

    def _apply_nest_prefix(self) -> None:
        prefix = next((p.global_prefix for p in self.parsed.values() if p.global_prefix), None)
        if not prefix:
            return
        for parsed in self.parsed.values():
            for endpoint in parsed.endpoints:
                if endpoint.framework == "nestjs":
                    endpoint.path = _join_paths(prefix, endpoint.path)

    def _apply_django_includes(self) -> None:
        """`path("api/", include("app.urls"))`: every route in app/urls.py starts with /api/."""
        includes: dict[str, list[tuple[str, str]]] = defaultdict(list)
        included: set[str] = set()
        for path, parsed in self.parsed.items():
            kept = []
            for endpoint in parsed.endpoints:
                handler = endpoint.handler or ""
                if endpoint.framework != "django" or not handler.startswith("include("):
                    kept.append(endpoint)
                    continue
                module = handler.removeprefix("include(").rstrip(")")
                target = self.python_modules.get(module)
                if target and target != path:
                    includes[path].append((target, endpoint.path))
                    included.add(target)
                    self.edges.add(Edge(path, target, "include", endpoint.line))
                elif not module.endswith(".urls"):
                    kept.append(endpoint)  # A package that is not part of this project.
            parsed.endpoints = kept
        done: set[str] = set()

        def apply(path: str, prefix: str) -> None:
            if path in done:
                return
            done.add(path)
            if prefix.strip("/"):
                for endpoint in self.parsed[path].endpoints:
                    if endpoint.framework == "django":
                        endpoint.path = _join_paths(prefix, endpoint.path)
            for target, sub_prefix in includes.get(path, ()):
                apply(target, _join_paths(prefix or "/", sub_prefix))

        for root in sorted(set(includes) - included):
            apply(root, "")

    # Indexes ----------------------------------------------------------------
    def _build_index(self) -> _SymbolIndex:
        index = _SymbolIndex()
        for path, parsed in self.parsed.items():
            for symbol in parsed.symbols:
                index.by_path[path][symbol.qualified_name] = symbol
                if symbol.parent is None:
                    index.top_level[path][symbol.name] = symbol
                if parsed.language == "php":
                    if symbol.kind in ("class", "interface", "trait", "enum"):
                        index.php_classes[symbol.qualified_name.lower()] = (path, symbol)
                        index.php_short_classes[symbol.name.lower()].append((path, symbol))
                    elif symbol.kind == "function":
                        index.php_functions[symbol.name.lower()].append((path, symbol))
                if symbol.parent:
                    key = (symbol.parent.lower(), symbol.name.lower())
                    index.methods.setdefault(key, (path, symbol))
        return index

    def _python_module_map(self) -> dict[str, str]:
        modules: dict[str, str] = {}
        for path, parsed in self.parsed.items():
            if parsed.language != "python":
                continue
            pure = PurePosixPath(path)
            parts = list(pure.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            # Every suffix is a candidate so `src/app/x.py` matches `app.x` too.
            for start in range(len(parts)):
                module = ".".join(parts[start:])
                if module:
                    # A shorter root (more complete path) wins over a deeper one.
                    modules.setdefault(module, path)
        return modules

    # Import resolution ------------------------------------------------------
    def _resolve_import(self, path: str, parsed: ParsedFile, reference: ImportRef) -> None:
        if parsed.language == "python":
            self._resolve_python_import(path, reference)
        elif parsed.language in ("javascript", "typescript"):
            target = self._resolve_js(path, reference.module)
            if target:
                self._add_import(path, target, reference, reference.names)
        elif parsed.language == "php":
            self._resolve_php_import(path, reference)

    def _add_import(self, path: str, target: str, reference: ImportRef, names: dict[str, str] | None = None) -> None:
        if target == path:
            return
        self.edges.add(Edge(path, target, reference.kind, reference.line))
        if reference.kind == "include":
            self.included_by[path].add(target)
        # A name imported from a file is used there even if no call to it can be resolved.
        for name in (names or {}).values():
            symbol = self.index.top_level.get(target, {}).get(name.rsplit("\\", 1)[-1])
            if symbol is not None:
                self.edges.add(Edge(path, target, reference.kind, reference.line, None, symbol.qualified_name))
        for alias in names or {}:
            self.resolved_imports[path][alias] = target

    def _resolve_python_import(self, path: str, reference: ImportRef) -> None:
        module = reference.module
        if reference.level:
            base = list(PurePosixPath(path).parent.parts)
            drop = reference.level - 1
            base = base[: len(base) - drop] if drop else base
            module = ".".join([*base, module] if module else base)
        # `from pkg import sub` may refer to the module pkg.sub.
        for alias, name in reference.names.items():
            if name != "*":
                candidate = self.python_modules.get(f"{module}.{name}" if module else name)
                if candidate:
                    self._add_import(path, candidate, reference, {alias: candidate})
                    self.resolved_imports[path][alias] = candidate
        target = self.python_modules.get(module)
        remaining = {alias: name for alias, name in reference.names.items() if alias not in self.resolved_imports[path]}
        # `from . import a, b` only imports submodules, so it is not a dependency on __init__.py.
        if target and remaining:
            self._add_import(path, target, reference, remaining)

    def _resolve_js(self, path: str, module: str) -> str | None:
        candidates: list[str] = []
        if module.startswith("."):
            candidates.append(posixpath.normpath(posixpath.join(posixpath.dirname(path), module)))
        else:
            for alias, roots in _JS_ALIASES.items():
                if module.startswith(alias):
                    tail = module[len(alias) :]
                    candidates.extend(root + tail for root in roots)
        for base in candidates:
            if base in self.paths:
                return base
            # TypeScript sources imported with the compiled `.js` extension.
            stem, ext = posixpath.splitext(base)
            if ext in (".js", ".jsx", ".mjs"):
                for replacement in (".ts", ".tsx", ".mts"):
                    if stem + replacement in self.paths:
                        return stem + replacement
            for suffix in (*_JS_EXTENSIONS, *_JS_INDEXES):
                if base + suffix in self.paths:
                    return base + suffix
        return None

    def _resolve_php_import(self, path: str, reference: ImportRef) -> None:
        if reference.kind == "include":
            target = self._resolve_php_include(path, reference.module)
            if target:
                self._add_import(path, target, reference)
            return
        found = self.index.php_classes.get(reference.module.lower())
        if found:
            self._add_import(path, found[0], reference, reference.names)

    def _resolve_php_include(self, path: str, module: str) -> str | None:
        directory = posixpath.dirname(path)
        cleaned = module.lstrip("/")
        for candidate in (
            posixpath.normpath(posixpath.join(directory, module.lstrip("/"))),
            posixpath.normpath(cleaned),
        ):
            if candidate in self.paths:
                return candidate
        # Legacy include_path style: match the unique file with this ending.
        ending = "/" + cleaned
        matches = [p for p in self.paths_by_name.get(posixpath.basename(cleaned), ()) if p.endswith(ending)]
        return matches[0] if len(matches) == 1 else None

    # Inheritance ------------------------------------------------------------
    def _inheritance(self, path: str, parsed: ParsedFile) -> None:
        for symbol in parsed.symbols:
            for kind, names in (
                ("extends", symbol.bases),
                ("implements", symbol.implements),
                ("uses_trait", symbol.traits),
            ):
                for name in names:
                    target = self._resolve_type(path, parsed, name)
                    if target and target[0] != path:
                        self.edges.add(
                            Edge(
                                path,
                                target[0],
                                kind,
                                symbol.start_line,
                                symbol.qualified_name,
                                target[1].qualified_name,
                            )
                        )

    def _resolve_type(self, path: str, parsed: ParsedFile, name: str) -> tuple[str, ParsedSymbol] | None:
        if parsed.language == "php":
            found = self.index.php_classes.get(name.lower())
            if found:
                return found
            # A global (no namespace) class referenced from a namespace, for legacy code.
            short = name.rsplit("\\", 1)[-1].lower()
            matches = self.index.php_short_classes.get(short, [])
            return matches[0] if len(matches) == 1 else None
        short = name.split(".")[-1]
        local = self.index.top_level.get(path, {}).get(short)
        if local:
            return path, local
        return self._symbol_via_import(path, short)

    def _symbol_via_import(self, path: str, alias: str) -> tuple[str, ParsedSymbol] | None:
        target = self.resolved_imports[path].get(alias)
        if not target:
            return None
        parsed = self.parsed[path]
        imported = alias
        for reference in parsed.imports:
            if alias in reference.names and reference.names[alias] not in ("*", "default"):
                imported = reference.names[alias]
                break
        symbols = self.index.top_level.get(target, {})
        symbol = symbols.get(imported)
        if symbol is None and len(symbols) == 1:
            symbol = next(iter(symbols.values()))  # Default export of a single symbol.
        return (target, symbol) if symbol else None

    # Call resolution --------------------------------------------------------
    def _resolve_call(self, path: str, parsed: ParsedFile, call: CallRef) -> None:
        if parsed.language == "php":
            resolved = self._php_call(path, parsed, call)
        else:
            resolved = self._script_call(path, parsed, call)
        if resolved is None:
            return
        target_path, target_symbol = resolved
        self.edges.add(
            Edge(
                path,
                target_path,
                "call",
                call.line,
                call.scope,
                target_symbol.qualified_name,
            )
        )

    def _script_call(self, path: str, parsed: ParsedFile, call: CallRef) -> tuple[str, ParsedSymbol] | None:
        receiver = call.receiver
        if receiver in ("self", "this", "cls") and call.scope:
            owner = self._owner_class(path, call.scope)
            if owner:
                found = self.index.methods.get((owner.lower(), call.name.lower()))
                if found:
                    return found
            return None
        if receiver:
            head = receiver.split(".")[-1]
            target = self.resolved_imports[path].get(head) or self.resolved_imports[path].get(receiver.split(".")[0])
            if target:
                symbol = self.index.top_level.get(target, {}).get(call.name)
                if symbol:
                    return target, symbol
                # `const svc = require('./svc'); svc.run()` where run is a method.
                for candidate in self.index.by_path.get(target, {}).values():
                    if candidate.name == call.name:
                        return target, candidate
            return None
        local = self.index.top_level.get(path, {}).get(call.name)
        if local:
            return path, local
        return self._symbol_via_import(path, call.name)

    def _owner_class(self, path: str, scope: str) -> str | None:
        symbols = self.index.by_path.get(path, {})
        parts = scope.split(".")
        while parts:
            candidate = ".".join(parts)
            symbol = symbols.get(candidate)
            if symbol is not None and symbol.kind == "class":
                return candidate
            if symbol is not None and symbol.parent:
                return symbol.parent
            parts.pop()
        return None

    def _php_call(self, path: str, parsed: ParsedFile, call: CallRef) -> tuple[str, ParsedSymbol] | None:
        if call.is_new and call.receiver_type:
            return self._resolve_type(path, parsed, call.receiver_type)
        if call.receiver_type:
            found = self.index.methods.get((call.receiver_type.lower(), call.name.lower()))
            if found:
                return found
            # The method may be inherited, so at least link to the class.
            owner = self._resolve_type(path, parsed, call.receiver_type)
            if owner:
                for base in owner[1].bases:
                    inherited = self.index.methods.get((base.lower(), call.name.lower()))
                    if inherited:
                        return inherited
            return owner
        if call.receiver == "parent" and call.scope:
            enclosing = self.index.by_path.get(path, {}).get(call.scope)
            owner_name = enclosing.parent if enclosing else None
            owner = self.index.by_path.get(path, {}).get(owner_name) if owner_name else None
            for base in owner.bases if owner else []:
                inherited = self.index.methods.get((base.lower(), call.name.lower()))
                if inherited:
                    return inherited
            return None
        if call.receiver is None:
            local = self.index.top_level.get(path, {}).get(call.name)
            if local:
                return path, local
            # Legacy PHP shares one global function table across included files.
            matches = self.index.php_functions.get(call.name.lower(), [])
            if len(matches) == 1:
                return matches[0]
            for match in matches:
                if match[0] in self.included_by[path]:
                    return match
        return None

    # Routes -----------------------------------------------------------------
    def _routes(self, path: str, parsed: ParsedFile) -> None:
        for endpoint in parsed.endpoints:
            handler = endpoint.handler
            if not handler or handler.startswith(("<", "view:", "include(")):
                continue
            target = self._route_target(path, parsed, handler)
            if target and target[0] != path:
                self.edges.add(Edge(path, target[0], "route", endpoint.line, None, target[1].qualified_name))

    def _route_target(self, path: str, parsed: ParsedFile, handler: str) -> tuple[str, ParsedSymbol] | None:
        if "@" in handler:
            controller, _, action = handler.partition("@")
            found = self.index.methods.get((controller.lower(), action.lower()))
            if found:
                return found
            return self.index.php_classes.get(controller.lower())
        if "::" in handler:  # Drupal: `\Drupal\mod\Controller\Foo::bar`
            controller, _, action = handler.lstrip("\\").partition("::")
            found = self.index.methods.get((controller.lower(), action.lower()))
            if found:
                return found
            return self.index.php_classes.get(controller.lower())
        if parsed.language == "php":
            found = self.index.php_classes.get(handler.lstrip("\\").lower())
            if found:
                return found
            matches = self.index.php_functions.get(handler.lower(), [])
            return matches[0] if len(matches) == 1 else None
        # JavaScript or Python: `controller.method` or a bare imported handler.
        head, _, tail = handler.partition(".")
        target = self.resolved_imports[path].get(head)
        if target:
            symbols = self.index.top_level.get(target, {})
            if tail and tail in symbols:
                return target, symbols[tail]
            for candidate in self.index.by_path.get(target, {}).values():
                if candidate.name == (tail or head):
                    return target, candidate
        return None


def _join_paths(prefix: str, path: str) -> str:
    """Keeps a trailing slash that belongs to the route, which matters in Django."""
    start = "/" + prefix.strip("/") if prefix.strip("/") else ""
    if not path.strip("/"):
        return start or "/"
    return f"{start}/{path.lstrip('/')}"


def find_import_cycles(edges: list[Edge]) -> list[list[str]]:
    """Strongly connected components of size above one, using iterative Tarjan.

    Only imports that load when the file loads are followed. A PHP `use`, a type-only
    import and a function-local import do not create a cycle at runtime.
    """
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.kind in ("import", "include") and edge.source_path != edge.target_path:
            adjacency[edge.source_path].append(edge.target_path)
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    cycles: list[list[str]] = []
    for start in sorted(adjacency):
        if start in index_of:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, child_index = work.pop()
            if child_index == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            neighbours = adjacency.get(node, [])
            advanced = False
            for position in range(child_index, len(neighbours)):
                neighbour = neighbours[position]
                if neighbour not in index_of:
                    work.append((node, position + 1))
                    work.append((neighbour, 0))
                    advanced = True
                    break
                if neighbour in on_stack:
                    low[node] = min(low[node], index_of[neighbour])
            if advanced:
                continue
            if low[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    cycles.append(sorted(component))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return sorted(cycles, key=len, reverse=True)
