from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_graph_mcp.cli import QUERIES
from repo_graph_mcp.cli import main


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_index_and_stats(pyrepo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "index", "--root", str(pyrepo))
    assert code == 0 and out.startswith("indexed 7 files (7 parsed, 0 unchanged, 0 removed)")
    code, out, _ = run(capsys, "index", "--root", str(pyrepo), "--json")
    assert json.loads(out)["reused"] == 7
    code, out, _ = run(capsys, "stats", "--root", str(pyrepo))
    assert json.loads(out)["nodes"]["module"] == 7


def test_every_query_runs(pyrepo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = ["--root", str(pyrepo)]
    calls = {
        "find_symbol": ["Order"],
        "source": ["Order.total"],
        "callers": ["audit", "--depth", "2"],
        "callees": ["Checkout.run"],
        "dependencies": ["shop.service", "--no-external"],
        "dependents": ["shop.models"],
        "impact": ["audit"],
        "tests_for": ["make_checkout"],
        "path_between": ["main", "audit"],
        "module_summary": ["shop/models.py"],
        "search": ["checkout"],
    }
    assert set(calls) == set(QUERIES)
    for tool, args in calls.items():
        code, out, err = run(capsys, "query", *root, tool, *args)
        assert code == 0, (tool, err)
        assert isinstance(json.loads(out), dict)
    code, out, _ = run(capsys, "query", *root, "--pretty", "find_symbol", "audit")
    assert out.startswith("{\n")


def test_errors_and_exit_codes(
    pyrepo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, "query", "--root", str(pyrepo), "callers", "save")
    assert code == 1 and "ambiguous" in err
    code, _, err = run(capsys, "stats", "--root", str(tmp_path / "missing"))
    assert code == 2 and "not a directory" in err
    (pyrepo / ".repo-graph.toml").write_text("bogus = 1\n")
    code, _, err = run(capsys, "index", "--root", str(pyrepo))
    assert code == 2 and "unknown" in err


def test_export_formats(pyrepo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = ["--root", str(pyrepo)]
    code, out, _ = run(capsys, "export", *root)
    assert code == 0 and out.startswith("flowchart LR")
    code, out, _ = run(capsys, "export", *root, "--format", "dot", "--level", "symbol")
    assert out.startswith("digraph repo_graph {")
    target = tmp_path / "graph.json"
    code, _, _ = run(
        capsys, "export", *root, "--format", "json", "--level", "all", "-o", str(target)
    )
    data = json.loads(target.read_text())
    assert {"nodes", "edges"} == set(data)
    code, _, err = run(capsys, "export", *root, "--format", "mermaid", "--level", "all")
    assert code == 2 and "only available as json" in err


@pytest.mark.parametrize("client", ["claude", "cursor", "codex", "opencode"])
def test_config_snippets(client: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "config", client)
    assert code == 0
    assert "repo-graph" in out and "serve" in out


def test_config_snippet_contents(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run(capsys, "config", "claude", "--root", "/srv/app")
    assert "claude mcp add --scope project repo-graph -- repo-graph serve --root /srv/app" in out
    _, out, _ = run(capsys, "config", "codex")
    assert '[mcp_servers.repo-graph]\ncommand = "repo-graph"\nargs = ["serve"]' in out
    _, out, _ = run(capsys, "config", "opencode")
    assert '"type": "local"' in out


def test_query_without_index_builds_it(pyrepo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "query", "--root", str(pyrepo), "find_symbol", "audit")
    assert code == 0 and "shop.models:audit" in out
