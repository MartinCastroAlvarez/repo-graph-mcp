"""Path confinement: every path that reaches the filesystem goes through here.

The indexed root is the trust boundary. A path from an MCP tool argument, a
config file or a symlink target is accepted only if, once fully resolved, it
is still inside the root.
"""

from __future__ import annotations

import os
from pathlib import Path
from pathlib import PurePosixPath


class PathEscapeError(ValueError):
    """Raised when a path resolves outside the indexed root."""


def is_within(root: Path, candidate: Path) -> bool:
    """True when ``candidate`` (already resolved) lies inside ``root`` (resolved)."""
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def safe_join(root: Path, relative: str) -> Path:
    """Join a repository-relative path onto ``root`` and refuse escapes.

    Absolute paths, ``..`` segments, NUL bytes and symlinks that point
    outside the root are all refused.
    """
    if not relative or "\x00" in relative:
        raise PathEscapeError("empty or NUL-containing path")
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise PathEscapeError(f"path must be relative to the root: {relative!r}")
    joined = (root / Path(*pure.parts)).resolve()
    if not is_within(root, joined):
        raise PathEscapeError(f"path escapes the root: {relative!r}")
    return joined


def to_relative(root: Path, path: Path) -> str:
    """Return ``path`` relative to ``root`` in POSIX form (``a/b.py``)."""
    return path.relative_to(root).as_posix()


def read_bounded(path: Path, limit: int) -> bytes | None:
    """Read at most ``limit`` bytes; ``None`` when the file is larger.

    The file is opened with ``O_NOFOLLOW`` where available so a symlink
    swapped in after the walk is not followed.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        return None
    return data
