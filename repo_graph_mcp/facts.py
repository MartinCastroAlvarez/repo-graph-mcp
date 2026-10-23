"""Per-file facts: what a parser saw in one file, before any cross-file resolution.

Facts are plain JSON (stored next to the file's content hash), so an unchanged
file is never parsed again. Resolution into nodes and edges happens in
:mod:`repo_graph_mcp.resolver`, over the facts of every file.
"""

from __future__ import annotations

import re
from typing import Literal
from typing import TypedDict

DefKind = Literal["class", "function", "method", "symbol", "test"]
RefKind = Literal["call", "base", "ref"]

#: Bumped whenever the facts format or a parser changes, so caches are rebuilt.
FACTS_VERSION = 2

DOC_LIMIT = 120
SIG_LIMIT = 160


class Def(TypedDict):
    local: str  # dotted path inside the module: "Class.method"
    kind: DefKind
    name: str
    line: int
    end: int
    sig: str
    doc: str
    returns: str  # static text of the return annotation ("" when unknown)


class Import(TypedDict):
    module: str  # "pkg.mod" (python) or the specifier "./util" (ts/js)
    level: int  # python relative-import level; 0 otherwise
    names: list[list[str]] | None  # [[imported, bound_as]]; None = whole module
    alias: str | None  # name the module itself is bound to (import x as y / import * as y)
    line: int


class Ref(TypedDict):
    scope: str  # local path of the enclosing def ("" = module level)
    target: str  # dotted expression text: "self.helper", "os.path.join", "Foo()"
    line: int
    kind: RefKind


class TypeHint(TypedDict):
    """``name`` has type ``type`` inside ``scope``.

    For a local or parameter, ``scope`` is the function; for an attribute
    (``self.x = ...``, ``x: T`` in a class body) it is the class. ``type`` is a
    dotted expression; a trailing ``()`` means "the result of calling it".
    """

    scope: str
    name: str
    type: str


class Facts(TypedDict):
    version: int
    language: str
    doc: str
    defs: list[Def]
    imports: list[Import]
    refs: list[Ref]
    types: list[TypeHint]
    error: str | None


def empty(language: str, error: str | None = None) -> Facts:
    return Facts(
        version=FACTS_VERSION,
        language=language,
        doc="",
        defs=[],
        imports=[],
        refs=[],
        types=[],
        error=error,
    )


_WS = re.compile(r"\s+")


def clip(text: str, limit: int) -> str:
    """Collapse whitespace and cut to ``limit`` characters."""
    text = _WS.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def first_line(doc: str | None) -> str:
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        if line.strip():
            return clip(line, DOC_LIMIT)
    return ""


_TEST_PARTS = {"tests", "test", "__tests__", "spec", "specs"}


def is_test_path(path: str) -> bool:
    """Heuristic: is this file a test file? (``tests/``, ``test_*.py``, ``*.test.ts`` ...)"""
    parts = path.split("/")
    name = parts[-1]
    if any(p in _TEST_PARTS for p in parts[:-1]):
        return name != "__init__.py" and name != "conftest.py"
    stem = name.split(".", 1)[0]
    if name.endswith((".py", ".pyi")):
        return stem.startswith("test_") or stem.endswith("_test")
    return ".test." in name or ".spec." in name
