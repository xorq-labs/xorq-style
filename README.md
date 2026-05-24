# xorq-style

Shared style enforcement and Claude Code tooling for xorq Python projects.

## Install

```bash
pip install xorq-style
```

Or as a dev dependency:

```bash
pip install -e ".[dev]"  # if added to your project's dev extras
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
            "command": "printf '%s' \"$CLAUDE_TOOL_INPUT\" | xorq-check-style --hook"
          }
        ]
      }
    ]
  }
}
```

### As a CLI

```bash
xorq-check-style src/myproject/foo.py src/myproject/bar.py
xorq-check-style --list                          # show all rules
xorq-check-style --disable=print,dataclasses .   # skip specific rules
```

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
| `cache-method` | No @functools.cache on methods (leaks memory via self) |
| `exception-hierarchy` | Custom exceptions must inherit from XorqError |
| `print` | No bare print() in library code (use logging/click.echo) |
