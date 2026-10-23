## Code navigation

This repository is indexed by repo-graph (MCP server `repo-graph`). Before grepping
or opening files:

- `find_symbol` / `source` to locate and read one definition,
- `callers`, `callees`, `path_between` for the call graph,
- `impact` before changing a function, and `tests_for` to pick the tests to run,
- `module_summary` instead of reading a whole file for its outline.

Run `reindex` after large edits. Edges marked `conf: name` are guesses; verify them.
