from __future__ import annotations

import ast

from repo_graph_mcp.facts import is_test_path
from repo_graph_mcp.parsers import parse
from repo_graph_mcp.parsers.python import annotation
from repo_graph_mcp.parsers.python import dotted

SOURCE = b'''
"""Module doc.

More text."""
import os.path as osp
import collections
from . import sibling
from ..pkg import thing as other
from .stars import *

LIMIT: int = 3
A, B = 1, 2

@decorate
class Widget(Base, metaclass=Meta):
    """A widget."""

    size: "Gauge"

    def __init__(self, store: Store | None) -> None:
        self.store = store
        self.helper = Helper()

    async def render(self, *, depth: int = 2) -> "Frame":
        def inner():
            return osp.join("a", "b")
        super().render()
        Factory().build().finish()
        self.store.save(LIMIT)
        return inner()

def free(x):
    local = make()
    return local.run(x)
'''


def test_definitions_signatures_and_docs() -> None:
    facts = parse("python", SOURCE, is_test=False)
    assert facts["error"] is None
    assert facts["doc"] == "Module doc."
    defs = {d["local"]: d for d in facts["defs"]}
    assert defs["Widget"]["kind"] == "class"
    assert defs["Widget"]["sig"] == "(Base)"
    assert defs["Widget"]["doc"] == "A widget."
    assert defs["Widget.render"]["kind"] == "method"
    assert defs["Widget.render"]["sig"] == "async (self, *, depth: int=2) -> 'Frame'"
    assert defs["Widget.render"]["returns"] == "Frame"
    assert defs["Widget.render.inner"]["kind"] == "function"
    assert defs["free"]["kind"] == "function"
    assert {"LIMIT", "A", "B"} <= {d["local"] for d in facts["defs"] if d["kind"] == "symbol"}
    assert defs["Widget"]["end"] > defs["Widget"]["line"]


def test_imports() -> None:
    facts = parse("python", SOURCE, is_test=False)
    imports = facts["imports"]
    assert imports[0] == {"module": "os.path", "level": 0, "names": None, "alias": "osp", "line": 5}
    assert imports[1]["module"] == "collections" and imports[1]["alias"] is None
    assert imports[2]["level"] == 1 and imports[2]["names"] == [["sibling", "sibling"]]
    assert imports[3]["level"] == 2 and imports[3]["names"] == [["thing", "other"]]
    assert imports[4]["names"] == [["*", "*"]]


def test_uses_and_type_hints() -> None:
    facts = parse("python", SOURCE, is_test=False)
    calls = {(r["scope"], r["target"]) for r in facts["refs"] if r["kind"] == "call"}
    assert ("Widget.render", "super.render") in calls
    assert ("Widget.render", "Factory().build().finish") in calls
    assert ("Widget.render", "self.store.save") in calls
    assert ("Widget.render.inner", "osp.join") in calls
    bases = {r["target"] for r in facts["refs"] if r["kind"] == "base"}
    assert bases == {"Base"}
    refs = {(r["scope"], r["target"]) for r in facts["refs"] if r["kind"] == "ref"}
    assert ("", "decorate") in refs
    assert ("Widget.render", "LIMIT") in refs
    hints = {(h["scope"], h["name"]): h["type"] for h in facts["types"]}
    assert hints[("Widget.__init__", "store")] == "Store"
    assert hints[("Widget", "store")] == "Store"  # self.store = <typed param>
    assert hints[("Widget", "helper")] == "Helper()"
    assert hints[("Widget", "size")] == "Gauge"
    assert hints[("free", "local")] == "make()"


def test_tests_are_detected_only_in_test_files() -> None:
    src = b"def test_one():\n    pass\n\nclass TestX:\n    def test_two(self):\n        pass\n"
    in_test = {d["local"]: d["kind"] for d in parse("python", src, is_test=True)["defs"]}
    assert in_test == {"test_one": "test", "TestX": "class", "TestX.test_two": "test"}
    plain = {d["local"]: d["kind"] for d in parse("python", src, is_test=False)["defs"]}
    assert plain["test_one"] == "function"


def test_syntax_error_is_reported_not_raised() -> None:
    facts = parse("python", b"def broken(:\n", is_test=False)
    assert facts["error"] and facts["error"].startswith("SyntaxError")
    assert facts["defs"] == []


def test_deep_nesting_does_not_crash() -> None:
    src = ("x = " + "(" * 400 + "1" + ")" * 400 + "\n").encode()
    facts = parse("python", src, is_test=False)
    assert facts["error"] is None or "Error" in facts["error"]


def test_docs_can_be_turned_off() -> None:
    facts = parse("python", SOURCE, is_test=False, docs=False)
    assert facts["doc"] == ""
    assert all(d["doc"] == "" for d in facts["defs"])


def test_dotted_and_annotation_helpers() -> None:
    def expr(text: str) -> ast.expr:
        return ast.parse(text, mode="eval").body

    assert dotted(expr("a.b.c")) == "a.b.c"
    assert dotted(expr("x[0].y")) == "x.y"
    assert dotted(expr("(lambda: 1)()")) is None
    assert annotation(expr("Optional[Foo]")) == "Foo"
    assert annotation(expr("None | Foo")) == "Foo"
    assert annotation(expr("list[Foo]")) == ""
    assert annotation(expr("'mod.Foo'")) == "mod.Foo"
    assert annotation(expr("'not valid ('")) == ""
    assert annotation(None) == ""


def test_is_test_path() -> None:
    assert is_test_path("tests/test_a.py")
    assert is_test_path("pkg/test_a.py")
    assert is_test_path("pkg/a_test.py")
    assert is_test_path("src/a.test.ts")
    assert is_test_path("src/__tests__/a.ts")
    assert not is_test_path("tests/conftest.py")
    assert not is_test_path("tests/__init__.py")
    assert not is_test_path("pkg/testing.py")
    assert not is_test_path("src/a.ts")


def test_unknown_language() -> None:
    assert parse("cobol", b"", is_test=False)["error"] == "unsupported language cobol"
