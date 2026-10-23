from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from repo_graph_mcp import config
from repo_graph_mcp.graph import Graph
from repo_graph_mcp.indexer import index

FIXTURES = Path(__file__).parent / "fixtures"


def copy_fixture(name: str, dest: Path) -> Path:
    target = dest / name
    shutil.copytree(FIXTURES / name, target)
    return target


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("REPO_GRAPH_DB", "REPO_GRAPH_MAX_FILE_BYTES", "REPO_GRAPH_MAX_FILES"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def pyrepo(tmp_path: Path) -> Path:
    root = copy_fixture("pyproj", tmp_path)
    # Files the fixture's .gitignore excludes (so git cannot ship them): made here.
    (root / "generated").mkdir()
    (root / "generated" / "gen.py").write_text("def generated():\n    pass\n")
    (root / "shop" / "scratch.tmp.py").write_text("def scratch():\n    pass\n")
    return root


@pytest.fixture
def tsrepo(tmp_path: Path) -> Path:
    pytest.importorskip("tree_sitter")
    return copy_fixture("tsproj", tmp_path)


def open_graph(root: Path) -> Graph:
    cfg = config.load(root)
    index(cfg)
    return Graph.open(cfg.db, cfg.root)


@pytest.fixture
def pygraph(pyrepo: Path) -> Iterator[Graph]:
    graph = open_graph(pyrepo)
    yield graph
    graph.close()


@pytest.fixture
def tsgraph(tsrepo: Path) -> Iterator[Graph]:
    graph = open_graph(tsrepo)
    yield graph
    graph.close()


def ids(items: list[dict[str, object]]) -> list[str]:
    return [str(item["id"]) for item in items]
