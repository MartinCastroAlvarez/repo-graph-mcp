from __future__ import annotations

import json
from pathlib import Path

import anyio
from mcp import Client

from repo_graph_mcp import config
from repo_graph_mcp.indexer import index
from repo_graph_mcp.server import build_server

TOOLS = {
    "find_symbol",
    "source",
    "callers",
    "callees",
    "dependencies",
    "dependents",
    "impact",
    "tests_for",
    "path_between",
    "module_summary",
    "search",
    "stats",
    "reindex",
}


def test_server_over_the_mcp_protocol(pyrepo: Path) -> None:
    cfg = config.load(pyrepo)
    index(cfg)
    server, state = build_server(cfg)

    async def scenario() -> None:
        async with Client(server) as client:
            listed = await client.list_tools()
            names = {t.name for t in listed.tools}
            assert names == TOOLS
            read_only = {
                t.name for t in listed.tools if t.annotations and t.annotations.read_only_hint
            }
            assert read_only == TOOLS - {"reindex"}

            result = await client.call_tool("callers", {"symbol": "audit"})
            assert not result.is_error
            text = result.content[0].text
            assert " " not in text.split('"sig"')[0]  # compact JSON
            payload = json.loads(text)
            assert {c["id"] for c in payload["callers"]} >= {"shop.service:Checkout.run"}

            bad = await client.call_tool("callers", {"symbol": "save"})
            assert bad.is_error
            assert "ambiguous" in bad.content[0].text

            (pyrepo / "shop" / "extra.py").write_text(
                "from shop.models import audit\n\ndef shout():\n    audit('!')\n"
            )
            refreshed = await client.call_tool("reindex", {})
            assert json.loads(refreshed.content[0].text)["parsed"] == 1
            after = await client.call_tool("callers", {"symbol": "audit"})
            assert "shop.extra:shout" in after.content[0].text

            impact = await client.call_tool("impact", {"symbol": "audit", "depth": 99})
            assert not impact.is_error  # depth is clamped, not refused

    try:
        anyio.run(scenario)
    finally:
        state.close()


def test_stdio_transport_end_to_end(pyrepo: Path) -> None:
    import sys

    from mcp import StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "repo_graph_mcp", "serve", "--root", str(pyrepo)]
    )

    async def scenario() -> str:
        async with Client(params) as client:
            result = await client.call_tool("find_symbol", {"name": "audit"})
            return str(result.content[0].text)

    assert '"id":"shop.models:audit"' in anyio.run(scenario)
    assert (pyrepo / ".repo-graph" / "graph.db").is_file()
