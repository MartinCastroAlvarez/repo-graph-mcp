"""Walk the repository: which files get indexed, in a deterministic order.

* ``.gitignore`` files are honoured (nested ones too, gitwildmatch semantics).
* :data:`config.ALWAYS_SKIPPED` directories and ``exclude`` globs are skipped.
* Symlinks are never followed. A symlinked file whose target is inside the
  root is indexed once, under its real path; one pointing outside is refused.
* Files larger than ``max_file_bytes`` are skipped; the walk stops at ``max_files``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import pathspec

from repo_graph_mcp.config import ALWAYS_SKIPPED
from repo_graph_mcp.config import Config
from repo_graph_mcp.paths import is_within

PYTHON_EXTENSIONS = {".py": "python", ".pyi": "python"}
TS_EXTENSIONS = {
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}


def language_of(path: str) -> str | None:
    """Parser language for a file name, or ``None`` when it is not source we parse."""
    if path.endswith(".d.ts"):
        return None
    ext = os.path.splitext(path)[1].lower()
    return PYTHON_EXTENSIONS.get(ext) or TS_EXTENSIONS.get(ext)


@dataclass
class WalkResult:
    """Files to index plus what was skipped and why (for ``stats``)."""

    files: list[tuple[str, str, int]] = field(default_factory=list)  # (relpath, language, size)
    skipped: dict[str, int] = field(default_factory=dict)
    truncated: bool = False

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _load_gitignore(directory: Path) -> pathspec.GitIgnoreSpec | None:
    path = directory / ".gitignore"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return pathspec.GitIgnoreSpec.from_lines(lines)


def walk(cfg: Config) -> WalkResult:
    """Return the files under ``cfg.root`` that should be indexed."""
    root = cfg.root
    result = WalkResult()
    wanted = {"python"} if "python" in cfg.languages else set()
    if "typescript" in cfg.languages:
        wanted |= {"typescript", "tsx", "javascript"}
    excludes = pathspec.GitIgnoreSpec.from_lines(cfg.exclude)
    # (directory relative to root, spec) for every .gitignore seen on the way down.
    ignores: list[tuple[str, pathspec.GitIgnoreSpec]] = []

    def ignored(rel: str, is_dir: bool) -> bool:
        probe = rel + "/" if is_dir else rel
        if excludes.match_file(probe):
            return True
        if not cfg.respect_gitignore:
            return False
        for base, spec in ignores:
            if base and not rel.startswith(base + "/"):
                continue
            sub = rel[len(base) + 1 :] if base else rel
            if spec.match_file(sub + "/" if is_dir else sub):
                return True
        return False

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        rel_dir = "" if current == root else current.relative_to(root).as_posix()
        if cfg.respect_gitignore:
            spec = _load_gitignore(current)
            if spec is not None:
                ignores.append((rel_dir, spec))

        kept = []
        for name in sorted(dirnames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if name in ALWAYS_SKIPPED or (current / name).is_symlink():
                continue
            if ignored(rel, is_dir=True):
                continue
            kept.append(name)
        dirnames[:] = kept

        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            language = language_of(name)
            if language is None or language not in wanted:
                continue
            if ignored(rel, is_dir=False):
                result.skip("ignored")
                continue
            full = current / name
            if full.is_symlink():
                target = full.resolve()
                if not is_within(root, target) or not target.is_file():
                    result.skip("symlink outside root")
                    continue
                # Indexed once, under its real path.
                result.skip("symlink")
                continue
            try:
                size = full.stat().st_size
            except OSError:
                result.skip("unreadable")
                continue
            if size > cfg.max_file_bytes:
                result.skip("too large")
                continue
            if len(result.files) >= cfg.max_files:
                result.truncated = True
                return result
            result.files.append((rel, language, size))
    return result
