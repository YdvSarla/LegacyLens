"""Code quality rules. Every finding carries a file and line and can be checked."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from app.services.analysis.parsed import ParsedFile


@dataclass
class Finding:
    rule: str
    category: str  # maintainability | security | reliability
    severity: str  # low | medium | high | critical
    message: str
    path: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class _LineRule:
    rule: str
    category: str
    severity: str
    message: str
    pattern: re.Pattern[str]
    languages: frozenset[str]
    skip_tests: bool = True


_PHP = frozenset({"php"})
_PY = frozenset({"python"})
_JS = frozenset({"javascript", "typescript"})
_CODE = _PHP | _PY | _JS

_LINE_RULES: tuple[_LineRule, ...] = (
    # Security.
    _LineRule(
        "dangerous-eval", "security", "high", "eval() runs arbitrary code.", re.compile(r"(?<![\w.>$])eval\s*\("), _CODE
    ),
    _LineRule(
        "js-new-function",
        "security",
        "high",
        "new Function() runs arbitrary code.",
        re.compile(r"\bnew\s+Function\s*\("),
        _JS,
    ),
    _LineRule(
        "php-shell-exec",
        "security",
        "high",
        "Shell execution function. Check that no user input reaches it.",
        re.compile(r"(?<![\w>:$])(shell_exec|passthru|system|popen|proc_open)\s*\("),
        _PHP,
    ),
    _LineRule(
        "php-request-in-include",
        "security",
        "critical",
        "Request data is used in an include path.",
        re.compile(r"\b(include|include_once|require|require_once)\b[^;]*\$_(GET|POST|REQUEST|COOKIE)"),
        _PHP,
    ),
    _LineRule(
        "php-unserialize-input",
        "security",
        "critical",
        "unserialize() on request data allows object injection.",
        re.compile(r"\bunserialize\s*\(\s*\$_(GET|POST|REQUEST|COOKIE)"),
        _PHP,
    ),
    _LineRule(
        "php-extract-input",
        "security",
        "high",
        "extract() on request data can overwrite variables.",
        re.compile(r"\bextract\s*\(\s*\$_(GET|POST|REQUEST|COOKIE)"),
        _PHP,
    ),
    _LineRule(
        "php-echo-input",
        "security",
        "high",
        "Request data is echoed without escaping, which allows XSS.",
        re.compile(r"\b(echo|print)\b[^;]*\$_(GET|POST|REQUEST|COOKIE)"),
        _PHP,
    ),
    _LineRule(
        "weak-password-hash",
        "security",
        "high",
        "MD5 or SHA-1 used for a password.",
        re.compile(r"\b(md5|sha1)\s*\([^)]*pass", re.IGNORECASE),
        _CODE,
    ),
    _LineRule(
        "sql-string-building",
        "security",
        "high",
        "SQL built by string concatenation may allow SQL injection.",
        re.compile(
            r"""["'`]\s*(SELECT\s.+\sFROM|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM)\b[^"'`]*["'`]\s*(\.|\+)\s*[$\w]"""
            r"""|\bf["'](SELECT\s.+\sFROM|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM)\b[^"']*\{"""
            r"""|["'](SELECT\s.+\sFROM|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM)\b[^"']*\$_(GET|POST|REQUEST)""",
            re.IGNORECASE,
        ),
        _CODE,
    ),
    _LineRule(
        "py-subprocess-shell",
        "security",
        "medium",
        "subprocess with shell=True can allow command injection.",
        re.compile(r"\bsubprocess\.\w+\(.*shell\s*=\s*True"),
        _PY,
    ),
    _LineRule(
        "py-os-system", "security", "medium", "os.system() runs a shell command.", re.compile(r"\bos\.system\s*\("), _PY
    ),
    _LineRule(
        "py-pickle-load",
        "security",
        "high",
        "pickle.load on untrusted data runs arbitrary code.",
        re.compile(r"\bpickle\.loads?\s*\("),
        _PY,
    ),
    _LineRule(
        "py-yaml-unsafe",
        "security",
        "high",
        "yaml.load without SafeLoader can run arbitrary code.",
        re.compile(r"\byaml\.load\s*\((?!.*Safe)"),
        _PY,
    ),
    _LineRule(
        "tls-verify-disabled",
        "security",
        "high",
        "TLS certificate verification is disabled.",
        re.compile(r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|CURLOPT_SSL_VERIFYPEER\s*,\s*(false|0)"),
        _CODE,
    ),
    _LineRule(
        "js-child-process-exec",
        "security",
        "medium",
        "child_process.exec runs a shell command.",
        re.compile(r"\b(child_process|cp)\.exec\s*\("),
        _JS,
    ),
    _LineRule(
        "js-inner-html",
        "security",
        "medium",
        "Assigning innerHTML can allow XSS.",
        re.compile(r"\.innerHTML\s*=(?!=)|dangerouslySetInnerHTML|document\.write\s*\("),
        _JS,
    ),
    _LineRule(
        "hardcoded-secret",
        "security",
        "high",
        "Looks like a hard-coded credential.",
        re.compile(
            r"""\b(password|passwd|pwd|secret|api[_-]?key|auth[_-]?token|access[_-]?token)['"]?\s*(=>|=|:)\s*['"](?P<value>[^'"\s$%{<]{6,})['"]""",
            re.IGNORECASE,
        ),
        _CODE,
    ),
    _LineRule(
        "private-key-material",
        "security",
        "critical",
        "A private key is stored in source code.",
        re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        _CODE,
    ),
    _LineRule(
        "aws-access-key",
        "security",
        "critical",
        "An AWS access key ID is stored in source code.",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        _CODE,
    ),
    # Removed or dangerous legacy APIs.
    _LineRule(
        "php-removed-mysql",
        "reliability",
        "high",
        "The mysql_* extension was removed in PHP 7. Use PDO or mysqli.",
        re.compile(
            r"(?<![\w>:$])mysql_(query|connect|fetch_\w+|select_db|real_escape_string|num_rows|error|close|result|pconnect)\s*\("
        ),
        _PHP,
    ),
    _LineRule(
        "php-removed-ereg",
        "reliability",
        "high",
        "ereg functions were removed in PHP 7. Use preg_*.",
        re.compile(r"(?<![\w>:$])(ereg|eregi|ereg_replace|eregi_replace|split|spliti)\s*\("),
        _PHP,
    ),
    _LineRule(
        "php-removed-create-function",
        "reliability",
        "high",
        "create_function() was removed in PHP 8.",
        re.compile(r"(?<![\w>:$])create_function\s*\("),
        _PHP,
    ),
    _LineRule(
        "php-error-suppression",
        "reliability",
        "low",
        "The @ operator hides errors.",
        re.compile(r"(?<![\w'\"])@(mysql_|mysqli_|file_|fopen|unlink|include|require|\$)"),
        _PHP,
    ),
    _LineRule(
        "py2-print",
        "reliability",
        "medium",
        "Python 2 print statement. This fails on Python 3.",
        re.compile(r"^\s*print\s+[\"'\w]"),
        _PY,
    ),
    _LineRule(
        "bare-except",
        "reliability",
        "low",
        "Bare except also hides SystemExit and KeyboardInterrupt.",
        re.compile(r"^\s*except\s*:"),
        _PY,
    ),
    # Debug leftovers.
    _LineRule(
        "debug-php",
        "reliability",
        "low",
        "Debug output left in code.",
        re.compile(r"(?<![\w>:$])(var_dump|print_r|dd|dump)\s*\("),
        _PHP,
    ),
    _LineRule(
        "debug-py",
        "reliability",
        "low",
        "Debugger call left in code.",
        re.compile(r"\b(pdb\.set_trace|breakpoint)\s*\("),
        _PY,
    ),
    _LineRule(
        "debug-js",
        "reliability",
        "low",
        "Debug statement left in code.",
        re.compile(r"^\s*debugger\s*;?\s*$|\bconsole\.log\s*\("),
        _JS,
    ),
    # Technical debt markers.
    _LineRule(
        "todo-marker",
        "maintainability",
        "low",
        "Unfinished work marker.",
        re.compile(r"\b(TODO|FIXME|HACK|XXX)\b"),
        _CODE,
        skip_tests=False,
    ),
)

_DEBT_RULE = "todo-marker"
_COMMENT_PREFIXES = ("//", "#", "*", "/*", "<!--")

LONG_FUNCTION_MEDIUM, LONG_FUNCTION_HIGH = 60, 150
COMPLEXITY_LOW, COMPLEXITY_MEDIUM, COMPLEXITY_HIGH = 11, 21, 41
TOO_MANY_PARAMS = 6
LARGE_CLASS_METHODS, LARGE_CLASS_LOC = 25, 600
LARGE_FILE_LOC = 1000
_MAX_PER_RULE_PER_FILE = 5


# Field types and sample values that look like credentials but are not, for example the
# Laravel cast `'password' => 'hashed'`.
_NOT_A_SECRET = re.compile(
    r"^(hashed|encrypted|string|required|nullable|password|passwd|secret|changeme|change[-_]?me|"
    r"example|sample|placeholder|default|dummy|testing|foobar|your[-_].*|.*[-_]here|x{3,}|\*+)$",
    re.IGNORECASE,
)


def _is_placeholder_secret(rule: _LineRule, match: re.Match[str]) -> bool:
    return rule.rule == "hardcoded-secret" and bool(_NOT_A_SECRET.match(match.group("value")))


def _is_declaration(raw: str, match: re.Match[str]) -> bool:
    """`public function split()` declares a method. It does not call the removed PHP function."""
    return raw[: match.start()].rstrip().endswith("function")


def scan_lines(path: str, language: str, role: str, content: str) -> list[Finding]:
    rules = [r for r in _LINE_RULES if language in r.languages]
    # Blade templates mix HTML, JavaScript and directives such as @include, which PHP rules misread.
    if not rules or path.endswith(".blade.php"):
        return []
    findings: list[Finding] = []
    counts: Counter[str] = Counter()
    is_test = role in ("test", "example")
    for number, raw in enumerate(content.splitlines(), start=1):
        if len(raw) > 2000:
            continue  # Generated or minified. Skipping avoids slow regex on huge lines.
        stripped = raw.lstrip()
        is_comment = stripped.startswith(_COMMENT_PREFIXES)
        for rule in rules:
            if is_test and rule.skip_tests:
                continue
            if is_comment and rule.rule != _DEBT_RULE:
                continue
            if counts[rule.rule] >= _MAX_PER_RULE_PER_FILE:
                continue
            match = rule.pattern.search(raw)
            if match and not _is_placeholder_secret(rule, match) and not _is_declaration(raw, match):
                counts[rule.rule] += 1
                findings.append(Finding(rule.rule, rule.category, rule.severity, rule.message, path, number))
    return findings


def structural_findings(path: str, parsed: ParsedFile, loc: int, role: str) -> list[Finding]:
    findings: list[Finding] = []
    if role in ("test", "example"):
        return findings
    if loc > LARGE_FILE_LOC:
        findings.append(
            Finding(
                "large-file",
                "maintainability",
                "medium",
                f"File has {loc} lines of code. Consider splitting it.",
                path,
                1,
            )
        )
    methods_per_class: Counter[str] = Counter()
    for symbol in parsed.symbols:
        if symbol.kind == "method" and symbol.parent:
            methods_per_class[symbol.parent] += 1
    for symbol in parsed.symbols:
        if symbol.kind in ("function", "method"):
            if symbol.loc > LONG_FUNCTION_HIGH:
                findings.append(
                    Finding(
                        "long-function",
                        "maintainability",
                        "high",
                        f"{symbol.name} spans {symbol.loc} lines.",
                        path,
                        symbol.start_line,
                    )
                )
            elif symbol.loc > LONG_FUNCTION_MEDIUM:
                findings.append(
                    Finding(
                        "long-function",
                        "maintainability",
                        "medium",
                        f"{symbol.name} spans {symbol.loc} lines.",
                        path,
                        symbol.start_line,
                    )
                )
            if symbol.complexity >= COMPLEXITY_HIGH:
                severity = "high"
            elif symbol.complexity >= COMPLEXITY_MEDIUM:
                severity = "medium"
            elif symbol.complexity >= COMPLEXITY_LOW:
                severity = "low"
            else:
                severity = None
            if severity:
                findings.append(
                    Finding(
                        "high-complexity",
                        "maintainability",
                        severity,
                        f"{symbol.name} has cyclomatic complexity {symbol.complexity}.",
                        path,
                        symbol.start_line,
                    )
                )
            if symbol.param_count > TOO_MANY_PARAMS:
                findings.append(
                    Finding(
                        "too-many-parameters",
                        "maintainability",
                        "low",
                        f"{symbol.name} takes {symbol.param_count} parameters.",
                        path,
                        symbol.start_line,
                    )
                )
        elif symbol.kind == "class":
            methods = methods_per_class.get(symbol.qualified_name, 0)
            if methods > LARGE_CLASS_METHODS or symbol.loc > LARGE_CLASS_LOC:
                findings.append(
                    Finding(
                        "large-class",
                        "maintainability",
                        "medium",
                        f"{symbol.name} has {methods} methods and spans {symbol.loc} lines.",
                        path,
                        symbol.start_line,
                    )
                )
    if parsed.parse_errors > 0:
        findings.append(
            Finding(
                "syntax-errors",
                "reliability",
                "medium",
                f"The parser recovered from {parsed.parse_errors} syntax problems in this file.",
                path,
                1,
            )
        )
    return findings


def cycle_findings(cycles: list[list[str]]) -> list[Finding]:
    findings: list[Finding] = []
    for cycle in cycles[:50]:
        severity = "high" if len(cycle) > 5 else "medium"
        shown = " -> ".join(cycle[:6]) + (" ..." if len(cycle) > 6 else "")
        findings.append(
            Finding(
                "circular-dependency",
                "maintainability",
                severity,
                f"{len(cycle)} files import each other in a cycle: {shown}",
                cycle[0],
                1,
            )
        )
    return findings


_SEVERITY_WEIGHT = {"low": 0.5, "medium": 2.0, "high": 5.0, "critical": 12.0}
_CATEGORY_WEIGHT = {"maintainability": 0.4, "security": 0.4, "reliability": 0.2}
# Density of weighted findings per 1000 lines at which a score falls to about 37.
_SCORE_SCALE = 25.0
_CAP_WITH_CRITICAL_SECURITY = 59  # At best grade D.
_CAP_WITH_HIGH_SECURITY = 89  # At best grade B.


def compute_scores(findings: list[Finding], total_loc: int) -> dict[str, object]:
    """Scores from 0 to 100, based on weighted findings per thousand lines of code."""
    kloc = max(total_loc / 1000.0, 1.0)
    weights: Counter[str] = Counter()
    for finding in findings:
        weights[finding.category] += _SEVERITY_WEIGHT.get(finding.severity, 1.0)
    categories = {
        category: round(100 * math.exp(-(weights[category] / kloc) / _SCORE_SCALE)) for category in _CATEGORY_WEIGHT
    }
    overall = round(sum(categories[c] * w for c, w in _CATEGORY_WEIGHT.items()))
    # A serious security hole outweighs any amount of tidy code, so it limits the grade.
    security = {f.severity for f in findings if f.category == "security"}
    if "critical" in security:
        overall = min(overall, _CAP_WITH_CRITICAL_SECURITY)
    elif "high" in security:
        overall = min(overall, _CAP_WITH_HIGH_SECURITY)
    if overall >= 90:
        grade = "A"
    elif overall >= 75:
        grade = "B"
    elif overall >= 60:
        grade = "C"
    elif overall >= 40:
        grade = "D"
    else:
        grade = "F"
    return {"overall": overall, "grade": grade, "categories": categories}
