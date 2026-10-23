from __future__ import annotations

import importlib.util

import pytest

from repo_graph_mcp.parsers import parse
from repo_graph_mcp.parsers import typescript

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter") is None, reason="typescript extra not installed"
)

SOURCE = b"""/** File doc. */
import Default, { a, b as c } from "./util";
import * as ns from "../lib/x";
import "./side-effect";
export { re } from "./re";
const legacy = require("./legacy");
const { one, two: second } = require("./pair");

/** A widget. */
export class Widget extends Base implements Shape {
  private size = 0;
  handler = (e: Event) => { this.render(); };
  constructor(private store: Store, plain: number) { super(); }
  render(depth: number): Frame {
    this.store.save();
    ns.go(new Helper().run());
    a(c(legacy.x()));
    return null;
  }
}

export interface Shape extends Thing { area(): number }
export enum Color { Red }
export type Id = string;
export const VERSION = "1";
export const make = async (n: number): Promise<Widget> => new Widget(n);
function plainFunction(x: Widget | null) { const w: Widget = x; return import("./lazy"); }
const instance = new Widget();
"""


def test_definitions() -> None:
    facts = parse("typescript", SOURCE, is_test=False)
    assert facts["error"] is None
    assert facts["doc"] == "File doc."
    defs = {d["local"]: d for d in facts["defs"]}
    assert defs["Widget"]["kind"] == "class"
    assert defs["Widget"]["sig"] == "(Base, Shape)"
    assert defs["Widget"]["doc"] == "A widget."
    assert defs["Widget.render"]["kind"] == "method"
    assert defs["Widget.render"]["sig"] == "(depth: number): Frame"
    assert defs["Widget.render"]["returns"] == "Frame"
    assert defs["Widget.handler"]["kind"] == "method"
    assert defs["Shape"]["sig"] == "interface"
    assert defs["Color"]["kind"] == "symbol" and defs["Id"]["sig"] == "type"
    assert defs["VERSION"]["kind"] == "symbol"
    assert defs["make"]["kind"] == "function"
    assert defs["plainFunction"]["kind"] == "function"


def test_imports() -> None:
    facts = parse("typescript", SOURCE, is_test=False)
    by_module = {i["module"]: i for i in facts["imports"]}
    assert by_module["./util"]["names"] == [["default", "Default"], ["a", "a"], ["b", "c"]]
    assert by_module["../lib/x"]["alias"] == "ns" and by_module["../lib/x"]["names"] is None
    assert by_module["./side-effect"]["names"] == []
    assert by_module["./re"]["names"] == [["re", "re"]]
    assert by_module["./legacy"]["alias"] == "legacy"
    assert by_module["./pair"]["names"] == [["one", "one"], ["two", "second"]]
    assert by_module["./lazy"]["names"] == []


def test_uses_and_types() -> None:
    facts = parse("typescript", SOURCE, is_test=False)
    calls = {(r["scope"], r["target"]) for r in facts["refs"] if r["kind"] == "call"}
    assert ("Widget.render", "this.store.save") in calls
    assert ("Widget.render", "ns.go") in calls
    assert ("Widget.render", "Helper().run") in calls
    assert ("Widget.render", "Helper") in calls
    assert ("Widget.handler", "this.render") in calls
    assert ("make", "Widget") in calls
    bases = {r["target"] for r in facts["refs"] if r["kind"] == "base"}
    assert {"Base", "Shape", "Thing"} <= bases
    hints = {(h["scope"], h["name"]): h["type"] for h in facts["types"]}
    assert hints[("Widget", "store")] == "Store"  # constructor parameter property
    assert ("Widget", "plain") not in hints
    assert ("Widget.render", "depth") not in hints  # `number` is not a class
    assert hints[("plainFunction", "x")] == "Widget"
    assert hints[("plainFunction", "w")] == "Widget"
    assert hints[("", "instance")] == "Widget()"


def test_tests_and_jsx() -> None:
    src = b"""
describe("Cart", () => {
  it("adds", () => { add(1); });
  it("adds", () => { add(2); });
  test(`literal`, () => {});
  it(`dynamic ${x}`, () => {});
});
const view = () => <Panel title="x"><Row /></Panel>;
"""
    facts = parse("tsx", src, is_test=True)
    tests = [d["local"] for d in facts["defs"] if d["kind"] == "test"]
    assert tests == ["Cart > adds", "Cart > adds #2", "Cart > literal"]
    calls = {(r["scope"], r["target"]) for r in facts["refs"] if r["kind"] == "call"}
    assert ("Cart > adds", "add") in calls
    assert ("view", "Panel") in calls and ("view", "Row") in calls
    refs = {r["target"] for r in facts["refs"] if r["kind"] == "ref"}
    assert "Cart" in refs  # describe("Cart") names the thing under test


def test_javascript_and_syntax_errors() -> None:
    facts = parse("javascript", b"function ok() { broken( }\n", is_test=False)
    assert facts["error"] == "syntax errors (partial)"
    assert [d["local"] for d in facts["defs"]] == ["ok"]


def test_dotted_none() -> None:
    assert typescript.dotted(None) is None
    assert typescript.type_text(None) == ""
    assert typescript.value_type(None) == ""


def test_pathological_nesting_is_refused() -> None:
    src = ("x = " + "[" * 1000 + "]" * 1000 + ";").encode()
    facts = parse("javascript", src, is_test=False)
    assert facts["error"] is not None
