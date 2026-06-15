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
import re
import sys
import types
from dataclasses import dataclass  # xorq-style: disable=dataclasses
from functools import cache
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import click
from click.shell_completion import get_completion_class

from xorq_style.enums import RuleId

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = [
    "RULES",
    "Config",
    "Violation",
    "check",
    "load_config",
    "main",
]


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
        RuleId.ATTRS_MUTABLE_DEFAULT: "No mutable defaults in attrs fields (use factory=)",
        RuleId.PROTECTED_ACCESS: "No protected member access on third-party objects",
        RuleId.PYTEST_PARAM_ID: "Parametrize args must use pytest.param with id=",
        RuleId.PYTEST_MARK_QUALIFY: "Use pytest.mark.X, not bare mark.X",
        RuleId.STDLIB_LOGGING: "No stdlib logging (use structlog)",
        RuleId.PYTEST_TMP_PATH: "No legacy tmpdir fixture (use tmp_path)",
        RuleId.IMPORT_ALIASING: "No suspicious import aliasing (e.g. import x as _x)",
        RuleId.STRENUM_COMPAT: "No direct StrEnum import (use compat shim)",
        RuleId.ENUM_PLACEMENT: "Enum classes must be defined in enums.py",
        RuleId.EXCEPTION_PLACEMENT: "Exception classes must be defined in exceptions.py",
        RuleId.LEAF_ENUM_IMPORT: "enums.py modules must only import from stdlib and compat",
        RuleId.UNLISTED_IMPORT: "Imported name not listed in target module's __all__",
        RuleId.INIT_REEXPORT: "Non-__init__ module re-exports imported name via __all__",
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
    strenum_compat_module: str = "xorq.common.compat"
    project_root: Path | None = None
    src_roots: tuple[str, ...] = ("src", ".")


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
            toplevel_modules.update(_full_module_paths(node))

        for node, parents in ctx.walked:
            if (
                not isinstance(node, ast.Import | ast.ImportFrom)
                or not _in_function(parents)
                or _in_type_checking(parents)
            ):
                continue
            for m in _full_module_paths(node):
                if any(t == m or t.startswith(m + ".") for t in toplevel_modules):
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


_MUTABLE_CALL_NAMES = frozenset({"list", "dict", "set"})

_ATTRS_FIELD_FUNCS = frozenset({"field", "attrib", "ib"})


def _is_mutable_default(node: ast.expr) -> bool:
    """Return True if *node* is a mutable literal or no-arg mutable constructor."""
    if isinstance(node, ast.List | ast.Dict | ast.Set):
        return True
    if isinstance(node, ast.Call) and not node.args and not node.keywords:
        match node.func:
            case ast.Name(id=name) if name in _MUTABLE_CALL_NAMES:
                return True
    return False


def _is_attrs_field_call(node: ast.Call) -> bool:
    """Return True if *node* looks like ``field(...)``, ``attr.ib(...)``, etc."""
    match node.func:
        case ast.Name(id=name) if name in _ATTRS_FIELD_FUNCS:
            return True
        case ast.Attribute(attr=attr, value=ast.Name(id="attr" | "attrs")) if (
            attr in _ATTRS_FIELD_FUNCS
        ):
            return True
    return False


class AttrsMutableDefaultRule:
    rule = RuleId.ATTRS_MUTABLE_DEFAULT

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule):
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            if not isinstance(node, ast.Call) or not _is_attrs_field_call(node):
                continue
            for kw in node.keywords:
                if kw.arg == "default" and _is_mutable_default(kw.value):
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        "mutable default in attrs field (use factory= instead)",
                    )


class ProtectedAccessRule:
    rule = RuleId.PROTECTED_ACCESS

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or ctx.is_test:
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, parents in ctx.walked:
            match node:
                case ast.Attribute(attr=attr, value=value) if attr.startswith("_") and not (
                    attr.startswith("__") and attr.endswith("__")
                ):
                    if isinstance(value, ast.Name) and value.id in ("self", "cls"):
                        continue
                    if self._is_super_call(value):
                        continue
                    if self._in_class_dunder(parents):
                        continue
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        f"protected member access `.{attr}` on external object",
                    )

    @staticmethod
    def _is_super_call(node: ast.expr) -> bool:
        match node:
            case ast.Call(func=ast.Name(id="super")):
                return True
        return False

    @staticmethod
    def _in_class_dunder(parents: tuple[ast.AST, ...]) -> bool:
        for i, p in enumerate(parents):
            if (
                isinstance(p, ast.FunctionDef | ast.AsyncFunctionDef)
                and p.name.startswith("__")
                and p.name.endswith("__")
                and i > 0
                and isinstance(parents[i - 1], ast.ClassDef)
            ):
                return True
        return False


class PytestParamIdRule:
    rule = RuleId.PYTEST_PARAM_ID

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or not ctx.is_test:
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            if not isinstance(node, ast.Call):
                continue
            match node.func:
                case ast.Attribute(
                    attr="parametrize",
                    value=ast.Attribute(attr="mark", value=ast.Name(id="pytest")),
                ):
                    pass
                case _:
                    continue
            if len(node.args) < 2:
                continue
            arg_list = node.args[1]
            if not isinstance(arg_list, ast.List | ast.Tuple):
                continue
            for elt in arg_list.elts:
                if not self._is_pytest_param(elt):
                    yield ctx.violation(
                        elt.lineno,
                        self.rule,
                        "parametrize arg should use pytest.param(..., id=...)",
                    )
                elif isinstance(elt, ast.Call) and not self._has_id_keyword(elt):
                    yield ctx.violation(
                        elt.lineno,
                        self.rule,
                        "pytest.param() missing id= keyword",
                    )

    @staticmethod
    def _is_pytest_param(node: ast.AST) -> bool:
        match node:
            case ast.Call(func=ast.Attribute(attr="param", value=ast.Name(id="pytest"))):
                return True
            case ast.Call(func=ast.Name(id="param")):
                return True
            case _:
                return False

    @staticmethod
    def _has_id_keyword(node: ast.Call) -> bool:
        return any(kw.arg == "id" for kw in node.keywords)


class PytestMarkQualifyRule:
    rule = RuleId.PYTEST_MARK_QUALIFY

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or not ctx.is_test:
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        has_mark_import = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "pytest"
            and any(alias.name == "mark" for alias in node.names)
            for node, _ in ctx.walked
        )
        if not has_mark_import:
            return
        for node, _parents in ctx.walked:
            match node:
                case ast.Attribute(attr=attr, value=ast.Name(id="mark")):
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        f"use `pytest.mark.{attr}` instead of `mark.{attr}`",
                    )


class StdlibLoggingRule:
    rule = RuleId.STDLIB_LOGGING

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or ctx.is_test:
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, parents in ctx.walked:
            if _in_type_checking(parents):
                continue
            match node:
                case ast.Import() | ast.ImportFrom() if "logging" in _top_modules(node):
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        "stdlib logging import (use structlog instead)",
                    )
                case ast.Call(
                    func=ast.Attribute(
                        attr="getLogger",
                        value=ast.Name(id="logging"),
                    )
                ):
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        "logging.getLogger() call (use structlog instead)",
                    )


_LEGACY_TMPDIR_FIXTURES = {"tmpdir": "tmp_path", "tmpdir_factory": "tmp_path_factory"}


class PytestTmpPathRule:
    rule = RuleId.PYTEST_TMP_PATH

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or not ctx.is_test:
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            match node:
                case (
                    ast.FunctionDef(args=ast.arguments(args=args))
                    | ast.AsyncFunctionDef(args=ast.arguments(args=args))
                ):
                    for arg in args:
                        if (replacement := _LEGACY_TMPDIR_FIXTURES.get(arg.arg)) is not None:
                            yield ctx.violation(
                                arg.lineno,
                                self.rule,
                                f"use `{replacement}` fixture instead of legacy `{arg.arg}`",
                            )
                case ast.ImportFrom(module=module) if module and module.startswith("py.path"):
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        "py.path import (use pathlib.Path via tmp_path fixture)",
                    )


class ImportAliasingRule:
    rule = RuleId.IMPORT_ALIASING

    _PREFIXES = ("_", "orig_", "_orig_", "original_", "base_", "real_")

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule):
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            match node:
                case ast.Import(names=names) | ast.ImportFrom(names=names):
                    for alias in names:
                        if alias.asname is None:
                            continue
                        base = alias.name.rsplit(".", 1)[-1]
                        if any(alias.asname == f"{prefix}{base}" for prefix in self._PREFIXES):
                            yield ctx.violation(
                                node.lineno,
                                self.rule,
                                f"suspicious import alias `{alias.name} as {alias.asname}`",
                            )


class StrEnumCompatRule:
    rule = RuleId.STRENUM_COMPAT

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or ctx.path.name == "compat.py":
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        compat_module = ctx.config.strenum_compat_module
        for node, _parents in ctx.walked:
            match node:
                case ast.ImportFrom(module="enum" | "strenum", names=names):
                    if any(alias.name == "StrEnum" for alias in names):
                        yield ctx.violation(
                            node.lineno,
                            self.rule,
                            f"direct StrEnum import (use `from {compat_module} import StrEnum`)",
                        )
                case ast.Import(names=names):
                    for alias in names:
                        if alias.name == "strenum":
                            yield ctx.violation(
                                node.lineno,
                                self.rule,
                                "direct strenum import"
                                f" (use `from {compat_module} import StrEnum`)",
                            )


_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})


def _has_enum_base(bases: list[ast.expr]) -> bool:
    for base in bases:
        match base:
            case ast.Name(id=name) if name in _ENUM_BASES:
                return True
            case ast.Attribute(attr=attr) if attr in _ENUM_BASES:
                return True
    return False


class EnumPlacementRule:
    rule = RuleId.ENUM_PLACEMENT

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or _is_enums_module(ctx.path) or ctx.is_test:
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            match node:
                case ast.ClassDef(name=name, bases=bases) if bases and _has_enum_base(bases):
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        f"enum class `{name}` should be defined in an enums.py module",
                    )


class ExceptionPlacementRule:
    rule = RuleId.EXCEPTION_PLACEMENT

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or _is_exceptions_module(ctx.path) or ctx.is_test:
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        for node, _parents in ctx.walked:
            match node:
                case ast.ClassDef(name=name, bases=bases) if bases and (
                    name.endswith("Error") or name.endswith("Exception")
                ):
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        f"exception class `{name}` should be defined in an exceptions.py module",
                    )


class LeafEnumImportRule:
    rule = RuleId.LEAF_ENUM_IMPORT

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule) or not _is_enums_module(ctx.path):
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        compat_module = ctx.config.strenum_compat_module
        for node, parents in ctx.walked:
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            if _in_type_checking(parents):
                continue
            top_mods = _top_modules(node)
            if all(m == "__future__" or m in STDLIB for m in top_mods):
                continue
            if any(fp == compat_module for fp in _full_module_paths(node)):
                continue
            display = ", ".join(_full_module_paths(node)) or "?"
            yield ctx.violation(
                node.lineno,
                self.rule,
                f"enums.py must be a leaf module (`{display}` is not stdlib or compat)",
            )


class UnlistedImportRule:
    rule = RuleId.UNLISTED_IMPORT

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule):
            return ()
        if ctx.config.project_root is None:
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        project_root = ctx.config.project_root
        assert project_root is not None
        src_roots = ctx.config.src_roots
        if not _is_within_package(ctx.path, project_root, src_roots):
            return
        for node, parents in ctx.walked:
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if _in_type_checking(parents):
                continue
            target = _resolve_module(node.module, project_root, src_roots)
            if target is None:
                continue
            dunder_all = _extract_dunder_all(target)
            if dunder_all is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name not in dunder_all:
                    yield ctx.violation(
                        node.lineno,
                        self.rule,
                        f"`{alias.name}` is not listed in `{node.module}.__all__`",
                    )


class InitReexportRule:
    rule = RuleId.INIT_REEXPORT

    def check(self, ctx: CheckContext) -> tuple[Violation, ...]:
        if not ctx.enabled(self.rule):
            return ()
        if ctx.path.name == "__init__.py":
            return ()
        return tuple(self._check(ctx))

    def _check(self, ctx: CheckContext) -> Iterator[Violation]:
        dunder_all = _extract_dunder_all(ctx.path)
        if dunder_all is None:
            return
        local_names = _locally_defined_names(ctx.tree)
        all_line = self._find_all_line(ctx.tree)
        for name in sorted(dunder_all):
            if name not in local_names:
                yield ctx.violation(
                    all_line,
                    self.rule,
                    f"`{name}` in __all__ is not locally defined (only __init__.py may re-export)",
                )

    @staticmethod
    def _find_all_line(tree: ast.Module) -> int:
        for node in ast.iter_child_nodes(tree):
            match node:
                case ast.Assign(targets=[ast.Name(id="__all__")]):
                    return node.lineno
                case ast.AnnAssign(target=ast.Name(id="__all__")):
                    return node.lineno
        return 1


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
    AttrsMutableDefaultRule(),
    ProtectedAccessRule(),
    PytestParamIdRule(),
    PytestMarkQualifyRule(),
    StdlibLoggingRule(),
    PytestTmpPathRule(),
    ImportAliasingRule(),
    StrEnumCompatRule(),
    EnumPlacementRule(),
    ExceptionPlacementRule(),
    LeafEnumImportRule(),
    UnlistedImportRule(),
    InitReexportRule(),
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

    project_root = pyproject.parent

    tool = data.get("tool", {}).get("xorq-style", {})
    if not tool:
        return Config(project_root=project_root)

    disabled = frozenset(RuleId(r) for r in tool.get("disable", ()))

    environ_cfg = tool.get("os-environ", {})
    environ_allow = tuple(environ_cfg.get("allow-paths", ()))

    exception_cfg = tool.get("exception-hierarchy", {})
    base_class = exception_cfg.get("base-class", "XorqError")

    print_cfg = tool.get("print", {})
    print_allow = frozenset(print_cfg.get("allow-files", ()))

    strenum_cfg = tool.get("strenum-compat", {})
    strenum_module = strenum_cfg.get("module", "xorq.common.compat")

    unlisted_cfg = tool.get("unlisted-import", {})
    src_roots = tuple(unlisted_cfg.get("src-roots", ("src", ".")))

    return Config(
        disabled=disabled,
        environ_allow_paths=environ_allow,
        exception_base_class=base_class,
        print_allow_files=print_allow,
        strenum_compat_module=strenum_module,
        project_root=project_root,
        src_roots=src_roots,
    )


def _is_test_file(path: Path) -> bool:
    return path.name.startswith("test_") or path.name == "conftest.py"


def _is_in_class(parents: tuple[ast.AST, ...]) -> bool:
    return any(isinstance(p, ast.ClassDef) for p in parents)


def _is_exceptions_module(path: Path) -> bool:
    return path.name == "exceptions.py"


def _is_enums_module(path: Path) -> bool:
    return path.name == "enums.py"


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


def _extract_all_names(node: ast.expr) -> frozenset[str] | None:
    if not isinstance(node, ast.List | ast.Tuple):
        return None
    names: set[str] = set()
    for elt in node.elts:
        match elt:
            case ast.Constant(value=str() as name):
                names.add(name)
            case ast.Starred():
                return None
            case _:
                return None
    return frozenset(names)


@cache
def _extract_dunder_all(path: Path) -> frozenset[str] | None:
    try:
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None
    for node in ast.iter_child_nodes(tree):
        match node:
            case ast.Assign(targets=[ast.Name(id="__all__")], value=value):
                return _extract_all_names(value)
            case ast.AnnAssign(target=ast.Name(id="__all__"), value=value) if value is not None:
                return _extract_all_names(value)
    return None


@cache
def _resolve_module(
    module_path: str, project_root: Path, src_roots: tuple[str, ...]
) -> Path | None:
    parts = module_path.split(".")
    rel = Path(*parts)
    for root_str in src_roots:
        base = project_root / root_str
        candidate = base / rel.with_suffix(".py")
        if candidate.is_file():
            return candidate
        candidate = base / rel / "__init__.py"
        if candidate.is_file():
            return candidate
    return None


def _is_within_package(filepath: Path, project_root: Path, src_roots: tuple[str, ...]) -> bool:
    resolved = filepath.resolve()
    for root_str in src_roots:
        base = (project_root / root_str).resolve()
        if resolved.is_relative_to(base):
            return True
    return False


def _locally_defined_names(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        match node:
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
                names.add(name)
            case ast.ClassDef(name=name):
                names.add(name)
            case ast.Assign(targets=targets):
                for target in targets:
                    if isinstance(target, ast.Name) and target.id != "__all__":
                        names.add(target.id)
            case ast.AnnAssign(target=ast.Name(id=name)) if name != "__all__":
                names.add(name)
    return frozenset(names)


def _top_modules(node: ast.AST) -> tuple[str, ...]:
    match node:
        case ast.Import():
            return tuple(a.name.split(".")[0] for a in node.names)
        case ast.ImportFrom(module=module) if module:
            return (module.split(".")[0],)
        case _:
            return ()


def _full_module_paths(node: ast.AST) -> tuple[str, ...]:
    match node:
        case ast.Import():
            return tuple(a.name for a in node.names)
        case ast.ImportFrom(module=module) if module:
            return (module,)
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


_HUNK_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")


def _parse_unified_diff(diff_text: str) -> dict[str, frozenset[int]]:
    result: dict[str, set[int]] = {}
    current_file: str | None = None
    current_line = 0
    in_hunk = False

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff "):
            current_file = None
            in_hunk = False
        elif raw_line.startswith("+++ "):
            path = raw_line[4:].split("\t")[0]
            if path == "/dev/null":
                current_file = None
                continue
            path = path.removeprefix("b/")
            current_file = path
            in_hunk = False
            if current_file not in result:
                result[current_file] = set()
        elif not in_hunk and raw_line.startswith("--- "):
            continue
        elif m := _HUNK_RE.match(raw_line):
            current_line = int(m.group(1))
            in_hunk = True
        elif in_hunk and current_file is not None:
            if raw_line.startswith("+"):
                result[current_file].add(current_line)
                current_line += 1
            elif raw_line.startswith(("-", "\\")):
                pass
            else:
                current_line += 1

    return {f: frozenset(lines) for f, lines in result.items() if lines}


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


def _violation_to_dict(v: Violation) -> dict[str, str | int]:
    return {
        "filepath": v.filepath,
        "line": v.line,
        "rule": v.rule.value,
        "message": v.msg,
    }


def _report(errors: tuple[Violation, ...], *, json_output: bool) -> None:
    if errors:
        if json_output:
            click.echo(json.dumps([_violation_to_dict(v) for v in errors]))
        else:
            for error in errors:
                click.echo(error, err=True)
        sys.exit(2)
    elif json_output:
        click.echo("[]")


def _hook(*, disabled: frozenset[RuleId] = frozenset(), json_output: bool = False) -> None:
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
    _report(errors, json_output=json_output)


def _diff(*, disabled: frozenset[RuleId] = frozenset(), json_output: bool = False) -> None:
    diff_text = sys.stdin.read()
    if not diff_text.strip():
        _report((), json_output=json_output)
        return

    file_lines = _parse_unified_diff(diff_text)
    if not file_lines:
        _report((), json_output=json_output)
        return

    all_errors: list[Violation] = []
    for filepath, only_lines in sorted(file_lines.items()):
        if not Path(filepath).is_file():
            continue
        config = load_config(Path(filepath).parent)
        errors = check(filepath, only_lines=only_lines, disabled=disabled, config=config)
        all_errors.extend(errors)

    _report(tuple(all_errors), json_output=json_output)


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
        if ctx._protected_args:  # xorq-style: disable=protected-access
            cmd_name = ctx._protected_args[0]  # xorq-style: disable=protected-access
            if self.get_command(ctx, cmd_name) is None:
                ctx.args = [*ctx._protected_args, *ctx.args]  # xorq-style: disable=protected-access
                ctx._protected_args = []  # xorq-style: disable=protected-access
        return super().invoke(ctx)


@click.group(
    cls=_FileFallbackGroup,
    invoke_without_command=True,
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False},
)
@click.option("--list", "list_rules", is_flag=True, help="List all available rules.")
@click.option("--hook", "hook_mode", is_flag=True, help="Run in hook mode (reads JSON from stdin).")
@click.option(
    "--diff",
    "diff_mode",
    is_flag=True,
    help="Read unified diff from stdin, lint only changed lines.",
)
@click.option("--json", "json_output", is_flag=True, help="Output violations as JSON.")
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
    diff_mode: bool,
    json_output: bool,
    disable: frozenset[RuleId],
) -> None:
    """Style enforcement for xorq Python projects."""
    if ctx.invoked_subcommand is not None:
        return

    if hook_mode and diff_mode:
        raise click.UsageError("--hook and --diff are mutually exclusive.")

    if list_rules:
        if json_output:
            click.echo(json.dumps([{"rule": str(r), "description": d} for r, d in RULES.items()]))
        else:
            for rule_id, desc in RULES.items():
                click.echo(f"  {rule_id:24s} {desc}")
        return

    if hook_mode:
        _hook(disabled=disable, json_output=json_output)
        return

    if diff_mode:
        if ctx.args:
            raise click.UsageError("--diff reads from stdin; positional FILES are not allowed.")
        _diff(disabled=disable, json_output=json_output)
        return

    files = ctx.args
    if not files:
        raise click.UsageError("Missing argument 'FILES...'.")

    config = load_config()
    all_errors = tuple(error for f in files for error in check(f, disabled=disable, config=config))
    _report(all_errors, json_output=json_output)


@main.command()
def version() -> None:
    """Show the xorq-style version."""
    click.echo(f"xorq-check-style {pkg_version('xorq-style')}")


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
