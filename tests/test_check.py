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
    _parse_disable,
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
    config = Config(environ_allow_paths=("common/utils",))
    assert "os-environ" not in _rules(check(str(p), config=config))


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
    assert config == Config()


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
    config = Config(environ_allow_paths=("config/",))
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


def test_parse_disable_invalid_rule() -> None:
    with pytest.raises(click.exceptions.BadParameter, match="unknown rule"):
        _parse_disable(["--disable=nonexistent-rule"])


def test_hook_no_violations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "mod.py"
    p.write_text("from __future__ import annotations\nx = 1\n")
    payload = json.dumps({"tool_input": {"file_path": str(p), "new_string": "x = 1"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    _hook([])


def test_hook_with_violations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "mod.py"
    p.write_text("x = 1\n")
    payload = json.dumps({"tool_input": {"file_path": str(p), "new_string": "x = 1"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    with pytest.raises(SystemExit):
        _hook([])


def test_hook_empty_filepath(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"tool_input": {"file_path": "", "new_string": "x"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    _hook([])


def test_hook_with_disable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "mod.py"
    p.write_text("x = 1\n")
    payload = json.dumps({"tool_input": {"file_path": str(p), "new_string": "x = 1"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    _hook(["--hook", "--disable=future-annotations"])


def test_hook_bare_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback: bare payload without tool_input wrapper still works."""
    p = tmp_path / "mod.py"
    p.write_text("from __future__ import annotations\nx = 1\n")
    payload = json.dumps({"file_path": str(p), "new_string": "x = 1"})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    _hook([])


def test_hook_non_dict_tool_input(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"tool_input": "unexpected"})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    _hook([])


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


def test_all_rules_satisfy_protocol() -> None:
    for rule in ALL_RULES:
        assert isinstance(rule, RuleChecker)


def test_all_rule_ids_covered() -> None:
    implemented = frozenset(r.rule for r in ALL_RULES)
    assert implemented == frozenset(RuleId)
