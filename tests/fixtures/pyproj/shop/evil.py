"""Executing this module would leave a marker file; indexing must never execute it."""

import pathlib

pathlib.Path("EXECUTED").write_text("indexing ran project code")
