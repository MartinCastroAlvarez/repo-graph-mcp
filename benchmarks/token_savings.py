"""Token-savings benchmark: graph answers vs. the grep-and-read an agent does without one.

Offline and deterministic. It indexes a repository (this one by default), asks
the graph a fixed set of questions, and compares the size of each answer
(request + JSON response) with what an agent would otherwise put in its
context to answer the same question:

* ``grep -rn <name>`` over the indexed source files, and
* every file it then has to open, read in full.

Tokens are counted with a simple tokenizer (``\\w+`` runs and single
punctuation characters), which is close to, and usually below, what BPE
tokenizers report for source code. No API is called.

    python benchmarks/token_savings.py                 # this repository
    python benchmarks/token_savings.py --root ../other --question callers:handle
    python benchmarks/token_savings.py --min-ratio 3   # exit 1 if savings drop below 3x
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repo_graph_mcp import config
from repo_graph_mcp.graph import Graph
from repo_graph_mcp.graph import dumps
from repo_graph_mcp.indexer import index
from repo_graph_mcp.walker import walk

_TOKEN = re.compile(r"\w+|[^\w\s]")

#: (tool, argument) pairs asked of this repository.
DEFAULT_QUESTIONS = [
    ("find_symbol", "safe_join"),
    ("source", "Graph.tests_for"),
    ("callers", "read_bounded"),
    ("callees", "index"),
    ("impact", "Store.open"),
    ("tests_for", "build_graph"),
    ("module_summary", "repo_graph_mcp/store.py"),
    ("dependents", "repo_graph_mcp.paths"),
    ("path_between", "cli.main->build_graph"),
    ("search", "symlink"),
]


def count_tokens(text: str) -> int:
    return len(_TOKEN.findall(text))


@dataclass
class Row:
    question: str
    graph_tokens: int
    baseline_tokens: int
    files_read: int

    @property
    def ratio(self) -> float:
        return self.baseline_tokens / max(1, self.graph_tokens)


class Baseline:
    """What an agent without the graph reads: grep output plus whole files."""

    def __init__(self, root: Path, files: list[str]) -> None:
        self.root = root
        self.text = {f: (root / f).read_text(encoding="utf-8", errors="replace") for f in files}

    def grep(self, pattern: str) -> tuple[str, list[str]]:
        rx = re.compile(pattern)
        lines, hits = [], []
        for path, text in self.text.items():
            found = False
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    lines.append(f"{path}:{n}:{line}")
                    found = True
            if found:
                hits.append(path)
        return "\n".join(lines), hits

    def read(self, paths: list[str]) -> str:
        return "\n".join(self.text[p] for p in dict.fromkeys(paths) if p in self.text)


def _name(symbol: str) -> str:
    return re.split(r"[.:/]", symbol)[-1]


def _defining_files(answer: dict[str, Any]) -> list[str]:
    loc = answer.get("symbol", answer.get("module", {})).get("loc", "")
    return [loc.rsplit(":", 1)[0]] if loc else []


def _locs(items: list[dict[str, Any]]) -> list[str]:
    return [i["loc"].rsplit(":", 1)[0] for i in items if "loc" in i]


def measure(graph: Graph, base: Baseline, tool: str, arg: str) -> Row:
    asks: dict[str, Callable[[], dict[str, Any]]] = {
        "find_symbol": lambda: graph.find_symbol(arg),
        "source": lambda: graph.source(arg),
        "callers": lambda: graph.callers(arg),
        "callees": lambda: graph.callees(arg),
        "impact": lambda: graph.impact(arg),
        "tests_for": lambda: graph.tests_for(arg),
        "module_summary": lambda: graph.module_summary(arg),
        "dependents": lambda: graph.dependents(arg),
        "search": lambda: graph.search(arg),
        "path_between": lambda: graph.path_between(*arg.split("->")),
    }
    answer = asks[tool]()
    request = json.dumps({"tool": tool, "args": arg})
    graph_tokens = count_tokens(request) + count_tokens(dumps(answer))

    name = _name(arg.split("->")[0])
    if tool in {"find_symbol", "source"}:
        out, _ = base.grep(rf"\b(def|class)\s+{re.escape(name)}\b")
        read = _defining_files(answer) or _locs(answer.get("matches", []))[:1]
    elif tool == "search":
        out, hits = base.grep(re.escape(arg))
        read = hits
    elif tool in {"callers", "callees"}:
        out, hits = base.grep(rf"\b{re.escape(name)}\b")
        read = hits if tool == "callers" else _defining_files(answer) + _locs(answer["callees"])
    elif tool == "impact":
        # grep the symbol, open every hit, then grep the names found there (one more level).
        out, hits = base.grep(rf"\b{re.escape(name)}\b")
        more = [_name(a["id"]) for a in answer["affected"] if a.get("depth", 1) == 1]
        extra_out = [base.grep(rf"\b{re.escape(n)}\b") for n in sorted(set(more))]
        out += "\n".join(o for o, _ in extra_out)
        read = hits + [h for _, hs in extra_out for h in hs]
    elif tool == "tests_for":
        out, hits = base.grep(rf"\b{re.escape(name)}\b")
        read = [h for h in hits if h.startswith("tests/")] + _defining_files(answer)
    elif tool == "module_summary":
        out = ""
        read = _defining_files(answer)
    elif tool == "dependents":
        mod = arg.rsplit(".", 1)[-1]
        out, hits = base.grep(rf"import.*\b{re.escape(mod)}\b|from\s+\S*{re.escape(mod)}\s+import")
        read = []
    elif tool == "path_between":
        out, hits = base.grep(rf"\b{re.escape(name)}\b")
        read = hits + _locs(answer.get("path") or [])
    else:  # pragma: no cover
        raise ValueError(tool)
    baseline_tokens = count_tokens(out) + count_tokens(base.read(read))
    return Row(f"{tool} {arg}", graph_tokens, baseline_tokens, len(set(read)))


def run(root: Path, questions: list[tuple[str, str]]) -> list[Row]:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = config.load(root, db=str(Path(tmp) / "graph.db"))
        index(cfg)
        files = [f for f, _, _ in walk(cfg).files]
        base = Baseline(cfg.root, files)
        graph = Graph.open(cfg.db, cfg.root)
        try:
            return [measure(graph, base, tool, arg) for tool, arg in questions]
        finally:
            graph.close()


def render(rows: list[Row]) -> str:
    lines = [
        "| Question | Graph tokens | grep + read tokens | Files read | Savings |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        lines.append(
            f"| `{r.question}` | {r.graph_tokens:,} | {r.baseline_tokens:,} | {r.files_read} | "
            f"{r.ratio:.1f}x |"
        )
    g = sum(r.graph_tokens for r in rows)
    b = sum(r.baseline_tokens for r in rows)
    lines.append(f"| **Total** | **{g:,}** | **{b:,}** | | **{b / max(1, g):.1f}x** |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--question", action="append", help="tool:argument (repeatable)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--min-ratio", type=float, default=0.0, help="fail below this total")
    args = parser.parse_args(argv)
    questions = (
        [tuple(q.split(":", 1)) for q in args.question] if args.question else DEFAULT_QUESTIONS
    )
    rows = run(Path(args.root), [(t, a) for t, a in questions])
    if args.json:
        print(json.dumps([r.__dict__ | {"ratio": round(r.ratio, 2)} for r in rows], indent=1))
    else:
        print(render(rows))
    total = sum(r.baseline_tokens for r in rows) / max(1, sum(r.graph_tokens for r in rows))
    if total < args.min_ratio:
        print(f"savings {total:.1f}x below --min-ratio {args.min_ratio}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
