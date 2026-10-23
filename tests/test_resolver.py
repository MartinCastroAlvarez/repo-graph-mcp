from __future__ import annotations

import importlib.util

import pytest

from repo_graph_mcp.facts import is_test_path
from repo_graph_mcp.parsers import parse
from repo_graph_mcp.resolver import Graph
from repo_graph_mcp.resolver import build_graph
from repo_graph_mcp.walker import language_of


def graph_of(sources: dict[str, str]) -> Graph:
    files = {}
    for path, text in sources.items():
        language = language_of(path)
        assert language is not None
        files[path] = (language, parse(language, text.encode(), is_test=is_test_path(path)))
    return build_graph(files)


def edges(graph: Graph, kind: str) -> set[tuple[str, str]]:
    return {(s, d) for (s, d, k) in graph.edges if k == kind}


def confidence(graph: Graph, src: str, dst: str, kind: str = "calls") -> str:
    return graph.edges[(src, dst, kind)][1]


def test_src_layout_reexports_and_star_imports() -> None:
    g = graph_of(
        {
            "src/pkg/__init__.py": "from .core import engine\nfrom .helpers import *\n",
            "src/pkg/core.py": "def engine():\n    pass\n",
            "src/pkg/helpers.py": "def helper():\n    pass\n",
            "src/pkg/sub/__init__.py": "",
            "src/pkg/sub/deep.py": "from ... import pkg\nfrom ..core import engine as e\n\ndef go():\n    e()\n",
            "app.py": "import pkg\nfrom pkg import helper\n\ndef main():\n    pkg.engine()\n    helper()\n",
        }
    )
    assert "pkg.core" in g.nodes and "pkg.sub.deep" in g.nodes  # src/ is not a package
    calls = edges(g, "calls")
    assert ("app:main", "pkg.core:engine") in calls  # through the __init__ re-export
    assert ("app:main", "pkg.helpers:helper") in calls  # through the star import
    assert ("pkg.sub.deep:go", "pkg.core:engine") in calls  # relative import with alias
    assert ("app", "pkg") in edges(g, "imports")


def test_submodule_import_and_attribute_chain() -> None:
    g = graph_of(
        {
            "lib/__init__.py": "",
            "lib/io.py": "class Reader:\n    def read_all(self):\n        pass\n",
            "use.py": (
                "import lib.io\nfrom lib import io as lio\n\n"
                "def a():\n    lib.io.Reader().read_all()\n\n"
                "def b():\n    lio.Reader.read_all(None)\n"
            ),
        }
    )
    calls = edges(g, "calls")
    assert ("use:a", "lib.io:Reader.read_all") in calls
    assert ("use:b", "lib.io:Reader.read_all") in calls
    assert ("use", "lib.io") in edges(g, "imports")


def test_name_matching_is_conservative() -> None:
    g = graph_of(
        {
            "a.py": "class Engine:\n    def ignite(self):\n        pass\n    def get(self):\n        pass\n",
            "b.py": (
                "import os\n\n"
                "def run(thing):\n    thing.ignite()\n    thing.get()\n    os.path.ignite()\n"
                "    str(thing).ignite()\n"
            ),
        }
    )
    calls = edges(g, "calls")
    assert ("b:run", "a:Engine.ignite") in calls
    assert confidence(g, "b:run", "a:Engine.ignite") == "name"
    assert ("b:run", "a:Engine.get") not in calls  # generic names are never guessed


def test_ambiguous_names_are_not_guessed() -> None:
    g = graph_of(
        {
            "a.py": "class A:\n    def launch(self):\n        pass\n",
            "b.py": "class B:\n    def launch(self):\n        pass\n",
            "c.py": "def go(x):\n    x.launch()\n",
        }
    )
    assert not [e for e in edges(g, "calls") if e[0] == "c:go"]


def test_inheritance_chain_and_super() -> None:
    g = graph_of(
        {
            "base.py": "class Root:\n    def hello(self):\n        pass\n",
            "mid.py": "from base import Root\n\nclass Mid(Root):\n    pass\n",
            "leaf.py": (
                "import mid\n\nclass Leaf(mid.Mid):\n    def hello(self):\n        super().hello()\n"
                "    def other(self):\n        self.hello()\n\nclass Ext(dict):\n    pass\n"
            ),
        }
    )
    assert ("leaf:Leaf", "mid:Mid") in edges(g, "inherits")
    assert ("mid:Mid", "base:Root") in edges(g, "inherits")
    calls = edges(g, "calls")
    assert ("leaf:Leaf.hello", "base:Root.hello") in calls  # super() through Mid
    assert ("leaf:Leaf.other", "leaf:Leaf.hello") in calls
    assert not [e for e in edges(g, "inherits") if e[0] == "leaf:Ext"]
    assert "ext:mid" not in g.nodes


def test_unresolved_base_by_unique_name() -> None:
    g = graph_of(
        {
            "models.py": "class Model:\n    pass\n",
            "user.py": "from somewhere import thing\n\nclass User(orm.Model):\n    pass\n",
        }
    )
    assert confidence(g, "user:User", "models:Model", "inherits") == "name"


def test_module_name_collisions_get_full_paths() -> None:
    g = graph_of(
        {
            "scripts/tool.py": "def a():\n    pass\n",
            "other/tool.py": "def b():\n    pass\n",
            "stubs.pyi": "def s() -> int: ...\n",
            "stubs.py": "def s():\n    return 1\n",
        }
    )
    assert {"tool", "scripts.tool"} <= set(g.nodes) or {"tool", "other.tool"} <= set(g.nodes)
    assert "stubs:s" in g.nodes


def test_test_name_heuristic() -> None:
    g = graph_of(
        {
            "calc.py": "def add_numbers(a, b):\n    return a + b\n",
            "tests/test_calc.py": "import calc\n\ndef test_add_numbers_negative():\n    assert True\n",
        }
    )
    assert (
        confidence(g, "test_calc:test_add_numbers_negative", "calc:add_numbers", "tests") == "name"
    )
    assert ("test_calc", "calc") in edges(g, "tests")


def test_external_nodes_and_symbol_references() -> None:
    g = graph_of(
        {
            "conf.py": "LIMIT = 3\n",
            "use.py": "import requests\nfrom conf import LIMIT\n\ndef f():\n    requests.get(LIMIT)\n",
        }
    )
    assert g.nodes["ext:requests"].kind == "external"
    assert ("use:f", "conf:LIMIT") in edges(g, "references")
    assert not [e for e in edges(g, "calls") if e[1].startswith("ext:")]


@pytest.mark.skipif(importlib.util.find_spec("tree_sitter") is None, reason="typescript extra")
def test_typescript_resolution_details() -> None:
    g = graph_of(
        {
            "src/widgets/index.ts": 'export { Button } from "./button";\n',
            "src/widgets/button.ts": "export class Button { click(): void {} }\n",
            "src/page.ts": (
                'import { Button } from "./widgets";\nimport Card from "./card.js";\n'
                'import * as lodash from "lodash";\nimport { x } from "@scope/pkg/sub";\n\n'
                "export function render(): Button {\n  const b = new Button();\n  b.click();\n"
                "  lodash.map([]);\n  return b;\n}\n"
                "export function use() {\n  render().click();\n  Card();\n}\n"
            ),
            "src/card.ts": "export default function Card() {}\n",
        }
    )
    calls = edges(g, "calls")
    assert ("src/page:render", "src/widgets/button:Button.click") in calls  # via index.ts re-export
    assert ("src/page:use", "src/widgets/button:Button.click") in calls  # return type
    assert ("src/page:use", "src/card:Card") in calls  # default import, ./card.js -> card.ts
    assert "ext:lodash" in g.nodes and "ext:@scope/pkg" in g.nodes
    assert ("src/page", "src/widgets/index") in edges(g, "imports")
