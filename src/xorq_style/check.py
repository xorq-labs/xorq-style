"""
Style enforcement for Claude Code edits.

Usage:
  xorq-check-style [--disable=rule1,rule2] <file> [file ...]
  xorq-check-style --hook [--disable=rule1,rule2]   (reads tool input from stdin)
  xorq-check-style --list                            (show all rule IDs)

Deferred = inside a function/method body, outside TYPE_CHECKING blocks.
Non-stdlib deferred imports are allowed in non-test files (e.g., heavy
imports deferred inside Click commands).
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Violation:
    filepath: str
    line: int
    rule: str
    msg: str

    def __str__(self):
        return f"{self.filepath}:{self.line}: [{self.rule}] {self.msg}"


RULES = {
    "relative-import": "No relative imports (use absolute imports)",
    "test-class": "No test classes (use plain test functions)",
    "deferred-import-test": "No deferred imports in test files",
    "deferred-stdlib": "No deferred stdlib imports (anywhere)",
    "os-environ": "No os.environ outside common/utils/",
    "future-annotations": "Missing `from __future__ import annotations`",
    "os-path": "No os.path (use pathlib.Path)",
    "dataclasses": "No dataclasses (use attrs)",
    "cache-method": "No @functools.cache on methods (leaks memory via self)",
    "exception-hierarchy": "Custom exceptions must inherit from XorqError",
    "print": "No bare print() in library code (use logging/click.echo)",
}

STDLIB = sys.stdlib_module_names


def _is_test_file(path):
    return path.name.startswith("test_") or path.name == "conftest.py"


def _environ_allowed(path):
    parts = path.parts
    for i, part in enumerate(parts):
        if part == "common" and i + 1 < len(parts) and parts[i + 1] == "utils":
            return True
    return False


def _is_in_class(parents):
    return any(isinstance(p, ast.ClassDef) for p in parents)


def _is_exceptions_module(path):
    return path.name == "exceptions.py"


STDLIB_EXCEPTIONS = frozenset(
    {
        "Exception",
        "BaseException",
        "ValueError",
        "TypeError",
        "RuntimeError",
        "KeyError",
        "AttributeError",
        "IndexError",
        "OSError",
        "IOError",
        "NotImplementedError",
        "StopIteration",
        "ArithmeticError",
        "LookupError",
        "ImportError",
        "FileNotFoundError",
        "PermissionError",
        "TimeoutError",
        "ConnectionError",
    }
)


def _walk_with_parents(node, parents=()):
    yield node, parents
    new_parents = (*parents, node)
    for child in ast.iter_child_nodes(node):
        yield from _walk_with_parents(child, new_parents)


def _in_function(parents):
    return any(isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)) for p in parents)


def _in_type_checking(parents):
    for t in (p.test for p in parents if isinstance(p, ast.If)):
        if (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or (
            isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING"
        ):
            return True
    return False


def _top_modules(node):
    match node:
        case ast.Import():
            return [a.name.split(".")[0] for a in node.names]
        case ast.ImportFrom() if node.module:
            return [node.module.split(".")[0]]
        case _:
            return []


def _changed_lines(filepath, new_string, old_string=None):
    try:
        content = Path(filepath).read_text()
    except (OSError, UnicodeDecodeError):
        return None

    if old_string is not None and old_string != new_string:
        pos = 0
        while True:
            idx = content.find(new_string, pos)
            if idx < 0:
                break
            candidate = content[:idx] + old_string + content[idx + len(new_string) :]
            if candidate.count(old_string) == 1:
                start_line = content[:idx].count("\n") + 1
                end_line = start_line + new_string.count("\n")
                return set(range(start_line, end_line + 1))
            pos = idx + 1

    lines = set()
    pos = 0
    while True:
        idx = content.find(new_string, pos)
        if idx < 0:
            break
        start_line = content[:idx].count("\n") + 1
        end_line = start_line + new_string.count("\n")
        lines.update(range(start_line, end_line + 1))
        pos = idx + len(new_string)
    return lines or set()


def _enabled(rule, disabled):
    return rule not in disabled


def check(filepath, only_lines=None, disabled=frozenset()):
    path = Path(filepath)
    if path.suffix != ".py" or not path.exists():
        return []

    if "vendor" in path.parts:
        return []

    try:
        source = path.read_text()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError):
        return []

    errors = []
    is_test = _is_test_file(path)

    if _enabled("future-annotations", disabled):
        has_future_annotations = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
            and any(alias.name == "annotations" for alias in node.names)
            for node in ast.iter_child_nodes(tree)
        )
        if not has_future_annotations and source.strip():
            errors.append(
                (
                    1,
                    "future-annotations",
                    "missing `from __future__ import annotations`",
                )
            )

    for node, parents in _walk_with_parents(tree):
        if (
            _enabled("relative-import", disabled)
            and isinstance(node, ast.ImportFrom)
            and node.level
            and node.level > 0
        ):
            errors.append(
                (
                    node.lineno,
                    "relative-import",
                    "relative import (use absolute import)",
                )
            )

        if (
            _enabled("test-class", disabled)
            and is_test
            and isinstance(node, ast.ClassDef)
            and node.name.startswith("Test")
        ):
            errors.append(
                (
                    node.lineno,
                    "test-class",
                    f"test class `{node.name}` (use plain test functions)",
                )
            )

        if (
            isinstance(node, (ast.Import, ast.ImportFrom))
            and _in_function(parents)
            and not _in_type_checking(parents)
        ):
            mods = _top_modules(node)
            if is_test and _enabled("deferred-import-test", disabled):
                errors.append(
                    (
                        node.lineno,
                        "deferred-import-test",
                        f"deferred import in test: {', '.join(mods) or '?'}",
                    )
                )
            elif not is_test and _enabled("deferred-stdlib", disabled):
                for m in (m for m in mods if m in STDLIB):
                    errors.append(
                        (
                            node.lineno,
                            "deferred-stdlib",
                            f"deferred stdlib import `{m}` (move to top of file)",
                        )
                    )

        if (
            _enabled("os-environ", disabled)
            and isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and not _environ_allowed(path)
        ):
            errors.append(
                (
                    node.lineno,
                    "os-environ",
                    "os.environ (use xorq.common.utils.env_utils instead)",
                )
            )

        if (
            _enabled("os-path", disabled)
            and isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "path"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
        ):
            errors.append(
                (
                    node.lineno,
                    "os-path",
                    f"os.path.{node.attr} (use pathlib.Path instead)",
                )
            )

        if _enabled("dataclasses", disabled) and isinstance(
            node, (ast.Import, ast.ImportFrom)
        ):
            mods = _top_modules(node)
            if "dataclasses" in mods:
                errors.append(
                    (
                        node.lineno,
                        "dataclasses",
                        "dataclasses import (use attrs instead)",
                    )
                )

        if (
            _enabled("cache-method", disabled)
            and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _is_in_class(parents)
        ):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Name) and dec.id == "cache") or (
                    isinstance(dec, ast.Attribute)
                    and dec.attr == "cache"
                    and isinstance(dec.value, ast.Name)
                    and dec.value.id == "functools"
                ):
                    errors.append(
                        (
                            dec.lineno,
                            "cache-method",
                            f"@functools.cache on method `{node.name}` (leaks memory via self)",
                        )
                    )

        if (
            _enabled("exception-hierarchy", disabled)
            and isinstance(node, ast.ClassDef)
            and (node.name.endswith("Error") or node.name.endswith("Exception"))
            and not _is_exceptions_module(path)
            and node.bases
        ):
            base_names = {
                getattr(b, "id", None) or getattr(b, "attr", None) for b in node.bases
            }
            if base_names & STDLIB_EXCEPTIONS and not base_names - STDLIB_EXCEPTIONS:
                errors.append(
                    (
                        node.lineno,
                        "exception-hierarchy",
                        f"`{node.name}` inherits from stdlib exception (use XorqError)",
                    )
                )

        if (
            _enabled("print", disabled)
            and not is_test
            and path.name != "cli.py"
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            errors.append(
                (node.lineno, "print", "bare print() (use logging or click.echo)")
            )

    results = [Violation(filepath, line, rule, msg) for line, rule, msg in errors]
    if only_lines is not None:
        results = [v for v in results if v.line in only_lines]
    return results


def _parse_disable(args):
    disabled = set()
    remaining = []
    for arg in args:
        if arg.startswith("--disable="):
            for rule in arg[len("--disable=") :].split(","):
                rule = rule.strip()
                if rule not in RULES:
                    print(f"unknown rule: {rule}", file=sys.stderr)
                    print(f"available: {', '.join(sorted(RULES))}", file=sys.stderr)
                    sys.exit(1)
                disabled.add(rule)
        else:
            remaining.append(arg)
    return frozenset(disabled), remaining


def _print_errors(errors):
    for error in errors:
        print(error)


def _hook(args):
    disabled, _ = _parse_disable(args)
    tool_input = json.load(sys.stdin)
    filepath = tool_input.get("file_path", "")
    if not filepath:
        return

    new_string = tool_input.get("new_string")
    old_string = tool_input.get("old_string")
    only_lines = (
        _changed_lines(filepath, new_string, old_string)
        if new_string is not None
        else None
    )

    errors = check(filepath, only_lines, disabled)
    if errors:
        _print_errors(errors)
        sys.exit(1)


def main():
    args = sys.argv[1:]

    if "--list" in args:
        for rule_id, desc in RULES.items():
            print(f"  {rule_id:24s} {desc}")
        return

    if args and args[0] == "--hook":
        return _hook(args[1:])

    disabled, files = _parse_disable(args)

    if not files:
        print(
            "usage: xorq-check-style [--disable=r1,r2] <file> [file ...]",
            file=sys.stderr,
        )
        print("       xorq-check-style --hook [--disable=r1,r2]", file=sys.stderr)
        print("       xorq-check-style --list", file=sys.stderr)
        sys.exit(1)

    all_errors = [error for f in files for error in check(f, disabled=disabled)]
    if all_errors:
        _print_errors(all_errors)
        sys.exit(1)


if __name__ == "__main__":
    main()
