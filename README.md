# xorq-style

Shared style enforcement and Claude Code tooling for xorq Python projects.

## Install

```bash
pip install xorq-style
```

Or as a dev dependency (with [uv](https://docs.astral.sh/uv/)):

```bash
uv sync --group dev
```

## Usage

### As a Claude Code hook

Add to your project's `.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "{ command -v xorq-check-style >/dev/null 2>&1 || exit 0; } && xorq-check-style --hook"
          }
        ]
      }
    ]
  }
}
```

The hook runs after every `Edit` or `Write` tool call. It reads the tool input from stdin, checks the written file against xorq style rules, and exits non-zero if there are violations — causing Claude Code to see and fix them.

### As a CLI

```bash
xorq-check-style src/myproject/foo.py src/myproject/bar.py
xorq-check-style --list                          # show all rules
xorq-check-style --disable=print,dataclasses .   # skip specific rules
```

### Lint only changed lines

Pipe any unified diff to `--diff` to lint only the lines that were added or modified:

```bash
git diff | xorq-check-style --diff               # unstaged changes
git diff HEAD~3 | xorq-check-style --diff        # last 3 commits
git diff main | xorq-check-style --diff          # changes vs main branch
```

This gives the same changed-line scoping that the Claude Code hook gets automatically.

### JSON output

Add `--json` to any invocation to get machine-readable output on stdout:

```bash
xorq-check-style --json src/myproject/foo.py
git diff | xorq-check-style --diff --json
```

Each violation is an object with `filepath`, `line`, `rule`, and `message` fields:

```json
[
  {"filepath": "src/foo.py", "line": 12, "rule": "os-path", "message": "import os.path (use pathlib.Path instead)"}
]
```

JSON is written to stdout; in text mode (the default), violations go to stderr.
Both modes exit 0 when clean and exit 2 when there are violations.

### Shell completions

```bash
# one-time install to standard location
xorq-check-style install-completion bash   # or zsh, fish

# or eval in your shell config
eval "$(xorq-check-style completion bash)"
```

The `--disable` option supports tab completion for rule names, including comma-separated lists.

## Rules

| Rule | Description |
|------|-------------|
| `relative-import` | No relative imports (use absolute imports) |
| `test-class` | No test classes (use plain test functions) |
| `deferred-import-test` | No deferred imports in test files |
| `deferred-stdlib` | No deferred stdlib imports (anywhere) |
| `os-environ` | No os.environ outside common/utils/ |
| `future-annotations` | Missing `from __future__ import annotations` |
| `os-path` | No os.path (use pathlib.Path) |
| `dataclasses` | No dataclasses (use attrs) |
| `cache-method` | No @functools.cache/lru_cache on methods (leaks memory via self) |
| `exception-hierarchy` | Custom exceptions must inherit from XorqError |
| `print` | No bare print() in library code (use logging/click.echo) |
| `type-annotations` | Functions must have type annotations |
| `unlisted-import` | Imported name not listed in target module's `__all__` |
| `init-reexport` | Non-`__init__` module re-exports imported name via `__all__` |
| `init-all` | `__init__.py` must declare `__all__` listing all public local names |

## Configuration

Add a `[tool.xorq-style]` section to your `pyproject.toml`:

```toml
[tool.xorq-style]
disable = ["dataclasses", "os-path"]   # disable rules globally

[tool.xorq-style.os-environ]
# allow os.environ in matching files, using the same gitignore-style patterns as
# print.allow-files; to exempt a whole directory tree, add /** (e.g. "common/**")
allow-paths = ["common/**", "src/**/utils/**"]

[tool.xorq-style.exception-hierarchy]
base-class = "XorqError"              # expected base class (default: XorqError)

[tool.xorq-style.print]
# allow print() in matching files. Patterns use gitignore syntax: a bare name
# matches anywhere; patterns with a slash (incl. ** globs) are anchored to pyproject.toml
allow-files = ["cli.py", "src/pkg/scripts.py", "src/**/repl.py"]

[tool.xorq-style.unlisted-import]
src-roots = ["python/"]               # source roots relative to pyproject.toml (default: ["src", "."])
```

All fields are optional. The config file is discovered by walking up from the checked file's directory.

> **Migration note (os-environ):** `os-environ.allow-paths` previously matched as
> plain substrings of the file path. It now uses gitignore-style globs, like
> `print.allow-files`. Rewrite bare directory prefixes as subtree globs — e.g.
> `allow-paths = ["common/"]` becomes `allow-paths = ["common/**"]`.

## Inline suppression

Suppress a rule on a single line with a trailing comment:

```python
from dataclasses import dataclass  # xorq-style: disable=dataclasses
```

Multiple rules can be comma-separated:

```python
import os.path  # xorq-style: disable=os-path,deferred-stdlib
```

The comment goes on the line the rule reports. For `init-all`, that is the
public definition itself, so a deliberately internal name is excluded from
`__all__` by annotating its definition (not the `__all__` line):

```python
def wrapped_do_connect() -> None: ...  # xorq-style: disable=init-all

__all__ = ["connect"]
```
