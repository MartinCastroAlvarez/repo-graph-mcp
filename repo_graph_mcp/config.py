"""Configuration: ``[tool.repo-graph]`` in ``pyproject.toml`` or ``.repo-graph.toml``.

Unknown keys are errors, so a typo cannot silently widen what gets indexed.
Environment variables override the file; CLI flags override both.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(".repo-graph") / "graph.db"
LANGUAGES = ("python", "typescript")

#: Directories that are never indexed, whatever .gitignore says.
ALWAYS_SKIPPED = (
    ".git",
    ".hg",
    ".svn",
    ".repo-graph",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
    ".nox",
)


class ConfigError(ValueError):
    """Raised for an invalid configuration file or value."""


@dataclass(frozen=True)
class Config:
    """Everything the indexer and the server need to know."""

    root: Path
    db: Path
    exclude: tuple[str, ...] = ()
    languages: tuple[str, ...] = LANGUAGES
    max_file_bytes: int = 1_000_000
    max_files: int = 20_000
    docs: bool = True
    respect_gitignore: bool = True

    def with_overrides(self, **kwargs: Any) -> Config:
        """Return a copy with the non-``None`` keyword arguments applied."""
        values = {k: v for k, v in kwargs.items() if v is not None}
        cfg = replace(self, **values)
        _validate(cfg)
        return cfg


_KEYS = {
    "exclude": list,
    "languages": list,
    "max_file_bytes": int,
    "max_files": int,
    "docs": bool,
    "respect_gitignore": bool,
    "db": str,
}


def _read_table(root: Path) -> dict[str, Any]:
    dedicated = root / ".repo-graph.toml"
    if dedicated.is_file() and not dedicated.is_symlink():
        with dedicated.open("rb") as fh:
            data = tomllib.load(fh)
        return dict(data.get("tool", {}).get("repo-graph", data))
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and not pyproject.is_symlink():
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
        return dict(data.get("tool", {}).get("repo-graph", {}))
    return {}


def _validate(cfg: Config) -> None:
    unknown = [lang for lang in cfg.languages if lang not in LANGUAGES]
    if unknown:
        raise ConfigError(
            f"unknown language(s): {', '.join(unknown)}; known: {', '.join(LANGUAGES)}"
        )
    if cfg.max_file_bytes <= 0 or cfg.max_files <= 0:
        raise ConfigError("max_file_bytes and max_files must be positive")


def load(root: str | os.PathLike[str] = ".", *, db: str | None = None) -> Config:
    """Load the configuration for ``root``.

    ``REPO_GRAPH_DB``, ``REPO_GRAPH_MAX_FILE_BYTES`` and ``REPO_GRAPH_MAX_FILES``
    override the file; ``db`` overrides everything.
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ConfigError(f"root is not a directory: {root_path}")
    try:
        table = _read_table(root_path)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc

    for key, value in table.items():
        expected = _KEYS.get(key)
        if expected is None:
            raise ConfigError(f"unknown [tool.repo-graph] key: {key!r}")
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ConfigError(f"[tool.repo-graph] {key} must be a {expected.__name__}")

    env_db = os.environ.get("REPO_GRAPH_DB")
    db_value = db or env_db or table.get("db")
    db_path = Path(db_value) if db_value else DEFAULT_DB
    if not db_path.is_absolute():
        db_path = root_path / db_path

    def env_int(name: str, fallback: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return fallback
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer") from exc

    cfg = Config(
        root=root_path,
        db=db_path,
        exclude=tuple(str(x) for x in table.get("exclude", [])),
        languages=tuple(str(x) for x in table.get("languages", LANGUAGES)),
        max_file_bytes=env_int("REPO_GRAPH_MAX_FILE_BYTES", table.get("max_file_bytes", 1_000_000)),
        max_files=env_int("REPO_GRAPH_MAX_FILES", table.get("max_files", 20_000)),
        docs=bool(table.get("docs", True)),
        respect_gitignore=bool(table.get("respect_gitignore", True)),
    )
    _validate(cfg)
    return cfg
