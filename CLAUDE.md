# CLAUDE.md

Contract between this repository and any AI agent contributing to it.

## Required reading

1. This file. 2. [`README.md`](README.md). 3. [`ARCHITECTURE.md`](ARCHITECTURE.md).
4. [`SECURITY.md`](SECURITY.md).

## Mission

`repo-graph-mcp` gives agents a cheap, exact view of a codebase. Answers must
stay deterministic, small and honest (a guess is marked `conf: name`).

## The rules

1. **Never execute the indexed code.** Parse with `ast` / tree-sitter only.
   No `subprocess`, shell, `eval`, `exec`, `pickle`, `importlib`.
2. **No path escapes.** Every path from the database or a tool argument goes
   through `paths.safe_join`; files are read with `paths.read_bounded`.
3. **Constant SQL.** `?` placeholders only; reads use the read-only store.
4. **Deterministic output.** Sort what you iterate; the same tree must give
   the same database (tested).
5. **Facts format changes bump `FACTS_VERSION`.**
6. **Every tool** has a `Graph` method, a CLI `query` entry, tests and a README row.
7. Never add `# noqa`, `# type: ignore` or `# nosec` to the package, skip a
   test, or loosen lint/type/coverage config. Fix the code.

## Done means

`poetry run pytest` (coverage ≥ 85%), `ruff check`, `ruff format --check`,
`mypy` (strict), `bandit`, `pip-audit` and `python benchmarks/token_savings.py
--min-ratio 5` all green; docs and CHANGELOG updated.

## Git

Small commits, imperative subjects, never force-push `main`, never commit
secrets or `.repo-graph/`.
