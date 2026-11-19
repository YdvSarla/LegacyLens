"""Plain data produced by the language analyzers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedSymbol:
    kind: str  # function | method | class | interface | trait | enum
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    parent: str | None = None
    signature: str | None = None
    docstring: str | None = None
    complexity: int = 1
    param_count: int = 0
    bases: list[str] = field(default_factory=list)
    implements: list[str] = field(default_factory=list)
    traits: list[str] = field(default_factory=list)

    @property
    def loc(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass
class ImportRef:
    module: str  # As written in the source: "a.b", "./x", "App\\Models\\User"
    names: dict[str, str] = field(default_factory=dict)  # Local alias -> imported name.
    line: int = 0
    # import: loaded when the file loads. include: PHP include or require.
    # use: PHP class name, autoloaded on demand. type_import: only for type checkers.
    # lazy_import: inside a function. Only import and include can form a real cycle.
    kind: str = "import"
    level: int = 0  # Python relative import depth.


@dataclass
class CallRef:
    name: str
    line: int
    scope: str | None = None  # Qualified name of the enclosing symbol.
    receiver: str | None = None  # "self", "this", a module alias or a class name.
    receiver_type: str | None = None  # Class known from a type hint.
    is_new: bool = False


@dataclass
class EndpointRef:
    method: str
    path: str
    handler: str | None
    framework: str
    line: int
    middleware: list[str] = field(default_factory=list)
    router_var: str | None = None  # Express router variable, for mount prefixes.


@dataclass
class ModelRef:
    name: str
    framework: str
    line: int
    table: str | None = None
    fields: list[dict[str, str]] = field(default_factory=list)
    relations: list[dict[str, str]] = field(default_factory=list)


@dataclass
class HookRef:
    module: str
    hook: str
    function: str
    line: int


@dataclass
class MountRef:
    """Express style `app.use('/prefix', router)`."""

    prefix: str
    identifier: str
    line: int


@dataclass
class ParsedFile:
    language: str
    namespace: str | None = None
    symbols: list[ParsedSymbol] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    calls: list[CallRef] = field(default_factory=list)
    endpoints: list[EndpointRef] = field(default_factory=list)
    models: list[ModelRef] = field(default_factory=list)
    hooks: list[HookRef] = field(default_factory=list)
    mounts: list[MountRef] = field(default_factory=list)
    parse_errors: int = 0
    # PHP script that reads request superglobals at the top level.
    request_inputs: set[str] = field(default_factory=set)
    top_level_complexity: int = 1
    global_prefix: str | None = None  # NestJS `app.setGlobalPrefix("api")`.
