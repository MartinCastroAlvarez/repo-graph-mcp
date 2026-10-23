# Contributing to repo-graph-mcp

## TL;DR

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install poetry
poetry install --all-extras
poetry run pytest
poetry run ruff check repo_graph_mcp tests benchmarks && poetry run ruff format --check repo_graph_mcp tests benchmarks
poetry run mypy repo_graph_mcp
poetry run bandit -c pyproject.toml -q -r repo_graph_mcp
poetry run pip-audit
poetry run python benchmarks/token_savings.py --min-ratio 5
```

Open a PR against `main` from a `feat/…`, `fix/…`, `docs/…` or `chore/…` branch.

## What this package will not accept

- Executing, importing or compiling indexed code; any subprocess or shell.
- Embeddings, vector stores, model calls or network access.
- SQL built from strings.
- Output that is not deterministic for the same tree.

## What it does accept

- Better resolution rules (with a fixture case in `tests/test_resolver.py`
  for the link that now resolves and one that must stay unresolved).
- New languages behind an optional extra (a parser producing the same facts).
- New questions (a `Graph` method + MCP tool + CLI entry + tests + README row).
- Client snippets, docs, benchmark questions.

## House rules

1. Tests alongside features; security-relevant changes get a hostile case.
2. A facts format change bumps `FACTS_VERSION` in `facts.py`.
3. No secrets in commits.

## Releasing

1. Bump `version` in `pyproject.toml` and `__version__` in
   `repo_graph_mcp/__init__.py`; move `Unreleased` in `CHANGELOG.md` under the new version.
2. Merge, tag the merge commit `vX.Y.Z`, push the tag. `publish.yml` checks
   tag == version, runs the tests, scans the artefacts and publishes through
   PyPI Trusted Publishing (environment `pypi`), then creates the GitHub Release.
   The PyPI pending publisher must be registered first
   (owner `MartinCastroAlvarez`, repository `repo-graph-mcp`, workflow `publish.yml`, environment `pypi`).
