from __future__ import annotations

import io
import json
import textwrap
from typing import TYPE_CHECKING

import click
import pytest
from click.shell_completion import ShellComplete
from click.testing import CliRunner

from xorq_style.check import (
    _DISABLE_TYPE,
    ALL_RULES,
    Config,
    RuleChecker,
    RuleId,
    Violation,
    _changed_lines,
    _hook,
    _parse_unified_diff,
    _violation_to_dict,
    check,
    load_config,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Protocol

    class _WritePy(Protocol):
        def __call__(self, code: str, name: str = ...) -> str: ...


@pytest.fixture()
def tmp_py(tmp_path: Path) -> _WritePy:
    def _write(code: str, name: str = "mod.py") -> str:
        p = tmp_path / name
        p.write_text(textwrap.dedent(code))
        return str(p)

    return _write


def _rules(violations: tuple[Violation, ...]) -> tuple[RuleId, ...]:
    return tuple(v.rule for v in violations)


# ---- future-annotations ----


def test_future_annotations_missing(tmp_py: _WritePy) -> None:
    path = tmp_py("x = 1\n")
    assert "future-annotations" in _rules(check(path))


def test_future_annotations_present(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        x = 1
    """)
    assert "future-annotations" not in _rules(check(path))


def test_future_annotations_empty_file(tmp_py: _WritePy) -> None:
    path = tmp_py("")
    assert "future-annotations" not in _rules(check(path))


def test_future_annotations_whitespace_only(tmp_py: _WritePy) -> None:
    path = tmp_py("   \n\n  ")
    assert "future-annotations" not in _rules(check(path))


# ---- relative-import ----


def test_relative_import_flagged(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from .foo import bar
    """)
    assert "relative-import" in _rules(check(path))


def test_absolute_import_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from foo import bar
    """)
    assert "relative-import" not in _rules(check(path))


# ---- test-class ----


def test_test_class_in_test_file(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        class TestFoo:
            pass
        """,
        name="test_example.py",
    )
    assert "test-class" in _rules(check(path))


def test_test_class_in_non_test_file(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        class TestFoo:
            pass
        """,
        name="example.py",
    )
    assert "test-class" not in _rules(check(path))


def test_non_test_class_in_test_file(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        class MyHelper:
            pass
        """,
        name="test_example.py",
    )
    assert "test-class" not in _rules(check(path))


def test_conftest_is_test_file(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        class TestSetup:
            pass
        """,
        name="conftest.py",
    )
    assert "test-class" in _rules(check(path))


# ---- deferred-import-test ----


def test_deferred_import_in_test(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        def test_foo():
            import json
        """,
        name="test_example.py",
    )
    assert "deferred-import-test" in _rules(check(path))


def test_deferred_import_in_test_type_checking(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from typing import TYPE_CHECKING
        def test_foo():
            if TYPE_CHECKING:
                import json
        """,
        name="test_example.py",
    )
    assert "deferred-import-test" not in _rules(check(path))


# ---- deferred-stdlib ----


def test_deferred_stdlib_in_function(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        def foo() -> None:
            import json
    """)
    assert "deferred-stdlib" in _rules(check(path))


def test_deferred_non_stdlib_in_function(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        def foo() -> None:
            import pandas
    """)
    assert "deferred-stdlib" not in _rules(check(path))


def test_deferred_stdlib_in_test_file(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        def test_foo():
            import json
        """,
        name="test_example.py",
    )
    assert "deferred-stdlib" in _rules(check(path))


def test_deferred_stdlib_in_type_checking(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from typing import TYPE_CHECKING
        def foo() -> None:
            if TYPE_CHECKING:
                import json
    """)
    assert "deferred-stdlib" not in _rules(check(path))


# ---- redundant-import ----


def test_redundant_import_same_module(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import pandas
        def foo() -> None:
            import pandas
    """)
    assert "redundant-import" in _rules(check(path))


def test_redundant_import_from_variant(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from pandas import DataFrame
        def foo() -> None:
            import pandas
    """)
    assert "redundant-import" in _rules(check(path))


def test_redundant_import_top_import_deferred_from(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import pandas
        def foo() -> None:
            from pandas import DataFrame
    """)
    assert "redundant-import" in _rules(check(path))


def test_redundant_import_no_overlap(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import json
        def foo() -> None:
            import pandas
    """)
    assert "redundant-import" not in _rules(check(path))


def test_redundant_import_deferred_in_type_checking(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import pandas
        from typing import TYPE_CHECKING
        def foo() -> None:
            if TYPE_CHECKING:
                import pandas
    """)
    assert "redundant-import" not in _rules(check(path))


def test_redundant_import_toplevel_in_type_checking(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            import pandas
        def foo() -> None:
            import pandas
    """)
    assert "redundant-import" not in _rules(check(path))


def test_redundant_import_submodule_not_redundant(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import xorq
        def foo() -> None:
            from xorq.catalog.zip_utils import extract_build_zip_context
    """)
    assert "redundant-import" not in _rules(check(path))


def test_redundant_import_submodule_same_path(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from xorq.catalog.zip_utils import extract_build_zip_context
        def foo() -> None:
            from xorq.catalog.zip_utils import extract_build_zip_context
    """)
    assert "redundant-import" in _rules(check(path))


def test_redundant_import_deeper_top_level(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import xorq.catalog.zip_utils
        def foo() -> None:
            import xorq
    """)
    assert "redundant-import" in _rules(check(path))


# ---- os-environ ----


def test_os_environ_flagged(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import os
        x = os.environ["FOO"]
    """)
    assert "os-environ" in _rules(check(path))


def test_os_environ_in_utils(tmp_path: Path) -> None:
    d = tmp_path / "common" / "utils"
    d.mkdir(parents=True)
    p = d / "env_utils.py"
    p.write_text("from __future__ import annotations\nimport os\nx = os.environ['FOO']\n")
    config = Config(environ_allow_paths=("common/utils/**",), project_root=tmp_path)
    assert "os-environ" not in _rules(check(str(p), config=config))


def test_os_environ_recursive_glob(tmp_path: Path) -> None:
    d = tmp_path / "src" / "a" / "common"
    d.mkdir(parents=True)
    p = d / "env_utils.py"
    p.write_text("from __future__ import annotations\nimport os\nx = os.environ['FOO']\n")
    config = Config(environ_allow_paths=("**/common/**",), project_root=tmp_path)
    assert "os-environ" not in _rules(check(str(p), config=config))


def test_os_environ_glob_no_match(tmp_path: Path) -> None:
    d = tmp_path / "src" / "core"
    d.mkdir(parents=True)
    p = d / "settings.py"
    p.write_text("from __future__ import annotations\nimport os\nx = os.environ['FOO']\n")
    config = Config(environ_allow_paths=("common/**",), project_root=tmp_path)
    assert "os-environ" in _rules(check(str(p), config=config))


# ---- os-path ----


def test_os_path_attribute(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import os
        os.path.join("a", "b")
    """)
    assert "os-path" in _rules(check(path))


def test_os_path_from_import(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from os.path import join
    """)
    assert "os-path" in _rules(check(path))


def test_os_path_import_statement(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import os.path
    """)
    assert "os-path" in _rules(check(path))


# ---- dataclasses ----


def test_dataclasses_from_import(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from dataclasses import dataclass
    """)
    assert "dataclasses" in _rules(check(path))


def test_dataclasses_import_module(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import dataclasses
    """)
    assert "dataclasses" in _rules(check(path))


# ---- cache-method ----


def test_cache_on_method(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import functools
        class Foo:
            @functools.cache
            def bar(self) -> None:
                pass
    """)
    assert "cache-method" in _rules(check(path))


def test_cache_on_free_function(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import functools
        @functools.cache
        def bar() -> None:
            pass
    """)
    assert "cache-method" not in _rules(check(path))


def test_bare_cache_on_method(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from functools import cache
        class Foo:
            @cache
            def bar(self) -> None:
                pass
    """)
    assert "cache-method" in _rules(check(path))


def test_cache_on_async_method(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import functools
        class Foo:
            @functools.cache
            async def bar(self) -> None:
                pass
    """)
    assert "cache-method" in _rules(check(path))


def test_lru_cache_on_method(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import functools
        class Foo:
            @functools.lru_cache
            def bar(self) -> None:
                pass
    """)
    assert "cache-method" in _rules(check(path))


def test_lru_cache_called_on_method(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import functools
        class Foo:
            @functools.lru_cache(maxsize=128)
            def bar(self) -> None:
                pass
    """)
    assert "cache-method" in _rules(check(path))


def test_bare_lru_cache_on_method(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from functools import lru_cache
        class Foo:
            @lru_cache
            def bar(self) -> None:
                pass
    """)
    assert "cache-method" in _rules(check(path))


def test_bare_lru_cache_called_on_method(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from functools import lru_cache
        class Foo:
            @lru_cache(maxsize=None)
            def bar(self) -> None:
                pass
    """)
    assert "cache-method" in _rules(check(path))


def test_lru_cache_on_free_function(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import functools
        @functools.lru_cache(maxsize=128)
        def bar() -> None:
            pass
    """)
    assert "cache-method" not in _rules(check(path))


# ---- exception-hierarchy ----


def test_exception_inherits_stdlib(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyError(ValueError):
            pass
    """)
    assert "exception-hierarchy" in _rules(check(path))


def test_exception_inherits_xorq_error(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyError(XorqError):
            pass
    """)
    assert "exception-hierarchy" not in _rules(check(path))


def test_exception_in_exceptions_module(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        class MyError(ValueError):
            pass
        """,
        name="exceptions.py",
    )
    assert "exception-hierarchy" not in _rules(check(path))


def test_exception_mixed_bases(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyError(ValueError, XorqError):
            pass
    """)
    assert "exception-hierarchy" not in _rules(check(path))


def test_exception_dotted_xorq_error(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyError(xorq.common.XorqError):
            pass
    """)
    assert "exception-hierarchy" not in _rules(check(path))


def test_exception_dotted_xorq_error_mixed(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyError(ValueError, xorq.common.XorqError):
            pass
    """)
    assert "exception-hierarchy" not in _rules(check(path))


def test_exception_dotted_base(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyError(some_module.SomeBase):
            pass
    """)
    assert "exception-hierarchy" not in _rules(check(path))


def test_exception_dotted_base_stdlib_name(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyError(some_module.ValueError):
            pass
    """)
    assert "exception-hierarchy" not in _rules(check(path))


# ---- print ----


def test_print_in_library(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        print("hello")
    """)
    assert "print" in _rules(check(path))


def test_print_in_test(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        print("hello")
        """,
        name="test_example.py",
    )
    assert "print" not in _rules(check(path))


def test_print_in_cli(tmp_py: _WritePy) -> None:
    config = Config(print_allow_files=frozenset({"cli.py"}))
    path = tmp_py(
        """\
        from __future__ import annotations
        print("hello")
        """,
        name="cli.py",
    )
    assert "print" not in _rules(check(path, config=config))


def _write_nested(tmp_path: Path, rel: str) -> str:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("from __future__ import annotations\nprint('hello')\n")
    return str(p)


def test_print_allow_bare_name_matches_anywhere(tmp_path: Path) -> None:
    config = Config(print_allow_files=frozenset({"cli.py"}), project_root=tmp_path)
    path = _write_nested(tmp_path, "src/pkg/cli.py")
    assert "print" not in _rules(check(path, config=config))


def test_print_allow_relative_path(tmp_path: Path) -> None:
    config = Config(print_allow_files=frozenset({"src/pkg/cli.py"}), project_root=tmp_path)
    path = _write_nested(tmp_path, "src/pkg/cli.py")
    assert "print" not in _rules(check(path, config=config))


def test_print_allow_relative_path_no_match(tmp_path: Path) -> None:
    config = Config(print_allow_files=frozenset({"src/other/cli.py"}), project_root=tmp_path)
    path = _write_nested(tmp_path, "src/pkg/cli.py")
    assert "print" in _rules(check(path, config=config))


def test_print_allow_recursive_glob(tmp_path: Path) -> None:
    config = Config(print_allow_files=frozenset({"src/**/cli.py"}), project_root=tmp_path)
    path = _write_nested(tmp_path, "src/a/b/cli.py")
    assert "print" not in _rules(check(path, config=config))


def test_print_allow_segment_glob(tmp_path: Path) -> None:
    config = Config(print_allow_files=frozenset({"src/*/scripts.py"}), project_root=tmp_path)
    deep = _write_nested(tmp_path, "src/a/b/scripts.py")
    shallow = _write_nested(tmp_path, "src/a/scripts.py")
    assert "print" in _rules(check(deep, config=config))
    assert "print" not in _rules(check(shallow, config=config))


# ---- type-annotations ----


def test_type_annotations_missing_return(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        def foo(x: int):
            pass
    """)
    vs = [v for v in check(path) if v.rule == "type-annotations"]
    assert len(vs) == 1
    assert "return" in vs[0].msg


def test_type_annotations_missing_param(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        def foo(x) -> None:
            pass
    """)
    vs = [v for v in check(path) if v.rule == "type-annotations"]
    assert len(vs) == 1
    assert "x" in vs[0].msg


def test_type_annotations_fully_annotated(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        def foo(x: int) -> None:
            pass
    """)
    assert "type-annotations" not in _rules(check(path))


def test_type_annotations_self_cls_skipped(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class Foo:
            def bar(self, x: int) -> None:
                pass
            @classmethod
            def baz(cls, x: int) -> None:
                pass
    """)
    assert "type-annotations" not in _rules(check(path))


def test_type_annotations_nested_function_skipped(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        def foo() -> None:
            def inner(x):
                pass
    """)
    assert "type-annotations" not in _rules(check(path))


def test_type_annotations_vararg_kwarg(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        def foo(*args, **kwargs) -> None:
            pass
    """)
    vs = [v for v in check(path) if v.rule == "type-annotations"]
    assert len(vs) == 1
    assert "*args" in vs[0].msg
    assert "**kwargs" in vs[0].msg


def test_type_annotations_async_function(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        async def foo(x):
            pass
    """)
    vs = [v for v in check(path) if v.rule == "type-annotations"]
    assert len(vs) == 1
    assert "x" in vs[0].msg
    assert "return" in vs[0].msg


# ---- attrs-mutable-default ----


def test_attrs_mutable_default_list_literal(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from attrs import field
        x = field(default=[])
    """)
    assert "attrs-mutable-default" in _rules(check(path))


def test_attrs_mutable_default_dict_literal(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from attrs import field
        x = field(default={})
    """)
    assert "attrs-mutable-default" in _rules(check(path))


def test_attrs_mutable_default_list_call(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from attrs import field
        x = field(default=list())
    """)
    assert "attrs-mutable-default" in _rules(check(path))


def test_attrs_mutable_default_dict_call(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from attrs import field
        x = field(default=dict())
    """)
    assert "attrs-mutable-default" in _rules(check(path))


def test_attrs_mutable_default_set_call(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from attrs import field
        x = field(default=set())
    """)
    assert "attrs-mutable-default" in _rules(check(path))


def test_attrs_mutable_default_set_literal(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from attrs import field
        x = field(default={1, 2})
    """)
    assert "attrs-mutable-default" in _rules(check(path))


def test_attrs_factory_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from attrs import field
        x = field(factory=list)
    """)
    assert "attrs-mutable-default" not in _rules(check(path))


def test_attrs_immutable_default_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from attrs import field
        x = field(default=None)
    """)
    assert "attrs-mutable-default" not in _rules(check(path))


def test_attrs_attrib_mutable_default(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import attr
        x = attr.attrib(default=[])
    """)
    assert "attrs-mutable-default" in _rules(check(path))


def test_attrs_ib_mutable_default(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import attr
        x = attr.ib(default={})
    """)
    assert "attrs-mutable-default" in _rules(check(path))


# ---- protected-access ----


def test_protected_access_on_object(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        obj._internal
    """)
    assert "protected-access" in _rules(check(path))


def test_protected_access_self_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class Foo:
            def bar(self) -> None:
                self._internal
    """)
    assert "protected-access" not in _rules(check(path))


def test_protected_access_cls_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class Foo:
            @classmethod
            def bar(cls) -> None:
                cls._internal
    """)
    assert "protected-access" not in _rules(check(path))


def test_protected_access_dunder_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        obj.__class__
    """)
    assert "protected-access" not in _rules(check(path))


def test_protected_access_in_test_file_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        obj._internal
        """,
        name="test_example.py",
    )
    assert "protected-access" not in _rules(check(path))


def test_protected_access_name_mangled(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        obj.__private
    """)
    assert "protected-access" in _rules(check(path))


def test_protected_access_chained(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        foo.bar._internal
    """)
    assert "protected-access" in _rules(check(path))


def test_protected_access_super_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class Foo:
            def bar(self) -> None:
                super()._bar()
    """)
    assert "protected-access" not in _rules(check(path))


def test_protected_access_super_with_args_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class Foo:
            def bar(self) -> None:
                super(Foo, self)._bar()
    """)
    assert "protected-access" not in _rules(check(path))


def test_protected_access_super_ref_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class Foo:
            def bar(self) -> None:
                s = super()
                s._bar()
    """)
    assert "protected-access" in _rules(check(path))


def test_protected_access_dunder_method_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class Foo:
            def __eq__(self, other: object) -> bool:
                return other._x == self._x
    """)
    assert "protected-access" not in _rules(check(path))


def test_protected_access_non_dunder_method_flagged(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class Foo:
            def compare(self, other: object) -> bool:
                return other._x == self._x
    """)
    assert "protected-access" in _rules(check(path))


# ---- pytest-param-id ----


def test_pytest_param_bare_values(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        import pytest
        @pytest.mark.parametrize("x", [1, 2, 3])
        def test_foo(x: int) -> None:
            pass
        """,
        name="test_example.py",
    )
    assert "pytest-param-id" in _rules(check(path))


def test_pytest_param_bare_tuples(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        import pytest
        @pytest.mark.parametrize("x,y", [(1, 2), (3, 4)])
        def test_foo(x: int, y: int) -> None:
            pass
        """,
        name="test_example.py",
    )
    assert "pytest-param-id" in _rules(check(path))


def test_pytest_param_with_id_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        import pytest
        @pytest.mark.parametrize("x", [pytest.param(1, id="one")])
        def test_foo(x: int) -> None:
            pass
        """,
        name="test_example.py",
    )
    assert "pytest-param-id" not in _rules(check(path))


def test_pytest_param_without_id(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        import pytest
        @pytest.mark.parametrize("x", [pytest.param(1)])
        def test_foo(x: int) -> None:
            pass
        """,
        name="test_example.py",
    )
    assert "pytest-param-id" in _rules(check(path))


def test_pytest_param_in_non_test_file_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        import pytest
        @pytest.mark.parametrize("x", [1, 2, 3])
        def test_foo(x: int) -> None:
            pass
        """,
        name="mod.py",
    )
    assert "pytest-param-id" not in _rules(check(path))


def test_pytest_param_indirect_still_flagged(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        import pytest
        @pytest.mark.parametrize("x", [1, 2], indirect=True)
        def test_foo(x: int) -> None:
            pass
        """,
        name="test_example.py",
    )
    assert "pytest-param-id" in _rules(check(path))


def test_pytest_param_mixed(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        import pytest
        @pytest.mark.parametrize("x", [pytest.param(1, id="one"), 2])
        def test_foo(x: int) -> None:
            pass
        """,
        name="test_example.py",
    )
    vs = [v for v in check(path) if v.rule == "pytest-param-id"]
    assert len(vs) == 1


# ---- pytest-mark-qualify ----


def test_pytest_mark_qualify_bare_mark(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from pytest import mark
        @mark.parametrize("x", [1])
        def test_foo(x: int) -> None:
            pass
        """,
        name="test_example.py",
    )
    assert "pytest-mark-qualify" in _rules(check(path))


def test_pytest_mark_qualify_bare_skip(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from pytest import mark
        @mark.skip(reason="wip")
        def test_foo() -> None:
            pass
        """,
        name="test_example.py",
    )
    assert "pytest-mark-qualify" in _rules(check(path))


def test_pytest_mark_qualify_qualified_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        import pytest
        @pytest.mark.parametrize("x", [pytest.param(1, id="one")])
        def test_foo(x: int) -> None:
            pass
        """,
        name="test_example.py",
    )
    assert "pytest-mark-qualify" not in _rules(check(path))


def test_pytest_mark_qualify_non_test_file_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from pytest import mark
        @mark.parametrize("x", [1])
        def test_foo(x: int) -> None:
            pass
        """,
        name="mod.py",
    )
    assert "pytest-mark-qualify" not in _rules(check(path))


def test_pytest_mark_qualify_local_var_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        class mark:
            x = 1
        mark.x
        """,
        name="test_example.py",
    )
    assert "pytest-mark-qualify" not in _rules(check(path))


# ---- stdlib-logging ----


def test_stdlib_logging_import(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import logging
    """)
    assert "stdlib-logging" in _rules(check(path))


def test_stdlib_logging_from_import(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from logging import getLogger
    """)
    assert "stdlib-logging" in _rules(check(path))


def test_stdlib_logging_getlogger_call(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import logging
        logger: logging.Logger = logging.getLogger(__name__)
    """)
    vs = [v for v in check(path) if v.rule == "stdlib-logging"]
    assert any("getLogger" in v.msg for v in vs)


def test_stdlib_logging_in_test_file_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        import logging
        """,
        name="test_example.py",
    )
    assert "stdlib-logging" not in _rules(check(path))


def test_stdlib_logging_in_type_checking_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            import logging
    """)
    assert "stdlib-logging" not in _rules(check(path))


def test_structlog_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import structlog
    """)
    assert "stdlib-logging" not in _rules(check(path))


# ---- pytest-tmp-path ----


def test_tmpdir_fixture_flagged(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        def test_foo(tmpdir) -> None:
            pass
        """,
        name="test_example.py",
    )
    vs = [v for v in check(path) if v.rule == "pytest-tmp-path"]
    assert len(vs) == 1
    assert "tmp_path" in vs[0].msg


def test_tmpdir_factory_fixture_flagged(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        def test_foo(tmpdir_factory) -> None:
            pass
        """,
        name="test_example.py",
    )
    vs = [v for v in check(path) if v.rule == "pytest-tmp-path"]
    assert len(vs) == 1
    assert "tmp_path_factory" in vs[0].msg


def test_tmp_path_fixture_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from pathlib import Path
        def test_foo(tmp_path: Path) -> None:
            pass
        """,
        name="test_example.py",
    )
    assert "pytest-tmp-path" not in _rules(check(path))


def test_py_path_import_flagged(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from py.path import local
        def test_foo(tmpdir) -> None:
            pass
        """,
        name="test_example.py",
    )
    vs = [v for v in check(path) if v.rule == "pytest-tmp-path"]
    assert len(vs) == 2


def test_async_tmpdir_fixture_flagged(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        async def test_foo(tmpdir) -> None:
            pass
        """,
        name="test_example.py",
    )
    vs = [v for v in check(path) if v.rule == "pytest-tmp-path"]
    assert len(vs) == 1
    assert "tmp_path" in vs[0].msg


def test_tmpdir_in_non_test_file_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        def test_foo(tmpdir) -> None:
            pass
        """,
        name="mod.py",
    )
    assert "pytest-tmp-path" not in _rules(check(path))


# ---- import-aliasing ----


def test_import_alias_underscore_prefix(tmp_py: _WritePy) -> None:
    path = tmp_py("import os as _os\n")
    assert "import-aliasing" in _rules(check(path))


def test_from_import_alias_underscore_prefix(tmp_py: _WritePy) -> None:
    path = tmp_py("from pathlib import Path as _Path\n")
    assert "import-aliasing" in _rules(check(path))


def test_import_alias_orig_prefix(tmp_py: _WritePy) -> None:
    path = tmp_py("import os as orig_os\n")
    assert "import-aliasing" in _rules(check(path))


def test_import_alias_original_prefix(tmp_py: _WritePy) -> None:
    path = tmp_py("import os as original_os\n")
    assert "import-aliasing" in _rules(check(path))


def test_import_alias_base_prefix(tmp_py: _WritePy) -> None:
    path = tmp_py("import os as base_os\n")
    assert "import-aliasing" in _rules(check(path))


def test_import_alias_real_prefix(tmp_py: _WritePy) -> None:
    path = tmp_py("import os as real_os\n")
    assert "import-aliasing" in _rules(check(path))


def test_import_alias_dotted_module(tmp_py: _WritePy) -> None:
    path = tmp_py("import os.path as _path\n")
    assert "import-aliasing" in _rules(check(path))


def test_import_no_alias_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("import os\n")
    assert "import-aliasing" not in _rules(check(path))


def test_import_legitimate_alias_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("import numpy as np\n")
    assert "import-aliasing" not in _rules(check(path))


def test_from_import_no_alias_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("from pathlib import Path\n")
    assert "import-aliasing" not in _rules(check(path))


# ---- strenum-compat ----


def test_strenum_compat_from_enum(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from enum import StrEnum
    """)
    assert "strenum-compat" in _rules(check(path))


def test_strenum_compat_from_strenum(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from strenum import StrEnum
    """)
    assert "strenum-compat" in _rules(check(path))


def test_strenum_compat_import_strenum(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import strenum
    """)
    assert "strenum-compat" in _rules(check(path))


def test_strenum_compat_from_compat_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from xorq.common.compat import StrEnum
    """)
    assert "strenum-compat" not in _rules(check(path))


def test_strenum_compat_in_compat_module_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from enum import StrEnum
        """,
        name="compat.py",
    )
    assert "strenum-compat" not in _rules(check(path))


def test_strenum_compat_other_enum_import_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from enum import IntEnum
    """)
    assert "strenum-compat" not in _rules(check(path))


def test_strenum_compat_custom_module(tmp_py: _WritePy) -> None:
    config = Config(strenum_compat_module="myproject.compat")
    path = tmp_py("""\
        from __future__ import annotations
        from enum import StrEnum
    """)
    vs = [v for v in check(path, config=config) if v.rule == "strenum-compat"]
    assert len(vs) == 1
    assert "myproject.compat" in vs[0].msg


# ---- enum-placement ----


def test_enum_placement_in_regular_module(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from xorq.common.compat import StrEnum
        class MyEnum(StrEnum):
            A = "a"
    """)
    assert "enum-placement" in _rules(check(path))


def test_enum_placement_in_enums_module_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from xorq.common.compat import StrEnum
        class MyEnum(StrEnum):
            A = "a"
        """,
        name="enums.py",
    )
    assert "enum-placement" not in _rules(check(path))


def test_enum_placement_in_test_file_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from xorq.common.compat import StrEnum
        class MyEnum(StrEnum):
            A = "a"
        """,
        name="test_example.py",
    )
    assert "enum-placement" not in _rules(check(path))


def test_enum_placement_int_enum(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from enum import IntEnum
        class Priority(IntEnum):
            LOW = 1
    """)
    assert "enum-placement" in _rules(check(path))


def test_enum_placement_dotted_base(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        import enum
        class MyEnum(enum.StrEnum):
            A = "a"
    """)
    assert "enum-placement" in _rules(check(path))


def test_enum_placement_no_base_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyClass:
            pass
    """)
    assert "enum-placement" not in _rules(check(path))


def test_enum_placement_non_enum_base_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyClass(SomeBase):
            pass
    """)
    assert "enum-placement" not in _rules(check(path))


# ---- exception-placement ----


def test_exception_placement_in_regular_module(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyError(ValueError):
            pass
    """)
    assert "exception-placement" in _rules(check(path))


def test_exception_placement_in_exceptions_module_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        class MyError(ValueError):
            pass
        """,
        name="exceptions.py",
    )
    assert "exception-placement" not in _rules(check(path))


def test_exception_placement_in_test_file_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        class MyError(ValueError):
            pass
        """,
        name="test_example.py",
    )
    assert "exception-placement" not in _rules(check(path))


def test_exception_placement_project_base(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyError(XorqError):
            pass
    """)
    assert "exception-placement" in _rules(check(path))


def test_exception_placement_exception_suffix(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyException(RuntimeError):
            pass
    """)
    assert "exception-placement" in _rules(check(path))


def test_exception_placement_non_exception_class_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        class MyService(BaseService):
            pass
    """)
    assert "exception-placement" not in _rules(check(path))


# ---- leaf-enum-import ----


def test_leaf_enum_import_stdlib_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from enum import IntEnum
        """,
        name="enums.py",
    )
    assert "leaf-enum-import" not in _rules(check(path))


def test_leaf_enum_import_compat_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from xorq.common.compat import StrEnum
        """,
        name="enums.py",
    )
    assert "leaf-enum-import" not in _rules(check(path))


def test_leaf_enum_import_project_import(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from xorq.catalog.constants import SOME_CONST
        """,
        name="enums.py",
    )
    vs = [v for v in check(path) if v.rule == "leaf-enum-import"]
    assert len(vs) == 1
    assert "xorq.catalog.constants" in vs[0].msg


def test_leaf_enum_import_third_party(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        import attrs
        """,
        name="enums.py",
    )
    assert "leaf-enum-import" in _rules(check(path))


def test_leaf_enum_import_non_enums_module_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from xorq.catalog.constants import SOME_CONST
    """)
    assert "leaf-enum-import" not in _rules(check(path))


def test_leaf_enum_import_type_checking_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from typing import TYPE_CHECKING
        from xorq.common.compat import StrEnum
        if TYPE_CHECKING:
            from xorq.vendor.ibis.expr.types import Table
        """,
        name="enums.py",
    )
    assert "leaf-enum-import" not in _rules(check(path))


# ---- disable ----


def test_disable_rule(tmp_py: _WritePy) -> None:
    path = tmp_py("x = 1\n")
    assert "future-annotations" in _rules(check(path))
    assert "future-annotations" not in _rules(
        check(path, disabled=frozenset({RuleId.FUTURE_ANNOTATIONS}))
    )


# ---- only_lines ----


def test_only_lines_filters(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from .foo import bar
        x = 1
    """)
    all_vs = check(path)
    assert "relative-import" in _rules(all_vs)
    filtered = check(path, only_lines=frozenset({3}))
    assert "relative-import" not in _rules(filtered)


# ---- file handling ----


def test_non_python_file(tmp_path: Path) -> None:
    p = tmp_path / "readme.txt"
    p.write_text("hello")
    assert check(str(p)) == ()


def test_missing_file(tmp_path: Path) -> None:
    assert check(str(tmp_path / "missing.py")) == ()


def test_vendor_directory(tmp_path: Path) -> None:
    d = tmp_path / "vendor"
    d.mkdir()
    p = d / "mod.py"
    p.write_text("x = 1\n")
    assert check(str(p)) == ()


def test_syntax_error_returns_empty(
    tmp_py: _WritePy,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_py("def foo(\n")
    assert check(path) == ()
    assert "cannot parse" in capsys.readouterr().err


# ---- inline suppression ----


def test_inline_suppress_single_rule(tmp_py: _WritePy) -> None:
    path = tmp_py(
        "from __future__ import annotations\n"
        "from .foo import bar  # xorq-style: disable=relative-import\n"
    )
    assert "relative-import" not in _rules(check(path))


def test_inline_suppress_multiple_rules(tmp_py: _WritePy) -> None:
    path = tmp_py(
        "from __future__ import annotations\n"
        "from .foo import bar  # xorq-style: disable=relative-import,os-path\n"
    )
    assert "relative-import" not in _rules(check(path))


def test_inline_suppress_only_affects_that_line(tmp_py: _WritePy) -> None:
    path = tmp_py(
        "from __future__ import annotations\n"
        "from .foo import bar  # xorq-style: disable=relative-import\n"
        "from .baz import qux\n"
    )
    vs = [v for v in check(path) if v.rule == "relative-import"]
    assert len(vs) == 1
    assert vs[0].line == 3


# ---- config ----


def test_load_config_from_pyproject(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[tool.xorq-style]\n"
        'disable = ["print", "dataclasses"]\n'
        "\n"
        "[tool.xorq-style.os-environ]\n"
        'allow-paths = ["config/"]\n'
        "\n"
        "[tool.xorq-style.exception-hierarchy]\n"
        'base-class = "AppError"\n'
        "\n"
        "[tool.xorq-style.print]\n"
        'allow-files = ["cli.py", "main.py"]\n'
    )
    config = load_config(tmp_path)
    assert config.disabled == frozenset({"print", "dataclasses"})
    assert config.environ_allow_paths == ("config/",)
    assert config.exception_base_class == "AppError"
    assert config.print_allow_files == frozenset({"cli.py", "main.py"})


def test_load_config_no_pyproject(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    config = load_config(empty)
    assert config == Config()


def test_load_config_no_tool_section(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'foo'\n")
    config = load_config(tmp_path)
    assert config == Config(project_root=tmp_path)


def test_config_disabled_merged_with_cli(tmp_py: _WritePy) -> None:
    config = Config(disabled=frozenset({RuleId.FUTURE_ANNOTATIONS}))
    path = tmp_py("""\
        x = 1
        from .foo import bar
    """)
    vs = check(path, disabled=frozenset({RuleId.RELATIVE_IMPORT}), config=config)
    rules = _rules(vs)
    assert "future-annotations" not in rules
    assert "relative-import" not in rules


def test_config_exception_base_class(tmp_py: _WritePy) -> None:
    config = Config(exception_base_class="AppError")
    path = tmp_py("""\
        from __future__ import annotations
        class MyError(ValueError):
            pass
    """)
    vs = [v for v in check(path, config=config) if v.rule == "exception-hierarchy"]
    assert len(vs) == 1
    assert "AppError" in vs[0].msg


def test_config_print_allow_files(tmp_py: _WritePy) -> None:
    config = Config(print_allow_files=frozenset({"cli.py", "main.py"}))
    path = tmp_py(
        """\
        from __future__ import annotations
        print("hello")
        """,
        name="main.py",
    )
    assert "print" not in _rules(check(path, config=config))


def test_config_environ_allow_paths(tmp_path: Path) -> None:
    d = tmp_path / "config"
    d.mkdir()
    p = d / "settings.py"
    p.write_text("from __future__ import annotations\nimport os\nx = os.environ['FOO']\n")
    config = Config(environ_allow_paths=("config/**",), project_root=tmp_path)
    assert "os-environ" not in _rules(check(str(p), config=config))


# ---- _changed_lines ----


def test_changed_lines_file_not_found() -> None:
    assert _changed_lines("/nonexistent/path.py", "x") is None


def test_changed_lines_empty_new_string(tmp_path: Path) -> None:
    p = tmp_path / "f.py"
    p.write_text("content\n")
    assert _changed_lines(str(p), "") is None


def test_changed_lines_single_occurrence(tmp_path: Path) -> None:
    p = tmp_path / "f.py"
    p.write_text("aaa\nbbb\nccc\n")
    result = _changed_lines(str(p), "bbb", "old")
    assert result == frozenset({2})


def test_changed_lines_multiline(tmp_path: Path) -> None:
    p = tmp_path / "f.py"
    p.write_text("aaa\nbbb\nccc\nddd\n")
    result = _changed_lines(str(p), "bbb\nccc", "old")
    assert result == frozenset({2, 3})


def test_changed_lines_fallback_all_matches(tmp_path: Path) -> None:
    p = tmp_path / "f.py"
    p.write_text("x\ny\nx\n")
    result = _changed_lines(str(p), "x")
    assert result == frozenset({1, 3})


def test_changed_lines_not_found(tmp_path: Path) -> None:
    p = tmp_path / "f.py"
    p.write_text("aaa\n")
    assert _changed_lines(str(p), "zzz") is None


def test_changed_lines_disambiguate_by_old_string(tmp_path: Path) -> None:
    p = tmp_path / "f.py"
    p.write_text("x = 1\ny = 2\nx = 1\n")
    result = _changed_lines(str(p), "x = 1", "x = 0")
    assert result == frozenset({1})


def test_changed_lines_no_old_string(tmp_path: Path) -> None:
    p = tmp_path / "f.py"
    p.write_text("aaa\nbbb\nccc\n")
    result = _changed_lines(str(p), "bbb")
    assert result == frozenset({2})


# ---- CLI / hook ----


def test_main_list() -> None:
    result = CliRunner().invoke(main, ["--list"])
    assert result.exit_code == 0
    assert "relative-import" in result.output
    assert "future-annotations" in result.output


def test_main_no_args() -> None:
    result = CliRunner().invoke(main, [])
    assert result.exit_code != 0
    assert "Usage" in result.output or "Missing" in result.output


def test_hook_no_violations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "mod.py"
    p.write_text("from __future__ import annotations\nx = 1\n")
    payload = json.dumps({"tool_input": {"file_path": str(p), "new_string": "x = 1"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    with pytest.raises(SystemExit, match="0"):
        _hook()


def test_hook_with_violations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "mod.py"
    p.write_text("x = 1\n")
    payload = json.dumps({"tool_input": {"file_path": str(p), "new_string": "x = 1"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    with pytest.raises(SystemExit, match="2"):
        _hook()


def test_hook_empty_filepath(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"tool_input": {"file_path": "", "new_string": "x"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    with pytest.raises(SystemExit, match="0"):
        _hook()


def test_hook_with_disable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "mod.py"
    p.write_text("x = 1\n")
    payload = json.dumps({"tool_input": {"file_path": str(p), "new_string": "x = 1"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    with pytest.raises(SystemExit, match="0"):
        _hook(disabled=frozenset({RuleId.FUTURE_ANNOTATIONS}))


def test_hook_bare_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback: bare payload without tool_input wrapper still works."""
    p = tmp_path / "mod.py"
    p.write_text("from __future__ import annotations\nx = 1\n")
    payload = json.dumps({"file_path": str(p), "new_string": "x = 1"})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    with pytest.raises(SystemExit, match="0"):
        _hook()


def test_hook_non_dict_tool_input(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"tool_input": "unexpected"})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    with pytest.raises(SystemExit, match="0"):
        _hook()


def test_hook_json_clean_emits_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "mod.py"
    p.write_text("from __future__ import annotations\nx = 1\n")
    payload = json.dumps({"tool_input": {"file_path": str(p), "new_string": "x = 1"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    with pytest.raises(SystemExit, match="0"):
        _hook(json_output=True)
    assert json.loads(capsys.readouterr().out) == []


def test_main_with_file(tmp_path: Path) -> None:
    p = tmp_path / "mod.py"
    p.write_text("from __future__ import annotations\nx: int = 1\n")
    result = CliRunner().invoke(main, [str(p)])
    assert result.exit_code == 0


def test_main_with_violations(tmp_path: Path) -> None:
    p = tmp_path / "mod.py"
    p.write_text("x = 1\n")
    result = CliRunner().invoke(main, [str(p)])
    assert result.exit_code != 0
    assert "future-annotations" in result.output


def test_main_disable_flag(tmp_path: Path) -> None:
    p = tmp_path / "mod.py"
    p.write_text("x = 1\n")
    result = CliRunner().invoke(main, ["--disable=future-annotations", str(p)])
    assert result.exit_code == 0


def test_shell_complete_disable() -> None:
    comp = ShellComplete(main, {}, "", "")
    ctx = click.Context(main)
    param = click.Argument(["--disable"])
    completions = _DISABLE_TYPE.shell_complete(ctx, param, "cache")
    values = [c.value for c in completions]
    assert "cache-method" in values
    del comp


def test_shell_complete_disable_comma_prefix() -> None:
    comp = ShellComplete(main, {}, "", "")
    ctx = click.Context(main)
    param = click.Argument(["--disable"])
    completions = _DISABLE_TYPE.shell_complete(ctx, param, "print,cache")
    values = [c.value for c in completions]
    assert "print,cache-method" in values
    del comp


# ---- protocol conformance ----


# ---- init-reexport ----


def test_init_reexport_all_local(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations

        def foo() -> None: ...

        class Bar: ...

        BAZ = 42

        __all__ = ["foo", "Bar", "BAZ"]
    """)
    assert "init-reexport" not in _rules(check(path))


def test_init_reexport_imported_name_in_all(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from other import Foo

        __all__ = ["Foo"]
    """)
    vs = [v for v in check(path) if v.rule == "init-reexport"]
    assert len(vs) == 1
    assert "Foo" in vs[0].msg


def test_init_reexport_init_file_exempt(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from other import Foo

        __all__ = ["Foo"]
        """,
        name="__init__.py",
    )
    assert "init-reexport" not in _rules(check(path))


def test_init_reexport_no_all(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from other import Foo
    """)
    assert "init-reexport" not in _rules(check(path))


def test_init_reexport_mixed(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from other import Foo

        def bar() -> None: ...

        __all__ = ["Foo", "bar"]
    """)
    vs = [v for v in check(path) if v.rule == "init-reexport"]
    assert len(vs) == 1
    assert "Foo" in vs[0].msg
    assert "bar" not in vs[0].msg


def test_init_reexport_assignment_counts_as_local(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from other import Foo

        Foo = Foo  # re-assignment makes it local

        __all__ = ["Foo"]
    """)
    assert "init-reexport" not in _rules(check(path))


def test_init_reexport_runtime_block_assignment_counts_as_local(
    tmp_py: _WritePy,
) -> None:
    # _locally_defined_names recurses into runtime control-flow blocks, so a
    # name rebound inside a try/except (or if/with/for/while) at module scope
    # counts as locally defined and is NOT treated as a bare re-export. This
    # pins the relaxation introduced alongside init-all.
    path = tmp_py("""\
        from __future__ import annotations
        from other import Foo, wrap

        try:
            Foo = wrap(Foo)
        except Exception:
            pass

        __all__ = ["Foo"]
    """)
    assert "init-reexport" not in _rules(check(path))


def test_init_reexport_starred_all_skipped(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from other import Foo

        __all__ = [*other.__all__, "Foo"]
    """)
    assert "init-reexport" not in _rules(check(path))


def test_init_reexport_violation_line(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from other import Foo

        __all__ = ["Foo"]
    """)
    vs = [v for v in check(path) if v.rule == "init-reexport"]
    assert len(vs) == 1
    assert vs[0].line == 4


# ---- init-all ----


def test_init_all_missing_flagged(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations

        def foo() -> None: ...

        class Bar: ...
        """,
        name="__init__.py",
    )
    assert "init-all" in _rules(check(path))


def test_init_all_complete_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations

        def foo() -> None: ...

        class Bar: ...

        BAZ = 42

        __all__ = ["foo", "Bar", "BAZ"]
        """,
        name="__init__.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_incomplete_flagged(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations

        def foo() -> None: ...

        class Bar: ...

        __all__ = ["foo"]
        """,
        name="__init__.py",
    )
    vs = [v for v in check(path) if v.rule == "init-all"]
    assert len(vs) == 1
    assert "Bar" in vs[0].msg
    # Reported at Bar's definition (line 5), not the __all__ line, so the
    # violation lands on a changed line under --diff and points at the culprit.
    assert vs[0].line == 5


def test_init_all_underscore_names_not_required(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations

        def foo() -> None: ...

        def _private() -> None: ...

        _CONST = 1

        __all__ = ["foo"]
        """,
        name="__init__.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_reexports_not_required(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from other import Imported

        def foo() -> None: ...

        __all__ = ["foo"]
        """,
        name="__init__.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_non_init_file_ignored(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations

        def foo() -> None: ...

        class Bar: ...
        """,
        name="mod.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_disable_comment_suppresses_presence(tmp_py: _WritePy) -> None:
    path = tmp_py(
        "from __future__ import annotations\n"
        "def foo() -> None: ...  # xorq-style: disable=init-all\n",
        name="__init__.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_disable_comment_suppresses_completeness(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations

        def foo() -> None: ...

        class Bar: ...  # xorq-style: disable=init-all

        __all__ = ["foo"]
        """,
        name="__init__.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_dynamic_all_not_flagged(tmp_py: _WritePy) -> None:
    # __all__ present but computed (concat / sorted) — cannot be statically
    # verified, so it must NOT be reported as "missing __all__".
    for value in ('["foo"] + ["bar"]', 'sorted(["foo", "bar"])'):
        path = tmp_py(
            "from __future__ import annotations\n"
            "def foo() -> None: ...\n"
            "def bar() -> None: ...\n"
            f"__all__ = {value}\n",
            name="__init__.py",
        )
        assert "init-all" not in _rules(check(path))


def test_init_all_tuple_unpacking_required(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations

        a, b = 1, 2

        __all__ = ["a"]
        """,
        name="__init__.py",
    )
    vs = [v for v in check(path) if v.rule == "init-all"]
    assert len(vs) == 1
    assert "`b`" in vs[0].msg


def test_init_all_nested_runtime_def_required(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations

        try:
            def foo() -> None: ...
        except Exception:
            foo = None

        __all__ = []
        """,
        name="__init__.py",
    )
    vs = [v for v in check(path) if v.rule == "init-all"]
    assert len(vs) == 1
    assert "`foo`" in vs[0].msg


def test_init_all_type_checking_names_not_required(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            class Stub: ...

        def foo() -> None: ...

        __all__ = ["foo"]
        """,
        name="__init__.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_diff_mode_flags_new_name(tmp_py: _WritePy) -> None:
    # A new public name added without touching __all__ must still be flagged
    # under --diff, because the violation lands on the new definition's line.
    path = tmp_py(
        """\
        from __future__ import annotations

        def foo() -> None: ...
        def newfunc() -> None: ...

        __all__ = ["foo"]
        """,
        name="__init__.py",
    )
    vs = [v for v in check(path, only_lines=frozenset({4})) if v.rule == "init-all"]
    assert len(vs) == 1
    assert "newfunc" in vs[0].msg
    assert vs[0].line == 4


def test_init_all_empty_file_ok(tmp_py: _WritePy) -> None:
    path = tmp_py("", name="__init__.py")
    assert "init-all" not in _rules(check(path))


def test_init_all_comment_only_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations

        # this package re-exports nothing of its own
        """,
        name="__init__.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_reexport_only_ok(tmp_py: _WritePy) -> None:
    path = tmp_py(
        """\
        from __future__ import annotations
        from other import Foo

        __all__ = ["Foo"]
        """,
        name="__init__.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_augmented_all_recognized(tmp_py: _WritePy) -> None:
    # Names added via `__all__ += [...]` are listed at runtime and must not be
    # reported as missing.
    path = tmp_py(
        """\
        from __future__ import annotations

        def foo() -> None: ...
        def bar() -> None: ...

        __all__ = ["foo"]
        __all__ += ["bar"]
        """,
        name="__init__.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_bare_annotation_not_required(tmp_py: _WritePy) -> None:
    # A bare annotation (`x: int` with no value) does not bind a runtime name,
    # so it cannot be exported and must not be required in __all__.
    path = tmp_py(
        """\
        from __future__ import annotations

        def foo() -> None: ...
        x: int

        __all__ = ["foo"]
        """,
        name="__init__.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_conditional_all_recognized(tmp_py: _WritePy) -> None:
    # __all__ assigned inside a runtime block is present, not absent — the
    # presence check must not report it as missing.
    path = tmp_py(
        """\
        from __future__ import annotations

        def foo() -> None: ...

        try:
            __all__ = ["foo"]
        except Exception:
            __all__ = []
        """,
        name="__init__.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_multi_target_all_recognized(tmp_py: _WritePy) -> None:
    # `__all__ = _alias = [...]` declares __all__ (a multi-target assignment),
    # so the presence check must not report it as missing. The aliased target
    # here is underscore-private, so completeness is satisfied too.
    path = tmp_py(
        """\
        from __future__ import annotations

        def foo() -> None: ...

        __all__ = _alias = ["foo"]
        """,
        name="__init__.py",
    )
    assert "init-all" not in _rules(check(path))


def test_init_all_type_checking_else_required(tmp_py: _WritePy) -> None:
    # The `else` of an `if TYPE_CHECKING:` runs at runtime, so a public name it
    # binds is a real export and must be required in __all__.
    path = tmp_py(
        """\
        from __future__ import annotations
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            Stub = int
        else:
            def Real() -> None: ...

        __all__ = ["Stub"]
        """,
        name="__init__.py",
    )
    vs = [v for v in check(path) if v.rule == "init-all"]
    assert len(vs) == 1
    assert "`Real`" in vs[0].msg


def test_init_all_match_block_def_required(tmp_py: _WritePy) -> None:
    # Names bound inside a module-level `match`/`case` block run at runtime and
    # must be required in __all__.
    path = tmp_py(
        """\
        from __future__ import annotations

        match 1:
            case 1:
                Pub = 2

        __all__ = []
        """,
        name="__init__.py",
    )
    vs = [v for v in check(path) if v.rule == "init-all"]
    assert len(vs) == 1
    assert "`Pub`" in vs[0].msg


# ---- unlisted-import ----


def _make_project(
    tmp_path: Path,
    target_code: str,
    consumer_code: str,
    *,
    target_name: str = "target.py",
    consumer_name: str = "consumer.py",
    src_root: str = ".",
) -> str:
    """Create a minimal project structure and return the consumer file path."""
    pyproject = tmp_path / "pyproject.toml"
    toml = f'[tool.xorq-style]\n[tool.xorq-style.unlisted-import]\nsrc-roots = ["{src_root}"]\n'
    pyproject.write_text(toml)

    base = tmp_path / src_root if src_root != "." else tmp_path
    pkg = base / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / target_name).write_text(textwrap.dedent(target_code))
    (pkg / consumer_name).write_text(textwrap.dedent(consumer_code))
    return str(pkg / consumer_name)


def test_unlisted_import_name_in_all(tmp_path: Path) -> None:
    path = _make_project(
        tmp_path,
        target_code="""\
            def foo() -> None: ...
            __all__ = ["foo"]
        """,
        consumer_code="""\
            from __future__ import annotations
            from pkg.target import foo
        """,
    )
    config = load_config(tmp_path)
    assert "unlisted-import" not in _rules(check(path, config=config))


def test_unlisted_import_name_not_in_all(tmp_path: Path) -> None:
    path = _make_project(
        tmp_path,
        target_code="""\
            def foo() -> None: ...
            def _private() -> None: ...
            __all__ = ["foo"]
        """,
        consumer_code="""\
            from __future__ import annotations
            from pkg.target import _private
        """,
    )
    config = load_config(tmp_path)
    vs = [v for v in check(path, config=config) if v.rule == "unlisted-import"]
    assert len(vs) == 1
    assert "_private" in vs[0].msg


def test_unlisted_import_no_all_defined(tmp_path: Path) -> None:
    path = _make_project(
        tmp_path,
        target_code="""\
            def foo() -> None: ...
        """,
        consumer_code="""\
            from __future__ import annotations
            from pkg.target import foo
        """,
    )
    config = load_config(tmp_path)
    assert "unlisted-import" not in _rules(check(path, config=config))


def test_unlisted_import_type_checking_exempt(tmp_path: Path) -> None:
    path = _make_project(
        tmp_path,
        target_code="""\
            class Foo: ...
            __all__ = ["Foo"]
        """,
        consumer_code="""\
            from __future__ import annotations
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                from pkg.target import Bar
        """,
    )
    config = load_config(tmp_path)
    assert "unlisted-import" not in _rules(check(path, config=config))


def test_unlisted_import_external_package(tmp_path: Path) -> None:
    path = _make_project(
        tmp_path,
        target_code="""\
            __all__ = ["Foo"]
        """,
        consumer_code="""\
            from __future__ import annotations
            from os.path import join
        """,
    )
    config = load_config(tmp_path)
    assert "unlisted-import" not in _rules(check(path, config=config))


def test_unlisted_import_starred_all_skipped(tmp_path: Path) -> None:
    path = _make_project(
        tmp_path,
        target_code="""\
            import other
            __all__ = [*other.__all__, "foo"]
        """,
        consumer_code="""\
            from __future__ import annotations
            from pkg.target import bar
        """,
    )
    config = load_config(tmp_path)
    assert "unlisted-import" not in _rules(check(path, config=config))


def test_unlisted_import_star_import_not_checked(tmp_path: Path) -> None:
    path = _make_project(
        tmp_path,
        target_code="""\
            def foo() -> None: ...
            __all__ = ["foo"]
        """,
        consumer_code="""\
            from __future__ import annotations
            from pkg.target import *
        """,
    )
    config = load_config(tmp_path)
    assert "unlisted-import" not in _rules(check(path, config=config))


def test_unlisted_import_multiple_names_partial(tmp_path: Path) -> None:
    path = _make_project(
        tmp_path,
        target_code="""\
            def foo() -> None: ...
            def bar() -> None: ...
            __all__ = ["foo"]
        """,
        consumer_code="""\
            from __future__ import annotations
            from pkg.target import foo, bar
        """,
    )
    config = load_config(tmp_path)
    vs = [v for v in check(path, config=config) if v.rule == "unlisted-import"]
    assert len(vs) == 1
    assert "bar" in vs[0].msg


def test_unlisted_import_no_project_root(tmp_py: _WritePy) -> None:
    path = tmp_py("""\
        from __future__ import annotations
        from pkg.target import foo
    """)
    assert "unlisted-import" not in _rules(check(path))


def test_unlisted_import_package_init(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.xorq-style]\n[tool.xorq-style.unlisted-import]\nsrc-roots = ["."]\n'
    )
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('__all__ = ["Foo"]\nclass Foo: ...\n')
    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (sub / "consumer.py").write_text("from __future__ import annotations\nfrom pkg import Bar\n")
    config = load_config(tmp_path)
    vs = [v for v in check(str(sub / "consumer.py"), config=config) if v.rule == "unlisted-import"]
    assert len(vs) == 1
    assert "Bar" in vs[0].msg


def test_unlisted_import_src_root_config(tmp_path: Path) -> None:
    path = _make_project(
        tmp_path,
        target_code="""\
            def foo() -> None: ...
            __all__ = ["foo"]
        """,
        consumer_code="""\
            from __future__ import annotations
            from pkg.target import bar
        """,
        src_root="src",
    )
    config = load_config(tmp_path)
    vs = [v for v in check(path, config=config) if v.rule == "unlisted-import"]
    assert len(vs) == 1
    assert "bar" in vs[0].msg


# ---- load_config (new fields) ----


def test_load_config_src_roots_custom(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.xorq-style]\n[tool.xorq-style.unlisted-import]\nsrc-roots = ["python/"]\n'
    )
    config = load_config(tmp_path)
    assert config.src_roots == ("python/",)
    assert config.project_root == tmp_path


def test_load_config_src_roots_default(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.xorq-style]\n")
    config = load_config(tmp_path)
    assert config.src_roots == ("src", ".")
    assert config.project_root == tmp_path


def test_all_rules_satisfy_protocol() -> None:
    for rule in ALL_RULES:
        assert isinstance(rule, RuleChecker)


def test_all_rule_ids_covered() -> None:
    implemented = frozenset(r.rule for r in ALL_RULES)
    assert implemented == frozenset(RuleId)


# ---- _parse_unified_diff ----


def test_parse_diff_single_file_added_lines() -> None:
    diff = textwrap.dedent("""\
        diff --git a/foo.py b/foo.py
        --- a/foo.py
        +++ b/foo.py
        @@ -1,3 +1,4 @@
         line1
        +added
         line2
         line3
    """)
    result = _parse_unified_diff(diff)
    assert result == {"foo.py": frozenset({2})}


def test_parse_diff_multiple_files() -> None:
    diff = textwrap.dedent("""\
        diff --git a/a.py b/a.py
        --- a/a.py
        +++ b/a.py
        @@ -1,2 +1,3 @@
         x
        +y
         z
        diff --git a/b.py b/b.py
        --- a/b.py
        +++ b/b.py
        @@ -1,2 +1,3 @@
         a
         b
        +c
    """)
    result = _parse_unified_diff(diff)
    assert "a.py" in result
    assert "b.py" in result
    assert result["a.py"] == frozenset({2})
    assert result["b.py"] == frozenset({3})


def test_parse_diff_deleted_file_skipped() -> None:
    diff = textwrap.dedent("""\
        diff --git a/gone.py b/gone.py
        --- a/gone.py
        +++ /dev/null
        @@ -1,2 +0,0 @@
        -old1
        -old2
    """)
    result = _parse_unified_diff(diff)
    assert result == {}


def test_parse_diff_new_file() -> None:
    diff = textwrap.dedent("""\
        diff --git a/new.py b/new.py
        --- /dev/null
        +++ b/new.py
        @@ -0,0 +1,3 @@
        +line1
        +line2
        +line3
    """)
    result = _parse_unified_diff(diff)
    assert result == {"new.py": frozenset({1, 2, 3})}


def test_parse_diff_context_lines_not_included() -> None:
    diff = textwrap.dedent("""\
        diff --git a/f.py b/f.py
        --- a/f.py
        +++ b/f.py
        @@ -1,5 +1,5 @@
         ctx1
         ctx2
        -old
        +new
         ctx4
         ctx5
    """)
    result = _parse_unified_diff(diff)
    assert result == {"f.py": frozenset({3})}


def test_parse_diff_multiple_hunks() -> None:
    diff = textwrap.dedent("""\
        diff --git a/f.py b/f.py
        --- a/f.py
        +++ b/f.py
        @@ -1,3 +1,4 @@
         a
        +b
         c
         d
        @@ -10,3 +11,4 @@
         x
        +y
         z
         w
    """)
    result = _parse_unified_diff(diff)
    assert result == {"f.py": frozenset({2, 12})}


def test_parse_diff_empty_input() -> None:
    assert _parse_unified_diff("") == {}
    assert _parse_unified_diff("   \n  \n") == {}


def test_parse_diff_no_prefix() -> None:
    diff = textwrap.dedent("""\
        diff --git a/foo.py b/foo.py
        --- foo.py
        +++ foo.py
        @@ -1,2 +1,3 @@
         a
        +b
         c
    """)
    result = _parse_unified_diff(diff)
    assert result == {"foo.py": frozenset({2})}


def test_parse_diff_only_removed_lines() -> None:
    diff = textwrap.dedent("""\
        diff --git a/f.py b/f.py
        --- a/f.py
        +++ b/f.py
        @@ -1,4 +1,2 @@
         keep
        -gone1
        -gone2
         keep2
    """)
    result = _parse_unified_diff(diff)
    assert result == {}


def test_parse_diff_removed_line_starting_with_triple_dash() -> None:
    diff = textwrap.dedent("""\
        diff --git a/f.py b/f.py
        --- a/f.py
        +++ b/f.py
        @@ -1,4 +1,4 @@
         keep
        ---- old separator
        +--- new separator
         keep2
         keep3
    """)
    result = _parse_unified_diff(diff)
    assert result == {"f.py": frozenset({2})}


# ---- _violation_to_dict ----


def test_violation_to_dict() -> None:
    v = Violation(filepath="a.py", line=10, rule=RuleId.PRINT, msg="no print")
    d = _violation_to_dict(v)
    assert d == {"filepath": "a.py", "line": 10, "rule": "print", "message": "no print"}


# ---- --diff CLI ----


def test_main_diff_reads_stdin(tmp_py: _WritePy) -> None:
    filepath = tmp_py("import os.path\n")
    diff = (
        f"diff --git a/{filepath} b/{filepath}\n"
        f"--- a/{filepath}\n"
        f"+++ b/{filepath}\n"
        "@@ -0,0 +1 @@\n"
        "+import os.path\n"
    )
    runner = CliRunner()
    result = runner.invoke(main, ["--diff"], input=diff)
    assert result.exit_code == 2
    assert "os-path" in result.output


def test_main_diff_no_violations(tmp_py: _WritePy) -> None:
    filepath = tmp_py("from __future__ import annotations\nx = 1\n")
    diff = (
        f"diff --git a/{filepath} b/{filepath}\n"
        f"--- a/{filepath}\n"
        f"+++ b/{filepath}\n"
        "@@ -1,1 +1,2 @@\n"
        " from __future__ import annotations\n"
        "+x = 1\n"
    )
    runner = CliRunner()
    result = runner.invoke(main, ["--diff"], input=diff)
    assert result.exit_code == 0


def test_main_diff_empty_stdin() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--diff"], input="")
    assert result.exit_code == 0


def test_main_diff_and_hook_exclusive() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--diff", "--hook"], input="")
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_main_diff_all_nonexistent_files_errors() -> None:
    diff = textwrap.dedent("""\
        diff --git a/nonexistent.py b/nonexistent.py
        --- /dev/null
        +++ b/nonexistent.py
        @@ -0,0 +1 @@
        +import os.path
    """)
    runner = CliRunner()
    result = runner.invoke(main, ["--diff"], input=diff)
    assert result.exit_code != 0
    assert "none of the" in result.output


def test_main_diff_and_files_exclusive() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--diff", "src/foo.py"], input="")
    assert result.exit_code != 0
    assert "not allowed" in result.output


# ---- --json CLI ----


def test_main_json_with_violations(tmp_py: _WritePy) -> None:
    filepath = tmp_py("from __future__ import annotations\nimport os.path\n")
    runner = CliRunner()
    result = runner.invoke(main, ["--json", filepath])
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) >= 1
    rules = {d["rule"] for d in data}
    assert "os-path" in rules
    assert data[0]["filepath"] == filepath


def test_main_json_no_violations(tmp_py: _WritePy) -> None:
    filepath = tmp_py("from __future__ import annotations\nx = 1\n")
    runner = CliRunner()
    result = runner.invoke(main, ["--json", filepath])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == []


def test_main_json_structure(tmp_py: _WritePy) -> None:
    filepath = tmp_py("import os.path\n")
    runner = CliRunner()
    result = runner.invoke(main, ["--json", filepath])
    data = json.loads(result.output)
    for entry in data:
        assert set(entry.keys()) == {"filepath", "line", "rule", "message"}
        assert isinstance(entry["line"], int)
        assert isinstance(entry["rule"], str)


def test_main_json_with_diff(tmp_py: _WritePy) -> None:
    filepath = tmp_py("from __future__ import annotations\nimport os.path\n")
    diff = (
        f"diff --git a/{filepath} b/{filepath}\n"
        f"--- a/{filepath}\n"
        f"+++ b/{filepath}\n"
        "@@ -1,1 +1,2 @@\n"
        " from __future__ import annotations\n"
        "+import os.path\n"
    )
    runner = CliRunner()
    result = runner.invoke(main, ["--diff", "--json"], input=diff)
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert len(data) >= 1
    rules = {d["rule"] for d in data}
    assert "os-path" in rules


def test_main_json_empty_diff() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--diff", "--json"], input="")
    assert result.exit_code == 0
    assert json.loads(result.output) == []


def test_main_json_list_rules() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) > 0
    for entry in data:
        assert set(entry.keys()) == {"rule", "description"}
        assert isinstance(entry["rule"], str)
        assert isinstance(entry["description"], str)
