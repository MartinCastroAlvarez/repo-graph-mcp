"""Questions about the graph: the logic behind every MCP tool and ``repo-graph query``.

Every answer is a small JSON-able dict. Nodes are shown as
``{"id": qualname, "kind": ..., "loc": "path:line"}`` (plus ``sig`` when known),
so an agent can jump straight to the line instead of reading whole files.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from repo_graph_mcp.paths import PathEscapeError
from repo_graph_mcp.paths import read_bounded
from repo_graph_mcp.paths import safe_join
from repo_graph_mcp.store import Store

MAX_LIMIT = 500
MAX_DEPTH = 6
EDGE_KINDS = ("imports", "defines", "calls", "inherits", "tests", "references")
_KIND_ORDER = {
    "class": 0,
    "function": 1,
    "method": 2,
    "module": 3,
    "symbol": 4,
    "test": 5,
    "external": 6,
}
_TOKEN = re.compile(r"[0-9A-Za-z_]+")
_FILE_LINE = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+)$")


class GraphError(LookupError):
    """A question the graph cannot answer as asked (unknown or ambiguous symbol)."""

    def __init__(self, message: str, candidates: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": str(self)}
        if self.candidates:
            out["candidates"] = self.candidates
        return out


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


class Graph:
    """Read-only view over a graph database."""

    def __init__(self, store: Store, root: Path) -> None:
        self.store = store
        self.root = root.resolve()

    @classmethod
    def open(cls, db: Path, root: Path) -> Graph:
        return cls(Store.open(db, readonly=True), root)

    def close(self) -> None:
        self.store.close()

    # ------------------------------------------------------------------ #
    # rows and formatting
    # ------------------------------------------------------------------ #
    def _row(self, node_id: int) -> sqlite3.Row:
        rows = self.store.query("SELECT * FROM nodes WHERE id = ?", (node_id,))
        return rows[0]

    @staticmethod
    def brief(row: sqlite3.Row, *, sig: bool = True, **extra: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"id": row["qualname"], "kind": row["kind"]}
        if row["file"]:
            out["loc"] = f"{row['file']}:{row['line']}"
        if sig and row["signature"]:
            out["sig"] = row["signature"]
        out.update({k: v for k, v in extra.items() if v is not None})
        return out

    def _sorted(self, rows: Iterable[sqlite3.Row]) -> list[sqlite3.Row]:
        return sorted(
            rows,
            key=lambda r: (r["is_test"], _KIND_ORDER.get(r["kind"], 9), r["file"] or "", r["line"]),
        )

    # ------------------------------------------------------------------ #
    # symbol lookup
    # ------------------------------------------------------------------ #
    def candidates(
        self, symbol: str, kind: str | None = None, limit: int = 20
    ) -> list[sqlite3.Row]:
        """Nodes matching ``symbol``: qualname, dotted name, ``file:line``, suffix, then name."""
        symbol = symbol.strip()
        if not symbol:
            return []
        rows: list[sqlite3.Row] = []
        match = _FILE_LINE.match(symbol)
        if match:
            rows = self.store.query(
                "SELECT * FROM nodes WHERE file = ? AND line <= ? AND end_line >= ? "
                "ORDER BY (end_line - line) ASC LIMIT 1",
                (match["file"], int(match["line"]), int(match["line"])),
            )
        if not rows:
            rows = self.store.query("SELECT * FROM nodes WHERE qualname = ?", (symbol,))
        if not rows:
            rows = self.store.query("SELECT * FROM nodes WHERE dotted = ?", (symbol,))
        if not rows:
            rows = self.store.query(
                "SELECT * FROM nodes WHERE file = ? AND kind = 'module'", (symbol,)
            )
        if not rows and ("." in symbol or "/" in symbol):
            dotted = symbol.replace(":", ".")
            pattern = "%" + _escape_like("." + dotted)
            rows = self.store.query(
                "SELECT * FROM nodes WHERE dotted LIKE ? ESCAPE '\\' OR dotted LIKE ? ESCAPE '\\' "
                "LIMIT 200",
                (pattern, "%" + _escape_like("/" + dotted)),
            )
        if not rows:
            rows = self.store.query("SELECT * FROM nodes WHERE name = ? LIMIT 200", (symbol,))
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        return self._sorted(rows)[: _clamp(limit, 1, MAX_LIMIT)]

    def one(self, symbol: str) -> sqlite3.Row:
        rows = self.candidates(symbol, limit=21)
        if not rows:
            hint = [self.brief(r) for r in self._fts(symbol, None, 5)]
            raise GraphError(f"no symbol matches {symbol!r}", hint)
        if len(rows) > 1:
            exact = [r for r in rows if r["name"] == symbol and not r["is_test"]]
            if len(exact) == 1:
                return exact[0]
            raise GraphError(
                f"{symbol!r} is ambiguous; pass one of the ids", [self.brief(r) for r in rows[:20]]
            )
        return rows[0]

    # ------------------------------------------------------------------ #
    # edges
    # ------------------------------------------------------------------ #
    def _out(self, node_id: int, kinds: Iterable[str]) -> list[sqlite3.Row]:
        wanted = set(kinds)
        rows = self.store.query(
            "SELECT dst AS other, kind, line, confidence FROM edges WHERE src = ? ORDER BY dst",
            (node_id,),
        )
        return [r for r in rows if r["kind"] in wanted]

    def _in(self, node_id: int, kinds: Iterable[str]) -> list[sqlite3.Row]:
        wanted = set(kinds)
        rows = self.store.query(
            "SELECT src AS other, kind, line, confidence FROM edges WHERE dst = ? ORDER BY src",
            (node_id,),
        )
        return [r for r in rows if r["kind"] in wanted]

    def _walk(
        self,
        start: Iterable[int],
        kinds: Iterable[str],
        *,
        reverse: bool,
        depth: int,
        limit: int,
    ) -> tuple[list[tuple[int, int, sqlite3.Row, int]], bool]:
        """BFS. Returns ``[(node_id, depth, edge_row, via_id)]`` and a truncated flag."""
        kinds = tuple(kinds)
        seen = set(start)
        queue = deque((n, 0) for n in start)
        found: list[tuple[int, int, sqlite3.Row, int]] = []
        while queue:
            node_id, d = queue.popleft()
            if d >= depth:
                continue
            edges = self._in(node_id, kinds) if reverse else self._out(node_id, kinds)
            for edge in edges:
                other = edge["other"]
                if other in seen:
                    continue
                seen.add(other)
                found.append((other, d + 1, edge, node_id))
                if len(found) >= limit:
                    return found, True
                queue.append((other, d + 1))
        return found, False

    def _members(self, node_id: int) -> list[int]:
        """The node plus everything it defines (a class's methods, a module's functions)."""
        found, _ = self._walk([node_id], ["defines"], reverse=False, depth=MAX_DEPTH, limit=5000)
        return [node_id, *(n for n, *_ in found)]

    def _site(self, edge: sqlite3.Row, caller: sqlite3.Row) -> str | None:
        return f"{caller['file']}:{edge['line']}" if caller["file"] else None

    @staticmethod
    def _conf(edge: sqlite3.Row) -> str | None:
        return None if edge["confidence"] == "exact" else edge["confidence"]

    # ------------------------------------------------------------------ #
    # the tools
    # ------------------------------------------------------------------ #
    def find_symbol(self, name: str, kind: str | None = None, limit: int = 20) -> dict[str, Any]:
        rows = self.candidates(name, kind, limit)
        if not rows:
            rows = self._fts(name, kind, limit)
        return {
            "matches": [
                self.brief(r, end=r["end_line"] if r["file"] else None, doc=r["doc"] or None)
                for r in rows
            ]
        }

    def callers(self, symbol: str, depth: int = 1, limit: int = 50) -> dict[str, Any]:
        return self._calls(symbol, depth, limit, reverse=True)

    def callees(self, symbol: str, depth: int = 1, limit: int = 50) -> dict[str, Any]:
        return self._calls(symbol, depth, limit, reverse=False)

    def _calls(self, symbol: str, depth: int, limit: int, *, reverse: bool) -> dict[str, Any]:
        node = self.one(symbol)
        found, truncated = self._walk(
            [node["id"]],
            ["calls"],
            reverse=reverse,
            depth=_clamp(depth, 1, MAX_DEPTH),
            limit=_clamp(limit, 1, MAX_LIMIT),
        )
        items = []
        for other_id, d, edge, _ in found:
            other = self._row(other_id)
            items.append(
                self.brief(
                    other,
                    sig=False,
                    at=edge["line"],
                    depth=d if depth > 1 else None,
                    conf=self._conf(edge),
                )
            )
        key = "callers" if reverse else "callees"
        # `at` is the line of the call, in the calling function's file.
        return {"symbol": self.brief(node), key: items, "truncated": truncated or None}

    def _module(self, module: str) -> sqlite3.Row:
        rows = [r for r in self.candidates(module, limit=50) if r["kind"] in {"module", "external"}]
        if not rows:
            raise GraphError(f"no module matches {module!r}")
        if len(rows) > 1:
            raise GraphError(f"{module!r} is ambiguous", [self.brief(r) for r in rows])
        return rows[0]

    def dependencies(self, module: str, external: bool = True) -> dict[str, Any]:
        node = self._module(module)
        internal, ext = [], []
        for edge in self._out(node["id"], ["imports"]):
            other = self._row(edge["other"])
            if other["kind"] == "external":
                ext.append(other["name"])
            else:
                internal.append(self.brief(other, sig=False, at=edge["line"]))
        out: dict[str, Any] = {"module": self.brief(node), "imports": internal}
        if external:
            out["external"] = sorted(set(ext))
        return out

    def dependents(self, module: str, depth: int = 1, limit: int = 100) -> dict[str, Any]:
        node = self._module(module)
        found, truncated = self._walk(
            [node["id"]],
            ["imports"],
            reverse=True,
            depth=_clamp(depth, 1, MAX_DEPTH),
            limit=_clamp(limit, 1, MAX_LIMIT),
        )
        items = [
            self.brief(self._row(n), sig=False, depth=d if depth > 1 else None)
            for n, d, _, _ in found
        ]
        return {"module": self.brief(node), "dependents": items, "truncated": truncated or None}

    def impact(self, symbol: str, depth: int = 3, limit: int = 100) -> dict[str, Any]:
        """Blast radius: what (transitively) calls, references, inherits or imports it."""
        node = self.one(symbol)
        seeds = self._members(node["id"])
        kinds = ["calls", "references", "inherits", "imports"]
        found, truncated = self._walk(
            seeds,
            kinds,
            reverse=True,
            depth=_clamp(depth, 1, MAX_DEPTH),
            limit=_clamp(limit, 1, MAX_LIMIT),
        )
        affected, tests, files = [], {}, set()
        if node["file"]:
            files.add(node["file"])
        reached = set(seeds)
        for other_id, d, edge, _ in found:
            other = self._row(other_id)
            reached.add(other_id)
            if other["file"]:
                files.add(other["file"])
            entry = self.brief(other, sig=False, depth=d, via=edge["kind"], conf=self._conf(edge))
            if other["is_test"]:
                if other["kind"] == "test":
                    tests[other["qualname"]] = self.brief(other, sig=False)
                continue
            affected.append(entry)
        for target in sorted(reached):
            for edge in self._in(target, ["tests"]):
                test = self._row(edge["other"])
                tests.setdefault(test["qualname"], self.brief(test, sig=False))
        return {
            "symbol": self.brief(node),
            "affected": affected,
            "tests": sorted(tests.values(), key=lambda t: t.get("loc", "")),
            "files": sorted(files),
            "truncated": truncated or None,
        }

    def tests_for(self, symbol: str, depth: int = 3, limit: int = 50) -> dict[str, Any]:
        """Tests that exercise ``symbol``: linked directly, or reaching it through calls."""
        node = self.one(symbol)
        limit = _clamp(limit, 1, MAX_LIMIT)
        seeds = self._members(node["id"])
        direct: dict[str, dict[str, Any]] = {}
        for target in seeds:
            for edge in self._in(target, ["tests"]):
                test = self._row(edge["other"])
                direct.setdefault(
                    test["qualname"], self.brief(test, sig=False, conf=self._conf(edge))
                )
        indirect: dict[str, dict[str, Any]] = {}
        found, truncated = self._walk(
            seeds, ["calls"], reverse=True, depth=_clamp(depth, 1, MAX_DEPTH), limit=2000
        )
        for other_id, d, _, via in found:
            other = self._row(other_id)
            if other["kind"] == "test" and other["qualname"] not in direct:
                indirect.setdefault(
                    other["qualname"],
                    self.brief(other, sig=False, depth=d, via=self._row(via)["qualname"]),
                )
        return {
            "symbol": self.brief(node),
            "tests": list(direct.values())[:limit],
            "indirect": list(indirect.values())[:limit],
            "truncated": truncated or None,
        }

    def path_between(
        self, source: str, target: str, max_depth: int = 8, kinds: list[str] | None = None
    ) -> dict[str, Any]:
        """Shortest directed path from ``source`` to ``target`` over the given edge kinds."""
        src, dst = self.one(source), self.one(target)
        use = [
            k for k in (kinds or ["calls", "imports", "references", "inherits"]) if k in EDGE_KINDS
        ]
        max_depth = _clamp(max_depth, 1, 12)
        parent: dict[int, tuple[int, sqlite3.Row] | None] = {src["id"]: None}
        queue = deque([(src["id"], 0)])
        while queue:
            node_id, d = queue.popleft()
            if node_id == dst["id"]:
                break
            if d >= max_depth:
                continue
            for edge in self._out(node_id, use):
                if edge["other"] not in parent:
                    parent[edge["other"]] = (node_id, edge)
                    queue.append((edge["other"], d + 1))
        if dst["id"] not in parent:
            return {"from": self.brief(src), "to": self.brief(dst), "path": None}
        hops: list[dict[str, Any]] = []
        cursor: int = dst["id"]
        while True:
            step = parent[cursor]
            row = self._row(cursor)
            if step is None:
                hops.append(self.brief(row, sig=False))
                break
            prev, edge = step
            hops.append(
                self.brief(row, sig=False, edge=edge["kind"], at=self._site(edge, self._row(prev)))
            )
            cursor = prev
        hops.reverse()
        return {"from": self.brief(src), "to": self.brief(dst), "path": hops}

    def module_summary(self, module: str) -> dict[str, Any]:
        """A module's outline (classes with methods, functions, symbols) and its neighbours."""
        node = self._module(module)
        if node["kind"] == "external":
            users = [self.brief(self._row(e["other"])) for e in self._in(node["id"], ["imports"])]
            return {"module": self.brief(node), "imported_by": users}
        outline: list[dict[str, Any]] = []
        for edge in self._out(node["id"], ["defines"]):
            child = self._row(edge["other"])
            entry = self.brief(child, doc=child["doc"] or None)
            if child["kind"] == "class":
                methods = [self._row(e["other"]) for e in self._out(child["id"], ["defines"])]
                entry["members"] = [
                    f"{m['name']}{m['signature'] if m['kind'] != 'test' else ''}:{m['line']}"
                    for m in sorted(methods, key=lambda m: m["line"])
                ]
            outline.append(entry)
        outline.sort(key=lambda e: int(e.get("loc", ":0").rsplit(":", 1)[1]))
        deps = self.dependencies(module)
        dependents = self._in(node["id"], ["imports"])
        tests = sorted({self._row(e["other"])["qualname"] for e in self._in(node["id"], ["tests"])})
        return {
            "module": self.brief(node, doc=node["doc"] or None, lines=node["end_line"]),
            "outline": outline,
            "imports": [d["id"] for d in deps["imports"]],
            "external": deps.get("external", []),
            "dependents": len(dependents),
            "tests": tests or None,
        }

    def search(self, query: str, kind: str | None = None, limit: int = 20) -> dict[str, Any]:
        rows = self._fts(query, kind, _clamp(limit, 1, MAX_LIMIT))
        return {"matches": [self.brief(r, doc=r["doc"] or None) for r in rows]}

    def _fts(self, query: str, kind: str | None, limit: int) -> list[sqlite3.Row]:
        tokens = [t.lower() for t in _TOKEN.findall(query)][:12]
        words: list[str] = []
        for token in tokens:
            words.extend(w for w in re.split(r"_+", token) if w)
        if not words:
            return []
        rows: list[sqlite3.Row]
        if self.store.fts:
            # Each word is quoted, so FTS5 operators in the input are inert text.
            match = " ".join('"' + w.replace('"', "") + '"*' for w in words)
            rows = self.store.query(
                "SELECT nodes.* FROM nodes_fts JOIN nodes ON nodes.id = nodes_fts.rowid "
                "WHERE nodes_fts MATCH ? "
                "ORDER BY bm25(nodes_fts, 10.0, 5.0, 2.0, 1.0), nodes.is_test "
                "LIMIT ?",
                (match, MAX_LIMIT),
            )
        else:  # pragma: no cover - SQLite without FTS5
            rows = self.store.query(
                "SELECT * FROM nodes WHERE words LIKE ? ESCAPE '\\' LIMIT ?",
                ("%" + _escape_like(words[0]) + "%", MAX_LIMIT),
            )
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        return rows[:limit]

    def source(self, symbol: str, context: int = 0, max_lines: int = 200) -> dict[str, Any]:
        """The source lines of one definition, read from disk inside the root only."""
        node = self.one(symbol)
        if not node["file"]:
            raise GraphError(f"{node['qualname']} has no source in this repository")
        try:
            path = safe_join(self.root, node["file"])
        except PathEscapeError as exc:
            raise GraphError(str(exc)) from exc
        if path.is_symlink():
            raise GraphError("refusing to read through a symlink")
        try:
            data = read_bounded(path, 2_000_000)
        except OSError as exc:
            raise GraphError(f"cannot read {node['file']}: {exc.strerror}") from exc
        if data is None:
            raise GraphError(f"{node['file']} is too large")
        stored = self.store.query("SELECT sha256 FROM files WHERE path = ?", (node["file"],))
        stale = bool(stored) and stored[0]["sha256"] != hashlib.sha256(data).hexdigest()
        lines = data.decode("utf-8", errors="replace").splitlines()
        context = _clamp(context, 0, 50)
        start = max(1, node["line"] - context)
        end = min(len(lines), node["end_line"] + context)
        cut = end - start + 1 > max_lines
        end = min(end, start + _clamp(max_lines, 1, 2000) - 1)
        return {
            "symbol": self.brief(node),
            "lines": f"{start}-{end}",
            "code": "\n".join(lines[start - 1 : end]),
            "truncated": cut or None,
            "stale": stale or None,
        }

    def stats(self) -> dict[str, Any]:
        def counts(sql: str) -> dict[str, int]:
            return {str(r[0]): int(r[1]) for r in self.store.query(sql)}

        errors = self.store.query(
            "SELECT path, error FROM files WHERE error IS NOT NULL ORDER BY path LIMIT 20"
        )
        return {
            "root": str(self.root),
            "indexed_at": self.store.get_meta("indexed_at"),
            "files": counts(
                "SELECT language, COUNT(*) FROM files GROUP BY language ORDER BY language"
            ),
            "nodes": counts("SELECT kind, COUNT(*) FROM nodes GROUP BY kind ORDER BY kind"),
            "edges": counts("SELECT kind, COUNT(*) FROM edges GROUP BY kind ORDER BY kind"),
            "name_matched_edges": int(
                self.store.query("SELECT COUNT(*) FROM edges WHERE confidence != 'exact'")[0][0]
            ),
            "parse_errors": [f"{r['path']}: {r['error']}" for r in errors] or None,
            "fts5": self.store.fts,
        }


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def prune(value: Any) -> Any:
    """Drop ``None`` values recursively (keeps answers small)."""
    if isinstance(value, dict):
        return {k: prune(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [prune(v) for v in value]
    return value


def dumps(value: Any) -> str:
    """Compact JSON: no ``None`` fields, no whitespace."""
    return json.dumps(prune(value), separators=(",", ":"), ensure_ascii=False)
