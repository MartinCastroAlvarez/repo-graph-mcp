from __future__ import annotations

from pathlib import Path

from repo_graph_mcp.graph import Graph
from tests.conftest import ids


def test_callers_across_es_imports_and_require(tsgraph: Graph) -> None:
    out = tsgraph.callers("slugify")
    assert set(ids(out["callers"])) == {
        "lib/legacy:run",  # const { slugify } = require("../src/util")
        "src/app.test:App > slugifies",
        "src/app:App",
    }


def test_this_and_constructor_parameter_properties(tsgraph: Graph) -> None:
    out = tsgraph.callers("Cache.evict")
    assert set(ids(out["callers"])) == {"src/util:Cache.put", "src/service:Service.warm"}
    callees = tsgraph.callees("App")
    assert set(ids(callees["callees"])) == {
        "src/app:Title",  # <Title /> JSX
        "src/util:slugify",
        "src/util:Cache",  # new Cache()
        "src/util:Cache.put",  # cache typed from `new Cache()`
    }


def test_inherits_and_tests(tsgraph: Graph) -> None:
    rows = tsgraph.store.query(
        "SELECT s.qualname AS s, d.qualname AS d FROM edges e JOIN nodes s ON s.id = e.src "
        "JOIN nodes d ON d.id = e.dst WHERE e.kind = 'inherits'"
    )
    assert [(r["s"], r["d"]) for r in rows] == [("src/util:Cache", "src/util:Store")]
    tests = tsgraph.tests_for("App")
    assert ids(tests["tests"]) == ["src/app.test:App > renders"]
    indirect = tsgraph.tests_for("slugify")
    assert ids(indirect["indirect"]) == ["src/app.test:App > renders"]


def test_modules_and_externals(tsgraph: Graph) -> None:
    deps = tsgraph.dependencies("lib/legacy.js")
    assert ids(deps["imports"]) == ["src/util"]
    assert deps["external"] == ["path"]
    summary = tsgraph.module_summary("src/util.ts")
    assert summary["module"]["doc"] == "Helpers shared by the app."
    assert summary["dependents"] == 4
    slug = next(e for e in summary["outline"] if e["id"] == "src/util:slugify")
    assert slug["doc"] == "Turn a title into a URL slug."
    assert set(ids(tsgraph.dependents("src/util")["dependents"])) == {
        "lib/legacy",
        "src/app",
        "src/app.test",
        "src/service",
    }


def test_impact_of_a_class(tsgraph: Graph) -> None:
    out = tsgraph.impact("Cache")
    affected = set(ids(out["affected"]))
    assert {"src/app:App", "src/service:Service.warm", "src/service:makeService"} <= affected
    assert "src/app.test:App > renders" in ids(out["tests"])


def test_mixed_repository(tmp_path: Path) -> None:
    from tests.conftest import copy_fixture
    from tests.conftest import open_graph

    root = tmp_path / "mixed"
    root.mkdir()
    copy_fixture("pyproj", root)
    copy_fixture("tsproj", root)
    graph = open_graph(root)
    try:
        stats = graph.stats()
        assert set(stats["files"]) == {"python", "typescript", "tsx", "javascript"}
    finally:
        graph.close()
