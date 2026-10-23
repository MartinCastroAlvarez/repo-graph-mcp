from __future__ import annotations

from pathlib import Path

import pytest

from repo_graph_mcp import config
from repo_graph_mcp.clients import snippet
from repo_graph_mcp.export import export
from repo_graph_mcp.graph import Graph
from tests.conftest import open_graph


def test_mermaid_and_dot_escape_labels(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / 'we"ird<x>').mkdir(parents=True)
    (root / 'we"ird<x>' / "__init__.py").write_text("")
    (root / 'we"ird<x>' / "m.py").write_text("def f():\n    g()\n\ndef g():\n    pass\n")
    graph = open_graph(root)
    try:
        mermaid = export(graph, "mermaid", level="symbol")
        assert '"' not in mermaid.replace('["', "").replace('"]', "")
        assert "#quot;" in mermaid and "#lt;" in mermaid
        assert "n0 --> n1" in mermaid
        dot = export(graph, "dot", level="symbol")
        assert '\\"' in dot
        focused = export(graph, "json", level="module", focus="nothing-matches")
        assert '"nodes": []' in focused
    finally:
        graph.close()


def test_export_validates_arguments(pygraph: Graph) -> None:
    with pytest.raises(ValueError):
        export(pygraph, "svg")
    with pytest.raises(ValueError):
        export(pygraph, "json", level="files")
    small = export(pygraph, "mermaid", max_nodes=2)
    assert small.count("([") == 2
    inherits = export(pygraph, "mermaid", level="symbol")
    assert "-.->" in inherits  # Order inherits Base


def test_config_from_pyproject_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.repo-graph]\nexclude = ["build/**"]\nlanguages = ["python"]\nmax_files = 7\ndocs = false\n'
    )
    cfg = config.load(tmp_path)
    assert cfg.exclude == ("build/**",) and cfg.languages == ("python",)
    assert cfg.max_files == 7 and cfg.docs is False
    assert cfg.db == tmp_path.resolve() / ".repo-graph" / "graph.db"
    monkeypatch.setenv("REPO_GRAPH_MAX_FILES", "9")
    monkeypatch.setenv("REPO_GRAPH_DB", "custom/g.db")
    cfg = config.load(tmp_path)
    assert cfg.max_files == 9 and cfg.db == tmp_path.resolve() / "custom" / "g.db"
    assert config.load(tmp_path, db=str(tmp_path / "x.db")).db == tmp_path / "x.db"
    assert cfg.with_overrides(max_files=None, docs=True).docs is True


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("typo = 1\n", "unknown"),
        ("max_files = true\n", "must be a int"),
        ('languages = ["cobol"]\n', "unknown language"),
        ("max_files = 0\n", "positive"),
        ("[[[\n", "invalid TOML"),
    ],
)
def test_config_errors(tmp_path: Path, text: str, message: str) -> None:
    (tmp_path / ".repo-graph.toml").write_text(text)
    with pytest.raises(config.ConfigError, match=message):
        config.load(tmp_path)


def test_env_must_be_integer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPO_GRAPH_MAX_FILE_BYTES", "lots")
    with pytest.raises(config.ConfigError):
        config.load(tmp_path)


def test_unknown_client() -> None:
    with pytest.raises(ValueError):
        snippet("emacs")


def test_typescript_only_language_filter(tmp_path: Path) -> None:
    (tmp_path / ".repo-graph.toml").write_text('languages = ["typescript"]\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.ts").write_text("export const y = 1;\n")
    (tmp_path / "c.d.ts").write_text("export declare const z: number;\n")
    from repo_graph_mcp.walker import walk

    assert [f[0] for f in walk(config.load(tmp_path)).files] == ["b.ts"]


def test_example_config_is_valid(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parent.parent / "examples" / "repo-graph.toml"
    (tmp_path / ".repo-graph.toml").write_text(example.read_text())
    cfg = config.load(tmp_path)
    assert "vendor/**" in cfg.exclude and cfg.max_files == 20000
