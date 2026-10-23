"""repo-graph-mcp: a codebase as a deterministic AST knowledge graph, served over MCP.

The public surface:

* :func:`repo_graph_mcp.indexer.index` builds or refreshes ``.repo-graph/graph.db``.
* :class:`repo_graph_mcp.graph.Graph` answers questions (callers, impact, tests, ...).
* :func:`repo_graph_mcp.server.build_server` wraps a :class:`Graph` as an MCP server.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
