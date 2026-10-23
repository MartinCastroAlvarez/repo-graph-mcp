"""Parsers turn one file's bytes into :class:`repo_graph_mcp.facts.Facts`.

They never execute, import or evaluate the code: Python goes through
:func:`ast.parse`, TypeScript/JavaScript through tree-sitter.
"""

from __future__ import annotations

from repo_graph_mcp.facts import Facts
from repo_graph_mcp.facts import empty
from repo_graph_mcp.parsers import python as python_parser


def typescript_available() -> bool:
    """True when the optional ``typescript`` extra (tree-sitter) is installed."""
    from repo_graph_mcp.parsers import typescript

    return typescript.available()


def parse(language: str, source: bytes, *, is_test: bool, docs: bool = True) -> Facts:
    """Dispatch to the parser for ``language``."""
    if language == "python":
        return python_parser.parse(source, is_test=is_test, docs=docs)
    if language in {"typescript", "tsx", "javascript"}:
        from repo_graph_mcp.parsers import typescript

        if not typescript.available():
            return empty(language, error="tree-sitter not installed")
        return typescript.parse(source, language, is_test=is_test, docs=docs)
    return empty(language, error=f"unsupported language {language}")
