"""Incremental indexing: walk, hash, parse what changed, resolve, store.

A file is re-parsed only when its SHA-256 differs from the stored one (or the
facts format changed). Resolution always runs over every file's facts, because
a change in one file (a renamed function) changes edges in others.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from repo_graph_mcp import __version__
from repo_graph_mcp import parsers
from repo_graph_mcp.config import Config
from repo_graph_mcp.facts import is_test_path
from repo_graph_mcp.paths import PathEscapeError
from repo_graph_mcp.paths import is_within
from repo_graph_mcp.paths import read_bounded
from repo_graph_mcp.resolver import build_graph
from repo_graph_mcp.store import Store
from repo_graph_mcp.walker import walk


@dataclass
class IndexReport:
    files: int = 0
    parsed: int = 0
    reused: int = 0
    removed: int = 0
    nodes: int = 0
    edges: int = 0
    errors: int = 0
    seconds: float = 0.0
    skipped: dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def check_db_location(cfg: Config) -> None:
    """Refuse a database path that is a symlink, or that escapes the root through one.

    A database outside the root is allowed only when configured as an absolute path.
    """
    db = cfg.db
    if db.is_symlink():
        raise PathEscapeError(f"refusing to write through a symlink: {db}")
    parent = db.parent
    if parent.exists() and parent.is_symlink():
        raise PathEscapeError(f"refusing to write through a symlinked directory: {parent}")
    lexical_inside = is_within(cfg.root, Path(*db.parts))
    if lexical_inside and parent.exists() and not is_within(cfg.root, parent.resolve()):
        raise PathEscapeError(f"database directory escapes the root: {parent}")


def _protect_dir(directory: Path) -> None:
    """Keep ``.repo-graph/`` out of git without touching the user's .gitignore."""
    marker = directory / ".gitignore"
    if not marker.exists():
        marker.write_text("# created by repo-graph-mcp\n*\n", encoding="utf-8")


def index(cfg: Config, *, full: bool = False) -> IndexReport:
    """Build or refresh the graph database for ``cfg.root``."""
    started = time.monotonic()
    check_db_location(cfg)
    store = Store.open(cfg.db)
    if is_within(cfg.root, cfg.db.parent.resolve()) and cfg.db.parent != cfg.root:
        _protect_dir(cfg.db.parent)
    report = IndexReport()
    try:
        walked = walk(cfg)
        report.skipped = dict(sorted(walked.skipped.items()))
        report.truncated = walked.truncated
        known = {} if full else store.file_hashes()
        ts_ok = parsers.typescript_available()
        ts_skipped = 0
        seen: set[str] = set()
        with store.transaction():
            for rel, language, size in walked.files:
                if language != "python" and not ts_ok:
                    ts_skipped += 1
                    continue
                try:
                    data = read_bounded(cfg.root / rel, cfg.max_file_bytes)
                except OSError:
                    report.skipped["unreadable"] = report.skipped.get("unreadable", 0) + 1
                    continue
                if data is None:
                    report.skipped["too large"] = report.skipped.get("too large", 0) + 1
                    continue
                seen.add(rel)
                digest = hashlib.sha256(data).hexdigest()
                if known.get(rel) == digest:
                    report.reused += 1
                    continue
                facts = parsers.parse(language, data, is_test=is_test_path(rel), docs=cfg.docs)
                store.upsert_file(rel, digest, language, size, facts)
                report.parsed += 1
            stale = [p for p in store.all_facts() if p not in seen]
            store.delete_files(stale)
            report.removed = len(stale)
            files = store.all_facts()
            graph = build_graph(files)
            store.replace_graph(graph)
            store.set_meta("indexed_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            store.set_meta("tool_version", __version__)
        report.files = len(files)
        report.errors = sum(1 for _, facts in files.values() if facts["error"])
        report.nodes = len(graph.nodes)
        report.edges = len(graph.edges)
        if ts_skipped:
            report.notes.append(
                f"{ts_skipped} TypeScript/JavaScript file(s) skipped: "
                'pip install "repo-graph-mcp[typescript]"'
            )
        if walked.truncated:
            report.notes.append(f"stopped at max_files={cfg.max_files}")
    finally:
        store.close()
    report.seconds = round(time.monotonic() - started, 3)
    return report
