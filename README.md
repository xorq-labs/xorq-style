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
            "command": "command -v xorq-check-style >/dev/null 2>&1 && printf '%s' \"$CLAUDE_TOOL_INPUT\" | xorq-check-style --hook || true"
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
