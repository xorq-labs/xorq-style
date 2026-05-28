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

## Configuration

Add a `[tool.xorq-style]` section to your `pyproject.toml`:

```toml
[tool.xorq-style]
disable = ["dataclasses", "os-path"]   # disable rules globally

[tool.xorq-style.os-environ]
allow-paths = ["common/", "utils/"]    # allow os.environ in these paths

[tool.xorq-style.exception-hierarchy]
base-class = "XorqError"              # expected base class (default: XorqError)

[tool.xorq-style.print]
allow-files = ["cli.py"]              # allow print() in these filenames
```

All fields are optional. The config file is discovered by walking up from the checked file's directory.

## Inline suppression

Suppress a rule on a single line with a trailing comment:

```python
from dataclasses import dataclass  # xorq-style: disable=dataclasses
```

Multiple rules can be comma-separated:

```python
import os.path  # xorq-style: disable=os-path,deferred-stdlib
```
