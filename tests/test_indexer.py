from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from repo_graph_mcp import config
from repo_graph_mcp import parsers
from repo_graph_mcp.indexer import index
from repo_graph_mcp.resolver import build_graph
from repo_graph_mcp.store import Store
from repo_graph_mcp.store import words_of


def snapshot(db: Path) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db)
    try:
        nodes = conn.execute("SELECT id, qualname, kind, line FROM nodes ORDER BY id").fetchall()
        edges = conn.execute(
            "SELECT src, dst, kind, line, confidence FROM edges ORDER BY 1, 2, 3"
        ).fetchall()
    finally:
        conn.close()
    return [*nodes, *edges]


def test_incremental_reindex(pyrepo: Path) -> None:
    cfg = config.load(pyrepo)
    first = index(cfg)
    assert (first.files, first.parsed, first.reused) == (7, 7, 0)
    assert first.errors == 1
    before = snapshot(cfg.db)

    again = index(cfg)
    assert (again.parsed, again.reused, again.removed) == (0, 7, 0)
    assert snapshot(cfg.db) == before  # deterministic

    (pyrepo / "shop" / "models.py").write_text(
        (pyrepo / "shop" / "models.py").read_text().replace("def audit(", "def audit_log(")
    )
    (pyrepo / "shop" / "evil.py").unlink()
    changed = index(cfg)
    assert (changed.parsed, changed.reused, changed.removed) == (1, 5, 1)
    store = Store.open(cfg.db, readonly=True)
    try:
        names = {r["qualname"] for r in store.query("SELECT qualname FROM nodes")}
    finally:
        store.close()
    assert "shop.models:audit_log" in names and "shop.models:audit" not in names
    assert "shop.evil" not in names

    full = index(cfg, full=True)
    assert full.parsed == 6 and full.reused == 0


def test_database_dir_is_git_ignored(pyrepo: Path) -> None:
    index(config.load(pyrepo))
    assert (pyrepo / ".repo-graph" / ".gitignore").read_text().strip().endswith("*")


def test_same_graph_from_any_order(pyrepo: Path) -> None:
    files = {}
    for path in sorted(pyrepo.rglob("*.py")):
        rel = path.relative_to(pyrepo).as_posix()
        files[rel] = ("python", parsers.parse("python", path.read_bytes(), is_test="tests/" in rel))
    forward = build_graph(files)
    backward = build_graph(dict(reversed(list(files.items()))))
    assert forward.nodes == backward.nodes
    assert forward.edges == backward.edges


def test_typescript_skipped_without_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.ts").write_text("export function f() {}\n")
    (tmp_path / "b.py").write_text("def g():\n    pass\n")
    monkeypatch.setattr(parsers, "typescript_available", lambda: False)
    report = index(config.load(tmp_path))
    assert report.files == 1
    assert any("repo-graph-mcp[typescript]" in note for note in report.notes)


def test_max_files_and_size_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(5):
        (tmp_path / f"m{i}.py").write_text(f"def f{i}():\n    pass\n")
    (tmp_path / "huge.py").write_text("x = 1\n" * 100)
    monkeypatch.setenv("REPO_GRAPH_MAX_FILES", "3")
    monkeypatch.setenv("REPO_GRAPH_MAX_FILE_BYTES", "200")
    report = index(config.load(tmp_path))
    assert report.files == 3
    assert report.truncated is True
    assert report.skipped["too large"] == 1
    assert any("max_files" in note for note in report.notes)
    assert json.loads(json.dumps(report.as_dict()))["files"] == 3


def test_words_of() -> None:
    assert words_of("FindSymbol", "find_symbol") == "find symbol find symbol"
    assert words_of("HTTPServer") == "http server"
