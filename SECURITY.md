# Security

repo-graph-mcp reads source code and answers questions about it for AI coding
agents. It is installed by other people and pointed at repositories they may
not have written, and its answers go straight into a model's context. The
design goal: nothing in an indexed repository, and nothing a model sends
through MCP, can make repo-graph-mcp run code, read outside the repository, or
write anywhere but its own database.

## Reporting a vulnerability

Open a private security advisory at
<https://github.com/MartinCastroAlvarez/repo-graph-mcp/security/advisories/new>.
Do **not** open a public issue for a suspected vulnerability. We acknowledge
within 5 business days and aim to ship a fix within 30 days; you are credited
in the advisory unless you ask otherwise.

Supported versions: the latest minor release.

## 1. Threat model

**Assets.** The machine the indexer and server run on (a laptop, an agent
sandbox, CI); files outside the indexed root (home directory, SSH keys, other
projects); the integrity of the answers an agent acts on.

**Actors.**

| Actor | Controls | Must not be able to |
| --- | --- | --- |
| Repository content (possibly hostile: a cloned dependency, a PR branch) | file names, contents, symlinks, `.gitignore`, `[tool.repo-graph]` | execute code, make the indexer read or write outside the root, exhaust memory/disk unboundedly |
| The model calling MCP tools | every tool argument | read files outside the root, run SQL of its choosing, write anything except `reindex` into `graph.db` |
| Text inside the repository reaching the model | names, docstrings, comments | be mistaken for instructions (see prompt injection below) |

**Threats and controls.**

| Threat | Control |
| --- | --- |
| Executing project code | Python is parsed with `ast.parse` only (no `compile`, `exec`, import); TS/JS with tree-sitter. A fixture module that writes a file when executed is indexed in the tests and the file must never appear. A test scans the package for `eval`, `exec`, `compile`, `__import__`, `subprocess`, `pickle`, `marshal`, `runpy`, `importlib`, `yaml`, `os.system`/`popen` and `shell=`. |
| Reading outside the root | The walker never follows symlinks (directories are not descended; a file symlink pointing outside the root is refused and reported). Files are opened with `O_NOFOLLOW`. The `source` tool resolves `root / file` through `paths.safe_join` (no absolute paths, no `..`, NUL refused, resolved path must stay inside the root) and refuses symlinks — even if the database were tampered with (tested). |
| Writing outside the database | Indexing never modifies repository files (tested via mtimes). The database path is refused when it, or its directory, is a symlink, or when it lexically sits in the root but resolves outside it. The only file created besides `graph.db` is `.repo-graph/.gitignore`. |
| SQL injection | Every statement is a constant string with `?` placeholders (a test walks the AST of every `execute`/`query` call). Query connections are opened `mode=ro`. FTS5 input is tokenised to `[A-Za-z0-9_]` words, each quoted, so operators like `NEAR`, `OR`, `*`, `"` are inert (tested). |
| Resource exhaustion | `max_file_bytes` (1 MB) and `max_files` (20 000) limits, both reported when hit; deep nesting is caught (`RecursionError`) and recorded as a parse error; graph walks are capped in depth (6, 12 for paths) and results (500). |
| Malformed input | Syntax errors produce a recorded `error` for that file, never a crash; tree-sitter recovers partially and marks the file. |
| Diagram injection | Mermaid and DOT labels are escaped (`"`, `<`, `>`, `&`, backslashes, newlines). |
| Network / cost | No network calls, no telemetry, no model calls. The MCP server speaks stdio only. |

**Prompt injection.** Names, signatures and first docstring lines come from
the repository and are passed to the model as data. A docstring can say
"ignore previous instructions". The server's MCP `instructions` tell the
client to treat them as data; the answers are short (docstrings are cut to one
line of 120 characters, signatures to 160); `docs = false` keeps comments and
docstrings out of the graph entirely. Agents should still apply their own
policy to repository content, as they would when reading files.

**Out of scope.** Vulnerabilities in the MCP SDK, tree-sitter grammars or
SQLite themselves; an operator who points `--db` at a sensitive absolute path
on purpose; the heuristics being incomplete (a static graph can miss dynamic
calls — answers marked `conf: name` are guesses).

## 2. Non-negotiable rules

Enforced by `tests/test_security.py`.

1. Source is parsed, never imported, compiled or executed.
2. No `subprocess`, shell, `eval`/`exec`, `pickle`/`marshal`/`yaml`.
3. Every path from the database or a tool argument goes through `paths.safe_join`.
4. SQL is constant text with placeholders; reads use a read-only connection.
5. Writes go only to the configured database and its `.gitignore`.
