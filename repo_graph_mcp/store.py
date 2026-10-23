"""SQLite storage for the graph (``.repo-graph/graph.db``).

Every statement is a constant string with ``?`` placeholders; no SQL is ever
built from input. The full-text index is SQLite FTS5 (``nodes_fts``), with a
plain ``LIKE`` fallback for builds without FTS5.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from collections.abc import Iterator
from contextlib import contextmanager
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote

from repo_graph_mcp.facts import FACTS_VERSION
from repo_graph_mcp.facts import Facts
from repo_graph_mcp.resolver import Graph

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    path      TEXT PRIMARY KEY,
    sha256    TEXT NOT NULL,
    language  TEXT NOT NULL,
    size      INTEGER NOT NULL,
    facts     TEXT NOT NULL,
    error     TEXT
);
CREATE TABLE IF NOT EXISTS nodes (
    id         INTEGER PRIMARY KEY,
    qualname   TEXT NOT NULL UNIQUE,
    dotted     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL,
    file       TEXT,
    line       INTEGER NOT NULL,
    end_line   INTEGER NOT NULL,
    signature  TEXT NOT NULL,
    doc        TEXT NOT NULL,
    is_test    INTEGER NOT NULL,
    language   TEXT NOT NULL,
    words      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS nodes_dotted ON nodes(dotted);
CREATE INDEX IF NOT EXISTS nodes_file ON nodes(file);
CREATE TABLE IF NOT EXISTS edges (
    src        INTEGER NOT NULL,
    dst        INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    line       INTEGER NOT NULL,
    confidence TEXT NOT NULL,
    PRIMARY KEY (src, dst, kind)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, kind);
"""

_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    name, words, qualname, doc, content='nodes', content_rowid='id'
);
"""

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SPLIT = re.compile(r"[^0-9A-Za-z]+")


def words_of(*texts: str) -> str:
    """``"FindSymbol find_symbol"`` -> ``"find symbol find symbol"`` (for full-text search)."""
    out: list[str] = []
    for text in texts:
        for chunk in _SPLIT.split(text):
            out.extend(w.lower() for w in _CAMEL.split(chunk) if w)
    return " ".join(out)


def dotted_of(qualname: str, language: str) -> str:
    return qualname.replace(":", ".") if language == "python" else qualname.replace(":", ".", 1)


class Store:
    """A thin, typed wrapper over the SQLite file."""

    def __init__(self, conn: sqlite3.Connection, *, readonly: bool) -> None:
        self.conn = conn
        self.readonly = readonly
        self.conn.row_factory = sqlite3.Row
        self.fts = self._has_fts()

    # -- lifecycle -------------------------------------------------------- #
    @classmethod
    def open(cls, path: Path, *, readonly: bool = False) -> Store:
        if readonly:
            if not path.is_file():
                raise FileNotFoundError(f"no graph at {path}; run `repo-graph index` first")
            uri = f"file:{quote(str(path))}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            return cls(conn, readonly=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        with suppress(sqlite3.OperationalError):  # SQLite built without FTS5
            conn.executescript(_FTS)
        store = cls(conn, readonly=False)
        if store.get_meta("schema") not in (None, str(SCHEMA_VERSION)):  # pragma: no cover
            raise RuntimeError(f"{path} has an unknown schema; delete it and re-index")
        store.set_meta("schema", str(SCHEMA_VERSION))
        conn.commit()
        return store

    def close(self) -> None:
        self.conn.close()

    def _has_fts(self) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'nodes_fts'"
        ).fetchone()
        return row is not None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
        except BaseException:
            self.conn.rollback()
            raise
        self.conn.commit()

    # -- meta ------------------------------------------------------------- #
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # -- files ------------------------------------------------------------ #
    def file_hashes(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT path, sha256, facts FROM files").fetchall()
        out: dict[str, str] = {}
        for row in rows:
            facts = json.loads(row["facts"])
            if facts.get("version") == FACTS_VERSION:
                out[row["path"]] = row["sha256"]
        return out

    def all_facts(self) -> dict[str, tuple[str, Facts]]:
        rows = self.conn.execute("SELECT path, language, facts FROM files ORDER BY path").fetchall()
        return {row["path"]: (row["language"], json.loads(row["facts"])) for row in rows}

    def upsert_file(self, path: str, sha256: str, language: str, size: int, facts: Facts) -> None:
        self.conn.execute(
            "INSERT INTO files(path, sha256, language, size, facts, error) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET sha256 = excluded.sha256, "
            "language = excluded.language, "
            "size = excluded.size, facts = excluded.facts, error = excluded.error",
            (
                path,
                sha256,
                language,
                size,
                json.dumps(facts, separators=(",", ":")),
                facts["error"],
            ),
        )

    def delete_files(self, paths: Iterable[str]) -> None:
        self.conn.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in paths])

    # -- graph ------------------------------------------------------------ #
    def replace_graph(self, graph: Graph) -> None:
        """Swap in a freshly resolved graph (ids are assigned in a stable order)."""
        self.conn.execute("DELETE FROM edges")
        self.conn.execute("DELETE FROM nodes")
        ordered = sorted(
            graph.nodes.values(), key=lambda n: (n.file is None, n.file or "", n.line, n.qualname)
        )
        ids: dict[str, int] = {}
        rows = []
        for i, node in enumerate(ordered, start=1):
            ids[node.qualname] = i
            rows.append(
                (
                    i,
                    node.qualname,
                    dotted_of(node.qualname, node.language or "python"),
                    node.kind,
                    node.name,
                    node.file,
                    node.line,
                    node.end_line,
                    node.signature,
                    node.doc,
                    int(node.is_test),
                    node.language,
                    words_of(node.name, node.qualname),
                )
            )
        self.conn.executemany(
            "INSERT INTO nodes(id, qualname, dotted, kind, name, file, line, end_line, signature, "
            "doc, is_test, language, words) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.executemany(
            "INSERT INTO edges(src, dst, kind, line, confidence) VALUES (?, ?, ?, ?, ?)",
            sorted(
                (ids[src], ids[dst], kind, line, conf)
                for (src, dst, kind), (line, conf) in graph.edges.items()
                if src in ids and dst in ids
            ),
        )
        if self.fts:
            self.conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES ('rebuild')")

    # -- reads ------------------------------------------------------------ #
    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Run one of this package's constant read statements."""
        return self.conn.execute(sql, params).fetchall()
