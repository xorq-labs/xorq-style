# .envrcs/

direnv configuration split into composable fragments, sourced from the root `.envrc`.

## Tracked (templates & helpers)

| File | Purpose |
|---|---|
| `.envrc.secrets.template` | Template for secrets layer |
| `.envrc.user.template` | Default user env: sources `.envrc.user.uv` |
| `.envrc.user.uv` | User env variant: uv sync + venv activation |

## Gitignored (local)

These are created locally by copying templates. Never commit them.

| File | Created from |
|---|---|
| `.envrc.secrets` | `.envrc.secrets.template` |
| `.envrc.user` | `.envrc.user.template` |

## How it fits together

```
.envrc (repo root)
├── watch_file pyproject.toml
├── export direnv_root
├── source_env_if_exists .envrcs/.envrc.secrets
└── source_env_if_exists .envrcs/.envrc.user
    └── .envrc.user.uv → uv sync + venv activation
```

To get started:

```sh
cp .envrcs/.envrc.secrets.template .envrcs/.envrc.secrets
cp .envrcs/.envrc.user.template .envrcs/.envrc.user
direnv allow
```
