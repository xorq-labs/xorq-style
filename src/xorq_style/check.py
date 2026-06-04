"""
Style enforcement for xorq Python projects.

Deferred = inside a function/method body, outside TYPE_CHECKING blocks.
Non-stdlib deferred imports are allowed in non-test files (e.g., heavy
imports deferred inside Click commands).
"""

from __future__ import annotations

import ast
import bisect
import builtins
import json
import os
import sys
import types
from dataclasses import dataclass  # xorq-style: disable=dataclasses

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    import enum

    class StrEnum(str, enum.Enum):
        pass


from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, Protocol, runtime_checkable

import click
from click.shell_completion import get_completion_class

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = [
    "RULES",
    "Config",
    "RuleId",
    "Violation",
    "check",
    "load_config",
    "main",
]


class RuleId(StrEnum):
    RELATIVE_IMPORT = "relative-import"
    TEST_CLASS = "test-class"
    DEFERRED_IMPORT_TEST = "deferred-import-test"
    DEFERRED_STDLIB = "deferred-stdlib"
    OS_ENVIRON = "os-environ"
    FUTURE_ANNOTATIONS = "future-annotations"
    OS_PATH = "os-path"
    DATACLASSES = "dataclasses"
    CACHE_METHOD = "cache-method"
    EXCEPTION_HIERARCHY = "exception-hierarchy"
    REDUNDANT_IMPORT = "redundant-import"
    PRINT = "print"
    TYPE_ANNOTATIONS = "type-annotations"


RULES: Mapping[RuleId, str] = types.MappingProxyType(
    {
        RuleId.RELATIVE_IMPORT: "No relative imports (use absolute imports)",
        RuleId.TEST_CLASS: "No test classes (use plain test functions)",
        RuleId.DEFERRED_IMPORT_TEST: "No deferred imports in test files",
        RuleId.DEFERRED_STDLIB: "No deferred stdlib imports (anywhere)",
        RuleId.OS_ENVIRON: "No os.environ outside common/utils/",
        RuleId.FUTURE_ANNOTATIONS: "Missing `from __future__ import annotations`",
        RuleId.OS_PATH: "No os.path (use pathlib.Path)",
        RuleId.DATACLASSES: "No dataclasses (use attrs)",
        RuleId.CACHE_METHOD: "No @functools.cache/lru_cache on methods (leaks memory via self)",
        RuleId.EXCEPTION_HIERARCHY: "Custom exceptions must inherit from XorqError",
        RuleId.REDUNDANT_IMPORT: (
            "No redundant deferred imports (module already imported at top level)"
        ),
        RuleId.PRINT: "No bare print() in library code (use logging/click.echo)",
        RuleId.TYPE_ANNOTATIONS: "Functions must have type annotations",
    }
)

STDLIB = sys.stdlib_module_names

STDLIB_EXCEPTIONS = frozenset(
    name
    for name in dir(builtins)
    if isinstance(cls := getattr(builtins, name, None), type) and issubclass(cls, BaseException)
)


@dataclass(frozen=True)
class Violation:
    filepath: str
    line: int
    rule: RuleId
    msg: str

    def __str__(self) -> str:
        return f"{self.filepath}:{self.line}: [{self.rule}] {self.msg}"


@dataclass(frozen=True)
class Config:
    disabled: frozenset[RuleId] = frozenset()
    environ_allow_paths: tuple[str, ...] = ()
    exception_base_class: str = "XorqError"
    print_allow_files: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CheckContext:
    filepath: str
    path: Path
    tree: ast.Module
    source: str
    is_test: bool
    disabled: frozenset[RuleId]
    config: Config
    suppressions: dict[int, frozenset[str]]
    walked: tuple[tuple[ast.AST, tuple[ast.AST, ...]], ...]

    def enabled(self, rule: RuleId) -> bool:
        return rule not in self.disabled

    def violation(self, line: int, rule: RuleId, msg: str) -> Violation:
        return Violation(self.filepath, line, rule, msg)


@runtime_checkable
class RuleChecker(Protocol):
    rule: RuleId

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]: ...


class FutureAnnotationsRule:
    rule = RuleId.FUTURE_ANNOTATIONS

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or not ctx.source.strip():
            return ()
        has_it = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
            and any(alias.name == "annotations" for alias in node.names)
            for node in ast.iter_child_nodes(ctx.tree)
        )
        if has_it:
            return ()
        return (ctx.violation(1, self.rule, "missing `from __future__ import annotations`"),)


class RelativeImportRule:
    rule = RuleId.RELATIVE_IMPORT

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule):
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            match node:
                case ast.ImportFrom(level=level) if level and level > 0:
                    yield ctx.violation(
                        node.lineno, self.rule, "relative import (use absolute import)"
                    )


class TestClassRule:
    rule = RuleId.TEST_CLASS

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or not ctx.is_test:
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            match node:
                case ast.ClassDef(name=name) if name.startswith("Test"):
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        f"test class `{name}` (use plain test functions)",
                    )


class DeferredImportTestRule:
    rule = RuleId.DEFERRED_IMPORT_TEST

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or not ctx.is_test:
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, parents in ctx.walked:
            if (
                not isinstance(node, ast.Import | ast.ImportFrom)
                or not _in_function(parents)
                or _in_type_checking(parents)
            ):
                continue
            mods = _top_modules(node)
            yield ctx.violation(
                node.lineno,
                self.rule,
                f"deferred import in test: {', '.join(mods) or '?'}",
            )


class DeferredStdlibRule:
    rule = RuleId.DEFERRED_STDLIB

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule):
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, parents in ctx.walked:
            if (
                not isinstance(node, ast.Import | ast.ImportFrom)
                or not _in_function(parents)
                or _in_type_checking(parents)
            ):
                continue
            for m in _top_modules(node):
                if m in STDLIB:
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        f"deferred stdlib import `{m}` (move to top of file)",
                    )


class RedundantImportRule:
    rule = RuleId.REDUNDANT_IMPORT

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule):
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        toplevel_modules: set[str] = set()
        for node, parents in ctx.walked:
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            if _in_function(parents) or _in_type_checking(parents):
                continue
            toplevel_modules.update(_top_modules(node))

        for node, parents in ctx.walked:
            if (
                not isinstance(node, ast.Import | ast.ImportFrom)
                or not _in_function(parents)
                or _in_type_checking(parents)
            ):
                continue
            for m in _top_modules(node):
                if m in toplevel_modules:
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        f"redundant deferred import `{m}` (already imported at top level)",
                    )


class OsEnvironRule:
    rule = RuleId.OS_ENVIRON

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule):
            return ()
        path_str = str(ctx.path)
        if any(fragment in path_str for fragment in ctx.config.environ_allow_paths):
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            match node:
                case ast.Attribute(attr="environ", value=ast.Name(id="os")):
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        "os.environ (use xorq.common.utils.env_utils instead)",
                    )


class OsPathRule:
    rule = RuleId.OS_PATH

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule):
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            match node:
                case ast.Attribute(value=ast.Attribute(attr="path", value=ast.Name(id="os"))):
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        f"os.path.{node.attr} (use pathlib.Path instead)",
                    )
                case ast.ImportFrom(module=module) if module is not None and (
                    module == "os.path" or module.startswith("os.path.")
                ):
                    names = ", ".join(a.name for a in node.names)
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        f"from {module} import {names} (use pathlib.Path instead)",
                    )
                case ast.Import():
                    for alias in node.names:
                        if alias.name == "os.path" or alias.name.startswith("os.path."):
                            yield ctx.violation(
                                node.lineno,
                                self.rule,
                                f"import {alias.name} (use pathlib.Path instead)",
                            )


class DataclassesRule:
    rule = RuleId.DATACLASSES

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule):
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            match node:
                case ast.Import() | ast.ImportFrom() if "dataclasses" in _top_modules(node):
                    yield ctx.violation(
                        node.lineno, self.rule, "dataclasses import (use attrs instead)"
                    )


class CacheMethodRule:
    rule = RuleId.CACHE_METHOD

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule):
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, parents in ctx.walked:
            match node:
                case ast.FunctionDef() | ast.AsyncFunctionDef() if _is_in_class(parents):
                    for dec in node.decorator_list:
                        is_cache = False
                        match dec:
                            case ast.Name(id="cache" | "lru_cache"):
                                is_cache = True
                            case ast.Attribute(
                                attr="cache" | "lru_cache",
                                value=ast.Name(id="functools"),
                            ):
                                is_cache = True
                            case ast.Call(
                                func=ast.Name(id="lru_cache")
                                | ast.Attribute(
                                    attr="lru_cache",
                                    value=ast.Name(id="functools"),
                                )
                            ):
                                is_cache = True
                        if is_cache:
                            yield ctx.violation(
                                dec.lineno,
                                self.rule,
                                f"@functools.cache/lru_cache on method `{node.name}`"
                                " (leaks memory via self)",
                            )


class ExceptionHierarchyRule:
    rule = RuleId.EXCEPTION_HIERARCHY

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or _is_exceptions_module(ctx.path):
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            match node:
                case ast.ClassDef(name=name, bases=bases) if bases and (
                    name.endswith("Error") or name.endswith("Exception")
                ):
                    base_class = ctx.config.exception_base_class
                    has_project_base = any(
                        (isinstance(b, ast.Name) and b.id == base_class)
                        or (isinstance(b, ast.Attribute) and b.attr == base_class)
                        for b in bases
                    )
                    if has_project_base:
                        continue
                    name_bases = {b.id for b in bases if isinstance(b, ast.Name)}
                    if (
                        name_bases & STDLIB_EXCEPTIONS
                        and all(isinstance(b, ast.Name) for b in bases)
                        and not name_bases - STDLIB_EXCEPTIONS
                    ):
                        yield ctx.violation(
                            node.lineno,
                            self.rule,
                            f"`{name}` inherits from stdlib exception"
                            f" (use {ctx.config.exception_base_class})",
                        )


class PrintRule:
    rule = RuleId.PRINT

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or ctx.is_test:
            return ()
        if ctx.path.name in ctx.config.print_allow_files:
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            match node:
                case ast.Call(func=ast.Name(id="print")):
                    yield ctx.violation(
                        node.lineno, self.rule, "bare print() (use logging or click.echo)"
                    )


class TypeAnnotationsRule:
    rule = RuleId.TYPE_ANNOTATIONS

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule):
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, parents in ctx.walked:
            match node:
                case ast.FunctionDef() | ast.AsyncFunctionDef() if not _in_function(parents):
                    missing: list[str] = []
                    for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                        if arg.arg in ("self", "cls"):
                            continue
                        if arg.annotation is None:
                            missing.append(arg.arg)
                    if node.args.vararg and node.args.vararg.annotation is None:
                        missing.append(f"*{node.args.vararg.arg}")
                    if node.args.kwarg and node.args.kwarg.annotation is None:
                        missing.append(f"**{node.args.kwarg.arg}")
                    if node.returns is None:
                        missing.append("return")
                    if missing:
                        yield ctx.violation(
                            node.lineno,
                            self.rule,
                            f"missing type annotations: {', '.join(missing)}",
                        )


ALL_RULES: tuple[RuleChecker, ...] = (
    FutureAnnotationsRule(),
    RelativeImportRule(),
    TestClassRule(),
    DeferredImportTestRule(),
    DeferredStdlibRule(),
    RedundantImportRule(),
    OsEnvironRule(),
    OsPathRule(),
    DataclassesRule(),
    CacheMethodRule(),
    ExceptionHierarchyRule(),
    PrintRule(),
    TypeAnnotationsRule(),
)


# Intentionally unbounded: this runs as a short-lived CLI, not a long-lived server.
@cache
def _find_pyproject(start: Path) -> Path | None:
    for parent in (start, *start.parents):
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def load_config(start: Path | None = None) -> Config:
    if start is None:
        start = Path.cwd()
    pyproject = _find_pyproject(start)
    if pyproject is None:
        return Config()

    with open(pyproject, "rb") as f:
        data = tomllib.load(f)

    tool = data.get("tool", {}).get("xorq-style", {})
    if not tool:
        return Config()

    disabled = frozenset(RuleId(r) for r in tool.get("disable", ()))

    environ_cfg = tool.get("os-environ", {})
    environ_allow = tuple(environ_cfg.get("allow-paths", ()))

    exception_cfg = tool.get("exception-hierarchy", {})
    base_class = exception_cfg.get("base-class", "XorqError")

    print_cfg = tool.get("print", {})
    print_allow = frozenset(print_cfg.get("allow-files", ()))

    return Config(
        disabled=disabled,
        environ_allow_paths=environ_allow,
        exception_base_class=base_class,
        print_allow_files=print_allow,
    )


def _is_test_file(path: Path) -> bool:
    return path.name.startswith("test_") or path.name == "conftest.py"


def _is_in_class(parents: tuple[ast.AST, ...]) -> bool:
    return any(isinstance(p, ast.ClassDef) for p in parents)


def _is_exceptions_module(path: Path) -> bool:
    return path.name == "exceptions.py"


def _walk_with_parents(
    node: ast.AST, parents: tuple[ast.AST, ...] = ()
) -> Iterator[tuple[ast.AST, tuple[ast.AST, ...]]]:
    yield node, parents
    new_parents = (*parents, node)
    for child in ast.iter_child_nodes(node):
        yield from _walk_with_parents(child, new_parents)


def _in_function(parents: tuple[ast.AST, ...]) -> bool:
    return any(isinstance(p, ast.FunctionDef | ast.AsyncFunctionDef) for p in parents)


def _in_type_checking(parents: tuple[ast.AST, ...]) -> bool:
    for p in parents:
        match p:
            case ast.If(test=ast.Name(id="TYPE_CHECKING")):
                return True
            case ast.If(test=ast.Attribute(attr="TYPE_CHECKING")):
                return True
    return False


def _top_modules(node: ast.AST) -> tuple[str, ...]:
    match node:
        case ast.Import():
            return tuple(a.name.split(".")[0] for a in node.names)
        case ast.ImportFrom(module=module) if module:
            return (module.split(".")[0],)
        case _:
            return ()


_SUPPRESS_PREFIX = "# xorq-style: disable="


def _suppressed_rules(source: str) -> dict[int, frozenset[str]]:
    result: dict[int, frozenset[str]] = {}
    for lineno, line in enumerate(source.splitlines(), 1):
        idx = line.find(_SUPPRESS_PREFIX)
        if idx < 0:
            continue
        rest = line[idx + len(_SUPPRESS_PREFIX) :].strip()
        rules = frozenset(r.strip() for r in rest.split(",") if r.strip())
        if rules:
            result[lineno] = rules
    return result


def _changed_lines(
    filepath: str, new_string: str, old_string: str | None = None
) -> frozenset[int] | None:
    try:
        content = Path(filepath).read_text()
    except (OSError, UnicodeDecodeError):
        return None

    newline_offsets = tuple(i for i, c in enumerate(content) if c == "\n")

    def _line_at(offset: int) -> int:
        return bisect.bisect_left(newline_offsets, offset) + 1

    if not new_string:
        return None

    if old_string is not None and old_string != new_string:
        positions: list[int] = []
        pos = 0
        while (idx := content.find(new_string, pos)) >= 0:
            positions.append(idx)
            pos = idx + 1

        if len(positions) == 1:
            start_line = _line_at(positions[0])
            end_line = start_line + new_string.count("\n")
            return frozenset(range(start_line, end_line + 1))

        for idx in positions:
            candidate = content[:idx] + old_string + content[idx + len(new_string) :]
            if candidate.count(old_string) == 1:
                start_line = _line_at(idx)
                end_line = start_line + new_string.count("\n")
                return frozenset(range(start_line, end_line + 1))

    # Ambiguous: multiple occurrences, none uniquely identified — report all (may over-report).
    lines: set[int] = set()
    pos = 0
    while (idx := content.find(new_string, pos)) >= 0:
        start_line = _line_at(idx)
        end_line = start_line + new_string.count("\n")
        lines.update(range(start_line, end_line + 1))
        pos = idx + 1
    return frozenset(lines) or None


def check(
    filepath: str,
    only_lines: frozenset[int] | None = None,
    disabled: frozenset[RuleId] = frozenset(),
    config: Config | None = None,
) -> tuple[Violation, ...]:
    if config is None:
        config = Config()
    all_disabled = disabled | config.disabled

    path = Path(filepath)
    if path.suffix != ".py" or not path.exists():
        return ()

    if "vendor" in path.parts:
        return ()

    try:
        source = path.read_text()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"xorq-check-style: cannot parse {filepath}: {exc}\n")
        return ()

    ctx = CheckContext(
        filepath=filepath,
        path=path,
        tree=tree,
        source=source,
        is_test=_is_test_file(path),
        disabled=all_disabled,
        config=config,
        suppressions=_suppressed_rules(source),
        walked=tuple(_walk_with_parents(tree)),
    )

    results: list[Violation] = []
    for rule in ALL_RULES:
        results.extend(rule.check(ctx))
    if only_lines is not None:
        results = [v for v in results if v.line in only_lines]
    if ctx.suppressions:
        results = [v for v in results if v.rule not in ctx.suppressions.get(v.line, frozenset())]
    return tuple(results)


class _DisableType(click.ParamType):  # type: ignore[type-arg]
    name = "rules"

    def convert(
        self, value: str, param: click.Parameter | None, ctx: click.Context | None
    ) -> frozenset[RuleId]:
        if not value:
            return frozenset()
        disabled: set[RuleId] = set()
        for r in value.split(","):
            r = r.strip()
            try:
                disabled.add(RuleId(r))
            except ValueError:
                self.fail(
                    f"unknown rule: {r}. Available: {', '.join(sorted(RULES))}",
                    param,
                    ctx,
                )
        return frozenset(disabled)

    def shell_complete(
        self, ctx: click.Context, param: click.Parameter, incomplete: str
    ) -> list[click.shell_completion.CompletionItem]:
        if "," in incomplete:
            prefix = incomplete[: incomplete.rindex(",") + 1]
            last = incomplete[incomplete.rindex(",") + 1 :]
        else:
            prefix = ""
            last = incomplete
        return [
            click.shell_completion.CompletionItem(f"{prefix}{r}")
            for r in RuleId
            if r.value.startswith(last)
        ]


_DISABLE_TYPE = _DisableType()


def _parse_disable(args: list[str]) -> tuple[frozenset[RuleId], tuple[str, ...]]:
    disabled: set[RuleId] = set()
    remaining: list[str] = []
    for arg in args:
        if arg.startswith("--disable="):
            disabled |= _DISABLE_TYPE.convert(arg[len("--disable=") :], None, None)
        else:
            remaining.append(arg)
    return frozenset(disabled), tuple(remaining)


def _print_and_exit(errors: tuple[Violation, ...]) -> NoReturn:
    for error in errors:
        click.echo(error, err=True)
    sys.exit(1)


def _hook(args: list[str]) -> None:
    disabled, _ = _parse_disable(args)
    hook_input = json.load(sys.stdin)
    tool_input = hook_input.get("tool_input", hook_input)
    if not isinstance(tool_input, dict):
        return
    filepath = tool_input.get("file_path", "")
    if not filepath:
        return

    config = load_config(Path(filepath).parent)
    new_string = tool_input.get("new_string")
    old_string = tool_input.get("old_string")
    only_lines = (
        _changed_lines(filepath, new_string, old_string) if new_string is not None else None
    )

    errors = check(filepath, only_lines, disabled, config)
    if errors:
        _print_and_exit(errors)


_PROG_NAME = "xorq-check-style"
_COMPLETE_VAR = "_XORQ_CHECK_STYLE_COMPLETE"

_COMPLETION_INSTALL_PATHS = {
    "bash": Path("~/.local/share/bash-completion/completions/xorq-check-style").expanduser(),
    "zsh": Path("~/.zfunc/_xorq-check-style").expanduser(),
    "fish": Path("~/.config/fish/completions/xorq-check-style.fish").expanduser(),
}


def _get_completion_source(shell: str) -> str:
    comp_cls = get_completion_class(shell)
    if comp_cls is None:
        raise click.UsageError(f"Unsupported shell: {shell}")
    comp = comp_cls(main, {}, _PROG_NAME, _COMPLETE_VAR)
    return comp.source()


def _detect_shell() -> str:
    shell_env = os.environ.get("SHELL")  # xorq-style: disable=os-environ
    shell_bin = Path(shell_env or "").name
    if shell_bin not in _COMPLETION_INSTALL_PATHS:
        raise click.UsageError(
            f"Cannot detect shell from $SHELL={shell_env!r}. "
            "Pass the shell name explicitly: bash, zsh, or fish."
        )
    return shell_bin


class _FileFallbackGroup(click.Group):
    def invoke(self, ctx: click.Context) -> object:
        if ctx._protected_args:
            cmd_name = ctx._protected_args[0]
            if self.get_command(ctx, cmd_name) is None:
                ctx.args = [*ctx._protected_args, *ctx.args]
                ctx._protected_args = []
        return super().invoke(ctx)


@click.group(
    cls=_FileFallbackGroup,
    invoke_without_command=True,
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False},
)
@click.option("--list", "list_rules", is_flag=True, help="List all available rules.")
@click.option("--hook", "hook_mode", is_flag=True, help="Run in hook mode (reads JSON from stdin).")
@click.option(
    "--disable",
    default="",
    type=_DISABLE_TYPE,
    help="Disable specific rules (comma-separated).",
)
@click.pass_context
def main(
    ctx: click.Context,
    list_rules: bool,
    hook_mode: bool,
    disable: frozenset[RuleId],
) -> None:
    """Style enforcement for xorq Python projects."""
    if ctx.invoked_subcommand is not None:
        return

    if list_rules:
        for rule_id, desc in RULES.items():
            click.echo(f"  {rule_id:24s} {desc}")
        return

    if hook_mode:
        _hook(sys.argv[1:])
        return

    files = ctx.args
    if not files:
        raise click.UsageError("Missing argument 'FILES...'.")

    config = load_config()
    all_errors = tuple(error for f in files for error in check(f, disabled=disable, config=config))
    if all_errors:
        _print_and_exit(all_errors)


@main.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]), required=False)
def completion(shell: str | None) -> None:
    """Output shell completion script.

    SHELL defaults to the value of $SHELL if not provided.

    \b
    Add to your shell config:
      bash:  eval "$(xorq-check-style completion bash)"
      zsh:   eval "$(xorq-check-style completion zsh)"
      fish:  xorq-check-style completion fish | source
    """
    if shell is None:
        shell = _detect_shell()
    click.echo(_get_completion_source(shell), nl=False)


@main.command("install-completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]), required=False)
def install_completion(shell: str | None) -> None:
    """Install shell completion script to the standard location.

    SHELL defaults to the value of $SHELL if not provided.

    \b
    Install paths:
      bash:  ~/.local/share/bash-completion/completions/xorq-check-style
      zsh:   ~/.zfunc/_xorq-check-style  (requires ~/.zfunc in fpath)
      fish:  ~/.config/fish/completions/xorq-check-style.fish
    """
    if shell is None:
        shell = _detect_shell()

    install_path = _COMPLETION_INSTALL_PATHS[shell]
    install_path.parent.mkdir(parents=True, exist_ok=True)
    install_path.write_text(_get_completion_source(shell))
    click.echo(f"Installed {shell} completion to {install_path}")
    click.echo(f"Restart your shell or run: source {install_path}")


if __name__ == "__main__":
    main()
