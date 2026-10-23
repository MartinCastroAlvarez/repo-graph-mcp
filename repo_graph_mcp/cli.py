"""``repo-graph``: index | query | serve | stats | export | config.

Exit codes: 0 ok, 1 the question had no answer (unknown/ambiguous symbol), 2 usage or config error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from collections.abc import Sequence
from typing import Any

from repo_graph_mcp import __version__
from repo_graph_mcp import config as config_mod
from repo_graph_mcp.clients import CLIENTS
from repo_graph_mcp.clients import snippet
from repo_graph_mcp.export import FORMATS
from repo_graph_mcp.export import LEVELS
from repo_graph_mcp.export import export
from repo_graph_mcp.graph import Graph
from repo_graph_mcp.graph import GraphError
from repo_graph_mcp.graph import dumps
from repo_graph_mcp.graph import prune
from repo_graph_mcp.indexer import index
from repo_graph_mcp.paths import PathEscapeError

Query = Callable[[Graph, argparse.Namespace], dict[str, Any]]

QUERIES: dict[str, tuple[list[tuple[str, dict[str, Any]]], Query]] = {
    "find_symbol": (
        [("name", {}), ("--kind", {}), ("--limit", {"type": int, "default": 20})],
        lambda g, a: g.find_symbol(a.name, a.kind, a.limit),
    ),
    "source": (
        [
            ("symbol", {}),
            ("--context", {"type": int, "default": 0}),
            ("--max-lines", {"type": int, "default": 200}),
        ],
        lambda g, a: g.source(a.symbol, a.context, a.max_lines),
    ),
    "callers": (
        [
            ("symbol", {}),
            ("--depth", {"type": int, "default": 1}),
            ("--limit", {"type": int, "default": 50}),
        ],
        lambda g, a: g.callers(a.symbol, a.depth, a.limit),
    ),
    "callees": (
        [
            ("symbol", {}),
            ("--depth", {"type": int, "default": 1}),
            ("--limit", {"type": int, "default": 50}),
        ],
        lambda g, a: g.callees(a.symbol, a.depth, a.limit),
    ),
    "dependencies": (
        [("module", {}), ("--no-external", {"action": "store_true"})],
        lambda g, a: g.dependencies(a.module, not a.no_external),
    ),
    "dependents": (
        [
            ("module", {}),
            ("--depth", {"type": int, "default": 1}),
            ("--limit", {"type": int, "default": 100}),
        ],
        lambda g, a: g.dependents(a.module, a.depth, a.limit),
    ),
    "impact": (
        [
            ("symbol", {}),
            ("--depth", {"type": int, "default": 3}),
            ("--limit", {"type": int, "default": 100}),
        ],
        lambda g, a: g.impact(a.symbol, a.depth, a.limit),
    ),
    "tests_for": (
        [
            ("symbol", {}),
            ("--depth", {"type": int, "default": 3}),
            ("--limit", {"type": int, "default": 50}),
        ],
        lambda g, a: g.tests_for(a.symbol, a.depth, a.limit),
    ),
    "path_between": (
        [("source", {}), ("target", {}), ("--max-depth", {"type": int, "default": 8})],
        lambda g, a: g.path_between(a.source, a.target, a.max_depth),
    ),
    "module_summary": ([("module", {})], lambda g, a: g.module_summary(a.module)),
    "search": (
        [("query", {}), ("--kind", {}), ("--limit", {"type": int, "default": 20})],
        lambda g, a: g.search(a.query, a.kind, a.limit),
    ),
}


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".", help="repository root (default: current directory)")
    common.add_argument(
        "--db", default=None, help="graph database (default: <root>/.repo-graph/graph.db)"
    )

    parser = argparse.ArgumentParser(
        prog="repo-graph",
        description="A deterministic AST knowledge graph of a codebase, served over MCP.",
    )
    parser.add_argument("--version", action="version", version=f"repo-graph {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", parents=[common], help="build or refresh the graph (incremental)")
    p.add_argument("--full", action="store_true", help="re-parse every file")
    p.add_argument("--json", action="store_true", help="print the report as JSON")

    q = sub.add_parser("query", parents=[common], help="ask the graph a question")
    q.add_argument("--pretty", action="store_true", help="indented JSON")
    qs = q.add_subparsers(dest="tool", required=True)
    for name, (arguments, _) in QUERIES.items():
        tp = qs.add_parser(name, help=f"same as the `{name}` MCP tool")
        for flag, options in arguments:
            tp.add_argument(flag, **options)

    s = sub.add_parser("serve", parents=[common], help="run the MCP server on stdio")
    s.add_argument(
        "--no-index", action="store_true", help="serve the existing graph without refreshing it"
    )

    sub.add_parser("stats", parents=[common], help="counts of files, nodes and edges")

    e = sub.add_parser("export", parents=[common], help="export as mermaid, json or dot")
    e.add_argument("--format", choices=FORMATS, default="mermaid")
    e.add_argument("--level", choices=LEVELS, default="module")
    e.add_argument("--focus", default=None, help="only nodes whose file or id starts with this")
    e.add_argument("--max-nodes", type=int, default=200)
    e.add_argument("-o", "--output", default=None, help="write to a file instead of stdout")

    c = sub.add_parser("config", help="print MCP client configuration")
    c.add_argument("client", choices=CLIENTS)
    c.add_argument(
        "--exe", default="repo-graph", help="executable the client launches (default: repo-graph)"
    )
    c.add_argument("--root", default=None, help="pass --root to the server")
    return parser


def _open_graph(cfg: config_mod.Config) -> Graph:
    if not cfg.db.exists():
        index(cfg)
    return Graph.open(cfg.db, cfg.root)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out = sys.stdout
    try:
        if args.command == "config":
            out.write(snippet(args.client, args.exe, args.root))
            return 0
        cfg = config_mod.load(args.root, db=args.db)
        if args.command == "index":
            report = index(cfg, full=args.full)
            if args.json:
                out.write(json.dumps(report.as_dict(), indent=1) + "\n")
            else:
                out.write(
                    f"indexed {report.files} files ({report.parsed} parsed, "
                    f"{report.reused} unchanged, "
                    f"{report.removed} removed): {report.nodes} nodes, {report.edges} edges "
                    f"in {report.seconds}s -> {cfg.db}\n"
                )
                for note in report.notes:
                    out.write(f"note: {note}\n")
            return 0
        if args.command == "serve":  # pragma: no cover - blocks on stdio
            from repo_graph_mcp.server import serve

            serve(cfg, refresh=not args.no_index)
            return 0
        graph = _open_graph(cfg)
        try:
            if args.command == "stats":
                out.write(json.dumps(prune(graph.stats()), indent=1) + "\n")
            elif args.command == "export":
                text = export(
                    graph, args.format, level=args.level, focus=args.focus, max_nodes=args.max_nodes
                )
                if args.output:
                    with open(args.output, "w", encoding="utf-8") as fh:
                        fh.write(text)
                else:
                    out.write(text)
            else:
                answer = QUERIES[args.tool][1](graph, args)
                if args.pretty:
                    out.write(json.dumps(prune(answer), indent=1, ensure_ascii=False) + "\n")
                else:
                    out.write(dumps(answer) + "\n")
        finally:
            graph.close()
        return 0
    except GraphError as exc:
        sys.stderr.write(dumps(exc.as_dict()) + "\n")
        return 1
    except (config_mod.ConfigError, PathEscapeError, ValueError, FileNotFoundError) as exc:
        sys.stderr.write(f"repo-graph: {exc}\n")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
