# Contributing Guide

## Setting up a development environment

This assumes you have [uv](https://docs.astral.sh/uv/getting-started/installation/) installed.

```bash
git clone git@github.com:xorq-labs/xorq-style.git
cd xorq-style
uv sync --group dev
uv run pre-commit install
```

## Running the test suite

```bash
uv run pytest
```

## Writing the commit

xorq-style follows the [Conventional Commits](https://www.conventionalcommits.org/) structure.
In brief, the commit summary should look like:

    fix(cli): handle non-Python files in hook mode

The type (e.g. `fix`) can be:

- `fix`: A bug fix. Correlates with PATCH in SemVer
- `feat`: A new feature. Correlates with MINOR in SemVer
- `docs`: Documentation only changes
- `ci`: Changes to CI configuration
- `style`: Changes that do not affect the meaning of the code

If the commit fixes a GitHub issue, add something like this to the bottom of the description:

    fixes #42

## Release Flow

***This section is intended for xorq maintainers***

### Steps

1. Ensure you're on upstream main: `git switch main && git pull`
2. Compute the new version number (`$version`) according to [Semantic Versioning](https://semver.org/) rules.
3. Create a release branch: `git switch --create release-$version`
4. Update the version in `pyproject.toml`: `version = "$version"`
5. Commit: `git add --update && git commit -m "release: $version"`
6. Open a PR and wait for CI to pass:
   `git push --set-upstream origin release-$version && gh pr create --fill`
7. Squash and merge the PR: `gh pr merge --squash`
8. Tag the updated main and push: `git fetch && git tag v$version origin/main && git push --tags`
9. The `v*` tag triggers the [publish workflow](.github/workflows/publish.yml), which builds and publishes to PyPI via Trusted Publishing.
