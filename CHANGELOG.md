# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### To do before the first PyPI release
- Register the PyPI pending publisher for `publish.yml` (environment `pypi`),
  then tag `v0.1.0`.

## [0.1.0] — unreleased

### Added
- Python parser (stdlib `ast`) and TypeScript/TSX/JavaScript parser
  (tree-sitter, `typescript` extra): modules, classes, functions, methods,
  symbols, tests; imports, defines, calls, inherits, tests, references edges.
- Static resolution through imports, re-exports, star and relative imports,
  `self`/`cls`/`this`, `super()`, annotated parameters and attributes,
  `x = Foo()`, return annotations and the class hierarchy; unique-name
  fallback marked `confidence = "name"`.
- Test linking: calls from tests, test-name heuristic, test-module naming.
- Incremental indexing keyed by SHA-256 into `.repo-graph/graph.db` (SQLite,
  FTS5), deterministic ids.
- MCP server (official SDK, `MCPServer`, stdio) with `find_symbol`, `source`,
  `callers`, `callees`, `dependencies`, `dependents`, `impact`, `tests_for`,
  `path_between`, `module_summary`, `search`, `stats`, `reindex`.
- CLI `repo-graph index | query | serve | stats | export | config`; exports
  to Mermaid, DOT and JSON; client snippets for Claude Code, Cursor, Codex,
  OpenCode.
- Offline token-savings benchmark (`benchmarks/token_savings.py`).
- Security: no code execution, `.gitignore`, size and count limits, symlink
  and path confinement, read-only query connection, constant SQL.
