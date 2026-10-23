"""The security contract: read-only, no execution, confined to the root."""

from __future__ import annotations

import ast
import os
import sqlite3
from pathlib import Path

import pytest

from repo_graph_mcp import config
from repo_graph_mcp.graph import Graph
from repo_graph_mcp.graph import GraphError
from repo_graph_mcp.indexer import index
from repo_graph_mcp.paths import PathEscapeError
from repo_graph_mcp.paths import read_bounded
from repo_graph_mcp.paths import safe_join
from repo_graph_mcp.walker import walk

PACKAGE = Path(__file__).resolve().parent.parent / "repo_graph_mcp"
needs_symlinks = pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")


def test_package_never_executes_or_shells_out() -> None:
    banned_calls = {"eval", "exec", "compile", "__import__"}
    banned_modules = {"subprocess", "pickle", "marshal", "shelve", "runpy", "importlib", "yaml"}
    banned_attrs = {"system", "popen", "spawnl", "spawnv", "execv", "execl"}
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in banned_calls, f"{path}: {node.func.id}()"
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
            ):
                assert node.attr not in banned_attrs, f"{path}: os.{node.attr}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned_modules, f"{path}: {alias.name}"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in banned_modules, f"{path}: {node.module}"
            if isinstance(node, ast.keyword) and node.arg == "shell":
                pytest.fail(f"{path}: shell= keyword")


def test_sql_is_never_built_from_strings() -> None:
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"execute", "executemany", "executescript", "query"}
                and node.args
            ):
                first = node.args[0]
                ok = isinstance(first, ast.Constant) or (
                    isinstance(first, ast.Name)
                    and first.id in {"sql", "node_sql", "_SCHEMA", "_FTS"}
                )
                assert ok, f"{path}:{node.lineno}: SQL must be a constant"


def test_safe_join_refuses_escapes(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "ok.py").write_text("")
    assert safe_join(root, "ok.py") == root / "ok.py"
    for bad in ["../etc/passwd", "/etc/passwd", "a/../../x", "", "a\x00b", "..\\..\\x"]:
        with pytest.raises(PathEscapeError):
            safe_join(root, bad)


@needs_symlinks
def test_symlinks_escaping_the_root_are_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("def secret():\n    pass\n")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "real.py").write_text("def real():\n    pass\n")
    (root / "link.py").symlink_to(outside / "secret.py")
    (root / "linkdir").symlink_to(outside, target_is_directory=True)
    (root / "alias.py").symlink_to(root / "real.py")
    result = walk(config.load(root))
    assert [f[0] for f in result.files] == ["real.py"]
    assert result.skipped["symlink outside root"] == 1
    assert result.skipped["symlink"] == 1
    with pytest.raises(PathEscapeError):
        safe_join(root.resolve(), "link.py")
    with pytest.raises(OSError):
        read_bounded(root / "link.py", 1000)  # O_NOFOLLOW


@needs_symlinks
def test_database_is_never_written_through_a_symlink(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / ".repo-graph").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PathEscapeError):
        index(config.load(root))
    assert list(elsewhere.iterdir()) == []

    root2 = tmp_path / "repo2"
    (root2 / ".repo-graph").mkdir(parents=True)
    (root2 / ".repo-graph" / "graph.db").symlink_to(tmp_path / "target.db")
    with pytest.raises(PathEscapeError):
        index(config.load(root2))


def test_source_refuses_paths_outside_root(pygraph: Graph, tmp_path: Path) -> None:
    # Simulate a tampered database pointing outside the root.
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret\n")
    db = pygraph.store.query("PRAGMA database_list")[0]["file"]
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE nodes SET file = ? WHERE qualname = 'shop.models:audit'", ("../secret.txt",)
    )
    conn.commit()
    conn.close()
    with pytest.raises(GraphError) as exc:
        pygraph.source("shop.models:audit")
    assert "root" in str(exc.value)


def test_graph_connection_is_read_only(pygraph: Graph) -> None:
    with pytest.raises(sqlite3.OperationalError):
        pygraph.store.conn.execute("DELETE FROM nodes")


def test_indexing_does_not_modify_repository_files(pyrepo: Path) -> None:
    before = {
        p: p.stat().st_mtime_ns
        for p in pyrepo.rglob("*")
        if p.is_file() and ".repo-graph" not in p.parts
    }
    index(config.load(pyrepo))
    after = {
        p: p.stat().st_mtime_ns
        for p in pyrepo.rglob("*")
        if p.is_file() and ".repo-graph" not in p.parts
    }
    assert before == after


def test_exclude_and_gitignore_off(pyrepo: Path) -> None:
    (pyrepo / ".repo-graph.toml").write_text(
        'exclude = ["shop/evil.py"]\nrespect_gitignore = false\n'
    )
    files = [f[0] for f in walk(config.load(pyrepo)).files]
    assert "shop/evil.py" not in files
    assert "generated/gen.py" in files
