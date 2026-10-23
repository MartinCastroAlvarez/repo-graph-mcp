from __future__ import annotations

from pathlib import Path

import pytest

from repo_graph_mcp.graph import Graph
from repo_graph_mcp.graph import GraphError
from repo_graph_mcp.graph import dumps
from tests.conftest import ids


def test_never_executes_project_code(pygraph: Graph, pyrepo: Path) -> None:
    assert pygraph.find_symbol("shop.evil")["matches"]
    assert not (pyrepo / "EXECUTED").exists()
    assert not (Path.cwd() / "EXECUTED").exists()


def test_gitignore_is_respected(pygraph: Graph) -> None:
    files = {r["file"] for r in pygraph.store.query("SELECT DISTINCT file FROM nodes")}
    assert "generated/gen.py" not in files
    assert "shop/scratch.tmp.py" not in files
    assert "shop/models.py" in files


def test_parse_errors_are_listed(pygraph: Graph) -> None:
    stats = pygraph.stats()
    assert stats["parse_errors"] and stats["parse_errors"][0].startswith(
        "shop/broken.py: SyntaxError"
    )
    assert stats["files"] == {"python": 7}
    assert stats["fts5"] is True


def test_callees_follow_types_self_super_and_imports(pygraph: Graph) -> None:
    out = pygraph.callees("Checkout.run")
    assert ids(out["callees"]) == [
        "shop.models:Order",
        "shop.models:Order.save",  # order = Order(...); order.save()
        "shop.models:audit",  # models.audit via `from . import models`
        "shop.service:Repo.store",  # self.repo typed through the __init__ parameter
    ]
    assert all("conf" not in c for c in out["callees"])
    assert out["callees"][1]["at"] == 24
    # return-type chain: make_checkout() -> Checkout, .run() -> Order, .total()
    assert ids(pygraph.callees("main")["callees"]) == [
        "shop.models:Order.total",
        "shop.service:Checkout.run",
        "shop.service:make_checkout",
    ]
    assert ids(pygraph.callers("Base.save")["callers"]) == ["shop.models:Order.save"]  # super()
    assert ids(pygraph.callees("Order.total")["callees"]) == ["shop.models:Order._tax"]


def test_callers_with_depth(pygraph: Graph) -> None:
    direct = pygraph.callers("audit")
    assert set(ids(direct["callers"])) == {
        "shop.models:Order.save",
        "shop.service:Repo.store",
        "shop.service:Checkout.run",
    }
    deep = pygraph.callers("audit", depth=3)
    by_id = {c["id"]: c for c in deep["callers"]}
    assert by_id["shop.service:main"]["depth"] == 2
    assert by_id["test_models:TestOrder.test_save"]["depth"] == 2


def test_inherits_and_defines(pygraph: Graph) -> None:
    rows = pygraph.store.query(
        "SELECT s.qualname AS s, d.qualname AS d FROM edges e JOIN nodes s ON s.id = e.src "
        "JOIN nodes d ON d.id = e.dst WHERE e.kind = 'inherits'"
    )
    assert [(r["s"], r["d"]) for r in rows] == [("shop.models:Order", "shop.models:Base")]
    summary = pygraph.module_summary("shop.models")
    order = next(e for e in summary["outline"] if e["id"] == "shop.models:Order")
    assert order["members"][0] == "__init__(self, items: list[int]) -> None:18"
    assert summary["external"] == ["json"]
    assert summary["tests"] == ["test_models"]


def test_dependencies_and_dependents(pygraph: Graph) -> None:
    deps = pygraph.dependencies("shop/service.py")
    assert ids(deps["imports"]) == ["shop", "shop.models"]
    assert deps["external"] == ["__future__"]
    assert "external" not in pygraph.dependencies("shop.service", external=False)
    dependents = pygraph.dependents("shop.models")
    assert set(ids(dependents["dependents"])) == {"shop", "shop.service", "test_models"}
    transitive = pygraph.dependents("shop.models", depth=2)
    assert "test_service" in ids(transitive["dependents"])
    external = pygraph.module_summary("ext:json")
    assert ids(external["imported_by"]) == ["shop.models"]


def test_impact_lists_code_tests_and_files(pygraph: Graph) -> None:
    out = pygraph.impact("audit")
    assert ids(out["affected"]) == [
        "shop.models:Order.save",
        "shop.service:Repo.store",
        "shop.service:Checkout.run",
        "shop.service:main",
    ]
    assert set(ids(out["tests"])) == {
        "test_models:TestOrder.test_save",
        "test_service:test_checkout_flow",
    }
    assert out["files"] == [
        "shop/models.py",
        "shop/service.py",
        "tests/test_models.py",
        "tests/test_service.py",
    ]
    # a class's impact includes the callers of its methods
    order = pygraph.impact("Order", depth=1)
    assert "shop.service:main" in ids(order["affected"])
    assert pygraph.impact("audit", limit=1)["truncated"] is True


def test_tests_for(pygraph: Graph) -> None:
    out = pygraph.tests_for("make_checkout")
    assert set(ids(out["tests"])) == {
        "test_service:test_make_checkout",
        "test_service:test_checkout_flow",
    }
    total = pygraph.tests_for("Order.total")
    assert ids(total["tests"]) == ["test_models:test_total"]
    indirect = pygraph.tests_for("audit")
    assert "test_service:test_checkout_flow" in ids(indirect["indirect"])
    via = {t["id"]: t["via"] for t in indirect["indirect"]}
    assert via["test_service:test_checkout_flow"] == "shop.service:Checkout.run"


def test_path_between(pygraph: Graph) -> None:
    out = pygraph.path_between("main", "audit")
    assert ids(out["path"]) == [
        "shop.service:main",
        "shop.service:Checkout.run",
        "shop.models:audit",
    ]
    assert out["path"][2]["at"] == "shop/service.py:25"
    assert pygraph.path_between("audit", "main")["path"] is None
    assert pygraph.path_between("audit", "main", max_depth=1)["path"] is None


def test_symbol_lookup_forms(pygraph: Graph) -> None:
    assert ids(pygraph.find_symbol("shop.models.Order.total")["matches"]) == [
        "shop.models:Order.total"
    ]
    assert ids(pygraph.find_symbol("shop.models:Order")["matches"]) == ["shop.models:Order"]
    assert ids(pygraph.find_symbol("Order.total")["matches"]) == ["shop.models:Order.total"]
    assert ids(pygraph.find_symbol("shop/models.py:23")["matches"]) == ["shop.models:Order.total"]
    assert ids(pygraph.find_symbol("save", kind="method")["matches"]) == [
        "shop.models:Base.save",
        "shop.models:Order.save",
    ]
    assert pygraph.find_symbol("checkout")["matches"]  # falls back to full-text search
    assert pygraph.find_symbol("   ")["matches"] == []


def test_ambiguous_and_unknown(pygraph: Graph) -> None:
    with pytest.raises(GraphError) as ambiguous:
        pygraph.callers("save")
    assert len(ambiguous.value.candidates) == 2
    assert "ambiguous" in ambiguous.value.as_dict()["error"]
    with pytest.raises(GraphError) as unknown:
        pygraph.callers("does_not_exist_anywhere")
    assert unknown.value.as_dict() == {"error": "no symbol matches 'does_not_exist_anywhere'"}
    with pytest.raises(GraphError):
        pygraph.dependencies("nope.module")


def test_search_is_injection_safe(pygraph: Graph) -> None:
    assert "shop.service:Checkout" in ids(pygraph.search("checkout")["matches"])
    assert ids(pygraph.search("make check")["matches"])[0] == "shop.service:make_checkout"
    assert pygraph.search("audit", kind="function")["matches"][0]["id"] == "shop.models:audit"
    for hostile in ['" OR 1=1 --', "NEAR(a b)", "*", "a' ; DROP TABLE nodes; --", "x AND NOT y"]:
        pygraph.search(hostile)  # never raises, never executes
    assert pygraph.search("")["matches"] == []
    assert pygraph.stats()["nodes"]["module"] > 0  # tables intact


def test_source_reads_only_the_definition(pygraph: Graph, pyrepo: Path) -> None:
    out = pygraph.source("Order.total")
    assert out["lines"] == "21-23"
    assert out["code"].startswith("    def total(self) -> float:")
    assert "stale" not in dumps(out)
    assert pygraph.source("Order.total", context=1)["lines"] == "20-24"
    cut = pygraph.source("Order", max_lines=2)
    assert cut["truncated"] is True and cut["lines"] == "13-14"
    models = pyrepo / "shop" / "models.py"
    models.write_text(models.read_text() + "\n# edited\n")
    assert pygraph.source("Order.total")["stale"] is True
    with pytest.raises(GraphError):
        pygraph.source("ext:json")


def test_dumps_is_compact() -> None:
    assert dumps({"a": None, "b": [1, {"c": None, "d": "é"}]}) == '{"b":[1,{"d":"é"}]}'
