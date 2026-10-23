"""Export the graph as JSON, Mermaid or GraphViz DOT.

``level="module"`` draws internal modules and their imports (the architecture
view); ``level="symbol"`` draws classes and functions with calls and inherits;
``level="all"`` (JSON only) is every node and edge.
Labels are escaped for each format, so names from the repository cannot break
out of the diagram syntax.
"""

from __future__ import annotations

import json
from typing import Any

from repo_graph_mcp.graph import Graph

FORMATS = ("json", "mermaid", "dot")
LEVELS = ("module", "symbol", "all")
_SYMBOL_KINDS = {"class", "function", "method"}


def _select(
    graph: Graph, level: str, focus: str | None, max_nodes: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if level == "module":
        node_sql = (
            "SELECT id, qualname, kind, file, line FROM nodes WHERE kind = 'module' ORDER BY id"
        )
        kinds = {"imports"}
    else:
        node_sql = (
            "SELECT id, qualname, kind, file, line FROM nodes "
            "WHERE kind IN ('class', 'function', 'method') AND is_test = 0 ORDER BY id"
        )
        kinds = {"calls", "inherits"}
    rows = graph.store.query(node_sql)
    if focus:
        rows = [
            r
            for r in rows
            if (r["file"] or "").startswith(focus) or r["qualname"].startswith(focus)
        ]
    rows = rows[:max_nodes]
    keep = {r["id"]: r for r in rows}
    nodes = [
        {"id": r["qualname"], "kind": r["kind"], "loc": f"{r['file']}:{r['line']}"} for r in rows
    ]
    edges = []
    for e in graph.store.query(
        "SELECT src, dst, kind, line, confidence FROM edges ORDER BY src, dst, kind"
    ):
        if e["kind"] in kinds and e["src"] in keep and e["dst"] in keep:
            edges.append(
                {
                    "src": keep[e["src"]]["qualname"],
                    "dst": keep[e["dst"]]["qualname"],
                    "kind": e["kind"],
                    "confidence": e["confidence"],
                }
            )
    return nodes, edges


def _full(graph: Graph) -> dict[str, Any]:
    rows = graph.store.query(
        "SELECT id, qualname, kind, name, file, line, end_line, signature, doc, is_test "
        "FROM nodes ORDER BY id"
    )
    ids = {r["id"]: r["qualname"] for r in rows}
    nodes = [
        {
            "id": r["qualname"],
            "kind": r["kind"],
            "name": r["name"],
            "file": r["file"],
            "line": r["line"],
            "end_line": r["end_line"],
            "signature": r["signature"],
            "doc": r["doc"],
            "is_test": bool(r["is_test"]),
        }
        for r in rows
    ]
    edges = [
        {
            "src": ids[e["src"]],
            "dst": ids[e["dst"]],
            "kind": e["kind"],
            "line": e["line"],
            "confidence": e["confidence"],
        }
        for e in graph.store.query(
            "SELECT src, dst, kind, line, confidence FROM edges ORDER BY src, dst, kind"
        )
    ]
    return {"nodes": nodes, "edges": edges}


def _mermaid_label(text: str) -> str:
    out = (
        text.replace("&", "#amp;").replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")
    )
    return out.replace("\n", " ")


def _dot_label(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def export(
    graph: Graph,
    fmt: str = "mermaid",
    *,
    level: str = "module",
    focus: str | None = None,
    max_nodes: int = 200,
) -> str:
    """Render the graph. ``level="all"`` (json only) is every node and edge."""
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {', '.join(FORMATS)}")
    if level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}")
    if level == "all":
        if fmt != "json":
            raise ValueError("level 'all' is only available as json")
        return json.dumps(_full(graph), indent=1, ensure_ascii=False)
    nodes, edges = _select(graph, level, focus, max_nodes)
    if fmt == "json":
        return json.dumps({"nodes": nodes, "edges": edges}, indent=1, ensure_ascii=False)
    alias = {n["id"]: f"n{i}" for i, n in enumerate(nodes)}
    if fmt == "mermaid":
        lines = ["flowchart LR"]
        for n in nodes:
            shape = ('(["', '"])') if n["kind"] == "module" else ('["', '"]')
            lines.append(f"    {alias[n['id']]}{shape[0]}{_mermaid_label(n['id'])}{shape[1]}")
        for e in edges:
            arrow = "-.->" if e["kind"] == "inherits" or e["confidence"] != "exact" else "-->"
            lines.append(f"    {alias[e['src']]} {arrow} {alias[e['dst']]}")
        return "\n".join(lines) + "\n"
    lines = [
        "digraph repo_graph {",
        "    rankdir=LR;",
        '    node [shape=box, fontname="Helvetica"];',
    ]
    for n in nodes:
        lines.append(f'    {alias[n["id"]]} [label="{_dot_label(n["id"])}"];')
    for e in edges:
        style = " [style=dashed]" if e["kind"] == "inherits" or e["confidence"] != "exact" else ""
        lines.append(f"    {alias[e['src']]} -> {alias[e['dst']]}{style};")
    lines.append("}")
    return "\n".join(lines) + "\n"
