"""Copy-paste MCP client configuration for Claude Code, Cursor, Codex and OpenCode."""

from __future__ import annotations

import json

CLIENTS = ("claude", "cursor", "codex", "opencode")


def snippet(client: str, command: str = "repo-graph", root: str | None = None) -> str:
    """Return the configuration text for ``client`` (the server runs ``repo-graph serve``)."""
    args = ["serve"] + (["--root", root] if root else [])
    if client == "claude":
        mcp_json = {
            "mcpServers": {"repo-graph": {"type": "stdio", "command": command, "args": args}}
        }
        return (
            "# Claude Code: one command (project scope writes .mcp.json)\n"
            f"claude mcp add --scope project repo-graph -- {command} {' '.join(args)}\n\n"
            "# or commit this .mcp.json at the repository root\n"
            f"{json.dumps(mcp_json, indent=2)}\n"
        )
    if client == "cursor":
        cfg_cursor = {"mcpServers": {"repo-graph": {"command": command, "args": args}}}
        return f"// .cursor/mcp.json\n{json.dumps(cfg_cursor, indent=2)}\n"
    if client == "codex":
        toml_args = ", ".join(json.dumps(a) for a in args)
        return (
            "# Codex: one command\n"
            f"codex mcp add repo-graph -- {command} {' '.join(args)}\n\n"
            "# or ~/.codex/config.toml\n"
            "[mcp_servers.repo-graph]\n"
            f"command = {json.dumps(command)}\n"
            f"args = [{toml_args}]\n"
        )
    if client == "opencode":
        cfg: dict[str, object] = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {"repo-graph": {"type": "local", "command": [command, *args], "enabled": True}},
        }
        return f"// opencode.json\n{json.dumps(cfg, indent=2)}\n"
    raise ValueError(f"unknown client {client!r}; choose one of {', '.join(CLIENTS)}")
