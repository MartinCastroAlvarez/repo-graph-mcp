# Examples

| File | Use |
| --- | --- |
| [`claude-code.mcp.json`](claude-code.mcp.json) | save as `.mcp.json` at the repository root (or `claude mcp add --scope project repo-graph -- repo-graph serve`) |
| [`cursor.mcp.json`](cursor.mcp.json) | save as `.cursor/mcp.json` |
| [`codex.config.toml`](codex.config.toml) | append to `~/.codex/config.toml` |
| [`opencode.json`](opencode.json) | merge into `opencode.json` |
| [`repo-graph.toml`](repo-graph.toml) | every configuration key, commented; save as `.repo-graph.toml` |
| [`CLAUDE-snippet.md`](CLAUDE-snippet.md) | a paragraph for `CLAUDE.md` / `AGENTS.md` telling the agent when to ask the graph |

`repo-graph config claude|cursor|codex|opencode [--root PATH]` prints the same
snippets. All of them start `repo-graph serve`, which refreshes the index
incrementally and then speaks MCP over stdio in the client's working directory.
