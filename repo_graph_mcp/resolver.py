"""Cross-file resolution: per-file facts in, graph nodes and edges out.

Everything here is static and best-effort. A call is linked when its target
can be followed through the module's own definitions, its imports (including
re-exports, star imports and relative imports), ``self``/``cls``/``this`` and
``super()`` through the class hierarchy. When that fails for ``obj.method()``
on an unknown object, a *name* match is used only if exactly one method in the
project has that name; such edges carry ``confidence = "name"``.

The output is deterministic: the same facts always give the same graph.
"""

from __future__ import annotations

import builtins
import posixpath
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field

from repo_graph_mcp.facts import Facts
from repo_graph_mcp.facts import Import
from repo_graph_mcp.facts import is_test_path

PY_EXTS = (".pyi", ".py")
TS_EXTS = (".tsx", ".ts", ".mts", ".cts", ".jsx", ".js", ".mjs", ".cjs")
_SELF = {"self", "cls", "this"}
_MAX_HOPS = 6
#: Method names too generic to link by name alone (dict.get, list.append, Promise.then ...).
_GENERIC = frozenset(
    [
        "get",
        "set",
        "add",
        "append",
        "extend",
        "insert",
        "remove",
        "pop",
        "clear",
        "copy",
        "update",
        "items",
        "keys",
        "values",
        "join",
        "split",
        "strip",
        "format",
        "replace",
        "lower",
        "upper",
        "encode",
        "decode",
        "read",
        "write",
        "open",
        "close",
        "run",
        "send",
        "call",
        "apply",
        "bind",
        "then",
        "catch",
        "finally",
        "map",
        "filter",
        "reduce",
        "forEach",
        "push",
        "shift",
        "sort",
        "find",
        "index",
        "count",
        "has",
        "delete",
        "start",
        "stop",
        "next",
        "emit",
        "on",
        "off",
        "log",
        "info",
        "warn",
        "error",
        "debug",
        "exec",
        "match",
        "test",
        "parse",
        "load",
        "dump",
        "dumps",
        "loads",
        "toString",
        "valueOf",
        "render",
        "init",
        "setup",
        "teardown",
        "main",
        "handle",
        "process",
        "visit",
    ]
)
_PY_BUILTINS = frozenset(dir(builtins))


@dataclass(frozen=True)
class Node:
    qualname: str
    kind: str  # module | class | function | method | symbol | test | external
    name: str
    file: str | None
    line: int
    end_line: int
    signature: str = ""
    doc: str = ""
    is_test: bool = False
    language: str = ""


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    # (src, dst, kind) -> (line, confidence)
    edges: dict[tuple[str, str, str], tuple[int, str]] = field(default_factory=dict)

    def add_edge(self, src: str, dst: str, kind: str, line: int, confidence: str = "exact") -> None:
        if src == dst:
            return
        key = (src, dst, kind)
        old = self.edges.get(key)
        if old is None or (confidence, line) < (old[1], old[0]):
            self.edges[key] = (line, confidence)


@dataclass
class _Module:
    qualname: str
    path: str
    language: str
    facts: Facts
    is_test: bool
    is_package: bool
    defs: dict[str, str] = field(default_factory=dict)  # local -> qualname
    kinds: dict[str, str] = field(default_factory=dict)  # local -> kind
    returns: dict[str, str] = field(default_factory=dict)  # local -> return annotation text
    types: dict[tuple[str, str], str] = field(default_factory=dict)  # (scope, name) -> type text
    imports: dict[str, Import] = field(default_factory=dict)  # bound name -> import
    imported_name: dict[str, str] = field(default_factory=dict)  # bound name -> original name
    star: list[Import] = field(default_factory=list)


def _strip_ext(path: str, exts: tuple[str, ...]) -> str:
    for ext in exts:
        if path.endswith(ext):
            return path[: -len(ext)]
    return path


class Resolver:
    """Build a :class:`Graph` from ``{path: (language, facts)}``."""

    def __init__(self, files: dict[str, tuple[str, Facts]]) -> None:
        self.files = dict(sorted(files.items()))
        self.graph = Graph()
        self.modules: dict[str, _Module] = {}  # qualname -> module
        self.by_path: dict[str, _Module] = {}
        self.py_alias: dict[str, list[str]] = defaultdict(list)  # import name -> module qualnames
        self.ts_paths: dict[str, str] = {}  # extension-less path -> module qualname
        self.methods_by_name: dict[str, list[str]] = defaultdict(list)
        self.defs_by_name: dict[str, list[str]] = defaultdict(list)
        self.bases: dict[str, list[str]] = defaultdict(list)

    # ------------------------------------------------------------------ #
    # module naming
    # ------------------------------------------------------------------ #
    def _python_names(self) -> None:
        py = [p for p, (lang, _) in self.files.items() if lang == "python"]
        package_dirs = {
            posixpath.dirname(p)
            for p in py
            if posixpath.basename(p) in {"__init__.py", "__init__.pyi"}
        }
        taken: set[str] = set()
        for path in sorted(py, key=lambda p: (p.endswith(".pyi"), p)):
            base = _strip_ext(path, PY_EXTS)
            parts = base.split("/")
            is_package = parts[-1] == "__init__"
            if is_package:
                parts = parts[:-1]
            # climb while the parent directory is a package
            start = len(parts) - 1
            while start > 0 and "/".join(parts[:start]) in package_dirs:
                start -= 1
            canonical = ".".join(parts[start:]) if parts else "__init__"
            full = ".".join(parts) if parts else "__init__"
            qual = canonical if canonical not in taken else full
            if qual in taken:  # x.py and x.pyi: keep the first
                continue
            taken.add(qual)
            self._add_module(qual, path, "python", is_package)
            for i in range(len(parts)):
                alias = ".".join(parts[i:])
                if alias and qual not in self.py_alias[alias]:
                    self.py_alias[alias].append(qual)

    def _ts_names(self) -> None:
        for path, (lang, _) in self.files.items():
            if lang == "python":
                continue
            qual = _strip_ext(path, TS_EXTS)
            if qual in self.modules:
                qual = path
            self._add_module(qual, path, lang, posixpath.basename(qual) == "index")
            self.ts_paths.setdefault(qual, qual)
            if posixpath.basename(qual) == "index":
                self.ts_paths.setdefault(posixpath.dirname(qual) or ".", qual)

    def _add_module(self, qual: str, path: str, language: str, is_package: bool) -> None:
        lang, facts = self.files[path]
        module = _Module(
            qualname=qual,
            path=path,
            language=language,
            facts=facts,
            is_test=is_test_path(path),
            is_package=is_package,
        )
        self.modules[qual] = module
        self.by_path[path] = module
        name = qual.rsplit(".", 1)[-1] if language == "python" else posixpath.basename(qual)
        last_line = max((d["end"] for d in facts["defs"]), default=1)
        self.graph.nodes[qual] = Node(
            qualname=qual,
            kind="module",
            name=name,
            file=path,
            line=1,
            end_line=last_line,
            doc=facts["doc"],
            is_test=module.is_test,
            language=lang,
        )

    # ------------------------------------------------------------------ #
    # definitions
    # ------------------------------------------------------------------ #
    def _definitions(self) -> None:
        for module in self.modules.values():
            for d in module.facts["defs"]:
                local = d["local"]
                if local in module.defs:
                    continue  # redefinition: first one wins (stable)
                qual = f"{module.qualname}:{local}"
                module.defs[local] = qual
                module.kinds[local] = d["kind"]
                module.returns[local] = d.get("returns", "")
                self.graph.nodes[qual] = Node(
                    qualname=qual,
                    kind=d["kind"],
                    name=d["name"],
                    file=module.path,
                    line=d["line"],
                    end_line=d["end"],
                    signature=d["sig"],
                    doc=d["doc"],
                    is_test=module.is_test,
                    language=module.language,
                )
                parent = self._parent(module, local)
                self.graph.add_edge(parent, qual, "defines", d["line"])
                if not module.is_test:
                    self.defs_by_name[d["name"]].append(qual)
                    if d["kind"] == "method":
                        self.methods_by_name[d["name"]].append(qual)
            for hint in module.facts.get("types", []):
                module.types.setdefault((hint["scope"], hint["name"]), hint["type"])
            for imp in module.facts["imports"]:
                if imp["names"] is None:
                    if imp["alias"]:
                        module.imports[imp["alias"]] = imp
                        module.imported_name[imp["alias"]] = ""
                    elif module.language == "python":
                        head = imp["module"].split(".", 1)[0]
                        module.imports.setdefault(head, Import(**{**imp, "module": head}))
                        module.imported_name.setdefault(head, "")
                    continue
                for original, bound in imp["names"]:
                    if original == "*":
                        module.star.append(imp)
                        continue
                    module.imports[bound] = imp
                    module.imported_name[bound] = original
                if imp["alias"]:  # import Default, * as ns from "x"
                    module.imports[imp["alias"]] = Import(**{**imp, "names": None})
                    module.imported_name[imp["alias"]] = ""

    def _parent(self, module: _Module, local: str) -> str:
        probe = local
        while "." in probe:
            probe = probe.rsplit(".", 1)[0]
            if probe in module.defs:
                return module.defs[probe]
        return module.qualname

    # ------------------------------------------------------------------ #
    # imports
    # ------------------------------------------------------------------ #
    def _python_module(self, importer: _Module, imp: Import) -> str | None:
        name = imp["module"]
        if imp["level"]:
            parts = importer.qualname.split(".")
            if not importer.is_package:
                parts = parts[:-1]
            up = imp["level"] - 1
            if up:
                parts = parts[:-up] if up <= len(parts) else []
            name = ".".join([*parts, name] if name else parts)
        if name in self.modules and self.modules[name].language == "python":
            return name
        candidates = self.py_alias.get(name, [])
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _ts_module(self, importer: _Module, spec: str) -> str | None:
        if not spec.startswith("."):
            return None
        base = posixpath.normpath(posixpath.join(posixpath.dirname(importer.path), spec))
        for candidate in (base, _strip_ext(base, TS_EXTS), posixpath.join(base, "index")):
            if candidate in self.ts_paths:
                return self.ts_paths[candidate]
        return None

    def import_target(self, importer: _Module, imp: Import) -> str:
        """Qualname of the imported module (internal) or ``ext:<package>``."""
        if importer.language == "python":
            found = self._python_module(importer, imp)
            if found:
                return found
            if imp["level"]:
                return f"ext:.{imp['module']}"
            return f"ext:{imp['module'].split('.', 1)[0]}"
        found = self._ts_module(importer, imp["module"])
        if found:
            return found
        spec = imp["module"]
        if spec.startswith("."):
            return f"ext:{spec}"
        parts = spec.split("/")
        return f"ext:{'/'.join(parts[:2]) if spec.startswith('@') else parts[0]}"

    def _imports(self) -> None:
        for module in self.modules.values():
            for imp in module.facts["imports"]:
                target = self.import_target(module, imp)
                if module.language == "python" and imp["names"] and not target.startswith("ext:"):
                    # from pkg import submodule -> link the submodule itself
                    for original, _ in imp["names"]:
                        sub = f"{target}.{original}"
                        if sub in self.modules:
                            self._edge_to(module.qualname, sub, "imports", imp["line"])
                self._edge_to(module.qualname, target, "imports", imp["line"])

    def _edge_to(self, src: str, dst: str, kind: str, line: int, confidence: str = "exact") -> None:
        if dst.startswith("ext:") and dst not in self.graph.nodes:
            name = dst[4:]
            self.graph.nodes[dst] = Node(
                qualname=dst, kind="external", name=name, file=None, line=0, end_line=0
            )
        self.graph.add_edge(src, dst, kind, line, confidence)

    # ------------------------------------------------------------------ #
    # name resolution
    # ------------------------------------------------------------------ #
    def _binding(self, module: _Module, name: str, hops: int = 0) -> str | None:
        """What ``name`` means at the top level of ``module``: a qualname or ``ext:..``."""
        if hops > _MAX_HOPS:
            return None
        if name in module.defs:
            return module.defs[name]
        imp = module.imports.get(name)
        if imp is not None:
            target = self.import_target(module, imp)
            original = module.imported_name.get(name, "")
            if not original:  # the module itself is bound to the name
                return target
            if target.startswith("ext:"):
                return target if original == "default" else f"{target}.{original}"
            imported = self.modules[target]
            if original == "default" and imported.language != "python":
                return self._default_export(imported) or target
            if imported.language == "python" and f"{target}.{original}" in self.modules:
                return f"{target}.{original}"
            return self._binding(imported, original, hops + 1)
        for imp in module.star:
            target = self.import_target(module, imp)
            if not target.startswith("ext:"):
                found = self._binding(self.modules[target], name, hops + 1)
                if found:
                    return found
        return None

    def _default_export(self, module: _Module) -> str | None:
        top = [
            q
            for local, q in module.defs.items()
            if "." not in local and module.kinds[local] != "test"
        ]
        named = [q for q in top if q.rsplit(":", 1)[-1] == posixpath.basename(module.qualname)]
        if named:
            return named[0]
        return top[0] if len(top) == 1 else None

    def _enclosing_class(self, module: _Module, scope: str) -> str | None:
        probe = scope
        while probe:
            if module.kinds.get(probe) == "class":
                return module.defs[probe]
            probe = probe.rsplit(".", 1)[0] if "." in probe else ""
        return None

    def _member(self, owner: str, part: str, hops: int = 0) -> str | None:
        """Resolve ``owner.part`` where ``owner`` is a module or class qualname."""
        if hops > _MAX_HOPS:
            return None
        if owner.startswith("ext:"):
            return f"{owner}.{part}"
        if owner in self.modules:
            module = self.modules[owner]
            if module.language == "python" and f"{owner}.{part}" in self.modules:
                return f"{owner}.{part}"
            return self._binding(module, part)
        node = self.graph.nodes.get(owner)
        if node is None or node.kind != "class":
            return None
        module = self.by_path[node.file or ""]
        local = owner.split(":", 1)[1]
        if f"{local}.{part}" in module.defs:
            return module.defs[f"{local}.{part}"]
        hint = module.types.get((local, part))
        if hint:  # an attribute with a known type: self.store = Store(...)
            return self._type_class(module, local, hint, hops + 1)
        for base in self.bases.get(owner, []):
            found = self._member(base, part, hops + 1)
            if found and not found.startswith("ext:"):
                return found
        return None

    def _returned(self, qual: str, hops: int) -> str | None:
        """The class a function/method is annotated to return."""
        node = self.graph.nodes.get(qual)
        if node is None or node.kind not in {"function", "method"} or hops > _MAX_HOPS:
            return None
        module = self.by_path[node.file or ""]
        local = qual.split(":", 1)[1]
        text = module.returns.get(local, "")
        if not text:
            return None
        scope = local.rsplit(".", 1)[0] if "." in local else ""
        return self._type_class(module, scope, text, hops + 1)

    def _type_class(self, module: _Module, scope: str, text: str, hops: int = 0) -> str | None:
        """Class qualname for a type text (``Foo``, ``mod.Foo``, ``Foo()``, ``make_foo()``)."""
        if hops > _MAX_HOPS:
            return None
        found = self.resolve(module, scope, text, hops + 1)
        if found is None or found.startswith("ext:"):
            return None
        node = self.graph.nodes.get(found)
        return found if node is not None and node.kind == "class" else None

    def _local_type(self, module: _Module, scope: str, name: str) -> str | None:
        probe = scope
        while True:
            hint = module.types.get((probe, name))
            if hint is not None:
                return hint
            if not probe:
                return None
            probe = probe.rsplit(".", 1)[0] if "." in probe else ""

    def _step(self, current: str | None, called: bool, hops: int) -> str | None:
        """After ``x`` in ``x()``: a class stays (an instance), a function gives its return type."""
        if current is None or not called:
            return current
        node = self.graph.nodes.get(current)
        if node is not None and node.kind in {"function", "method"}:
            return self._returned(current, hops)
        return current

    def resolve(self, module: _Module, scope: str, target: str, hops: int = 0) -> str | None:
        if hops > _MAX_HOPS:
            return None
        parts = target.split(".")
        head = parts[0]
        current: str | None
        if head in _SELF:
            current = self._enclosing_class(module, scope)
        elif head == "super":
            cls = self._enclosing_class(module, scope)
            if cls is None or len(parts) < 2:
                return None
            current = None
            for base in self.bases.get(cls, []):
                found = self._member(base, parts[1].removesuffix("()"), hops + 1)
                if found and not found.startswith("ext:"):
                    current = self._step(found, parts[1].endswith("()"), hops + 1)
                    break
            parts = parts[1:]
        else:
            name = head.removesuffix("()")
            current = None
            probe = scope
            while probe:  # lexical scopes, innermost first
                if f"{probe}.{name}" in module.defs:
                    current = module.defs[f"{probe}.{name}"]
                    break
                probe = probe.rsplit(".", 1)[0] if "." in probe else ""
            if current is None:
                hint = self._local_type(module, scope, name)
                if hint is not None:
                    if len(parts) == 1:
                        return None  # a typed variable, not a definition
                    current = self._type_class(module, scope, hint, hops + 1)
                    head = name
            if current is None:
                current = self._binding(module, name)
            current = self._step(current, head.endswith("()"), hops + 1)
        for part in parts[1:]:
            if current is None:
                return None
            current = self._member(current, part.removesuffix("()"), hops + 1)
            current = self._step(current, part.endswith("()"), hops + 1)
        return current

    # ------------------------------------------------------------------ #
    # uses
    # ------------------------------------------------------------------ #
    def _scope_node(self, module: _Module, scope: str) -> str:
        return module.defs.get(scope, module.qualname) if scope else module.qualname

    def _inheritance(self) -> None:
        for module in self.modules.values():
            for ref in module.facts["refs"]:
                if ref["kind"] != "base":
                    continue
                cls = module.defs.get(ref["scope"])
                if cls is None:
                    continue
                target = self.resolve(module, "", ref["target"])
                if target is None:
                    candidates = [
                        q
                        for q in self.defs_by_name.get(ref["target"].rsplit(".", 1)[-1], [])
                        if self.graph.nodes[q].kind == "class"
                    ]
                    if (
                        len(candidates) == 1
                        and ref["target"].rsplit(".", 1)[-1] not in _PY_BUILTINS
                    ):
                        self.bases[cls].append(candidates[0])
                        self.graph.add_edge(cls, candidates[0], "inherits", ref["line"], "name")
                    continue
                if target.startswith("ext:"):
                    continue
                if self.graph.nodes.get(target) and self.graph.nodes[target].kind == "class":
                    self.bases[cls].append(target)
                    self.graph.add_edge(cls, target, "inherits", ref["line"])

    def _uses(self) -> None:
        for module in self.modules.values():
            for ref in module.facts["refs"]:
                if ref["kind"] == "base":
                    continue
                src = self._scope_node(module, ref["scope"])
                target = self.resolve(module, ref["scope"], ref["target"])
                kind = "calls" if ref["kind"] == "call" else "references"
                if target is not None:
                    if target.startswith("ext:"):
                        continue
                    if target not in self.graph.nodes:
                        continue
                    if kind == "calls" and self.graph.nodes[target].kind in {"module", "symbol"}:
                        kind = "references"
                    self.graph.add_edge(src, target, kind, ref["line"])
                    continue
                if kind != "calls":
                    continue
                guess = self._guess(module, ref["scope"], ref["target"])
                if guess is not None:
                    self.graph.add_edge(src, guess, "calls", ref["line"], "name")

    def _guess(self, module: _Module, scope: str, target: str) -> str | None:
        parts = target.split(".")
        if len(parts) < 2:
            return None
        name = parts[-1].removesuffix("()")
        if name in _GENERIC or len(name) < 4 or name.startswith("__"):
            return None
        head = parts[0].removesuffix("()")
        if head in _SELF or self._local_type(module, scope, head) is not None:
            return None  # the receiver's type is known; a name match would only add noise
        # A known external head (os.path.join, np.array) is never guessed into the project.
        bound = self._binding(module, head)
        if bound is not None and bound.startswith("ext:"):
            return None
        if head in _PY_BUILTINS and module.language == "python":
            return None
        candidates = self.methods_by_name.get(name, [])
        return candidates[0] if len(candidates) == 1 else None

    # ------------------------------------------------------------------ #
    # tests
    # ------------------------------------------------------------------ #
    def _tests(self) -> None:
        nodes = self.graph.nodes
        out: dict[str, list[tuple[str, str, int, str]]] = defaultdict(list)
        for (src, dst, kind), (line, conf) in self.graph.edges.items():
            out[src].append((dst, kind, line, conf))
        imported_by: dict[str, set[str]] = defaultdict(set)
        for (src, dst, kind), _ in self.graph.edges.items():
            if kind == "imports":
                imported_by[src].add(dst)

        for qual, node in list(nodes.items()):
            if node.kind == "test":
                module = self.by_path[node.file or ""]
                linked = False
                for dst, kind, line, conf in out.get(qual, []):
                    callee = nodes.get(dst)
                    if (
                        kind in {"calls", "references"}
                        and callee is not None
                        and not callee.is_test
                        and callee.kind not in {"module", "external", "symbol"}
                    ):
                        self.graph.add_edge(qual, dst, "tests", line, conf)
                        linked = True
                if not linked:
                    guess = self._test_name_target(module, node, imported_by[module.qualname])
                    if guess:
                        self.graph.add_edge(qual, guess, "tests", node.line, "name")
            elif node.kind == "module" and node.is_test:
                module = self.modules[qual]
                tested = self._test_module_target(module, imported_by[qual])
                if tested:
                    self.graph.add_edge(qual, tested, "tests", 1, "name")
                for dst, kind, line, conf in out.get(qual, []):  # describe("Foo") at module level
                    used = nodes.get(dst)
                    if kind == "references" and used and used.kind == "class" and not used.is_test:
                        self.graph.add_edge(qual, dst, "tests", line, conf)

    def _test_name_target(self, module: _Module, node: Node, imported: set[str]) -> str | None:
        name = node.name
        if name.startswith("test_"):
            name = name[5:]
        elif name.startswith("test"):
            name = name[4:]
        words = [w for w in name.replace(" ", "_").split("_") if w]
        scope = {q for q in imported if q in self.modules}
        for size in range(len(words), 0, -1):
            candidate = "_".join(words[:size])
            for qual in self.defs_by_name.get(candidate, []):
                owner = qual.split(":", 1)[0]
                if owner in scope and nodes_kind(self.graph, qual) in {
                    "function",
                    "method",
                    "class",
                }:
                    return qual
        return None

    def _test_module_target(self, module: _Module, imported: set[str]) -> str | None:
        stem = posixpath.basename(module.path)
        for marker in (".test.", ".spec."):
            if marker in stem:
                stem = stem.split(marker, 1)[0]
                break
        else:
            stem = _strip_ext(stem, PY_EXTS)
            stem = stem.removeprefix("test_").removesuffix("_test")
        candidates = [
            m.qualname
            for m in self.modules.values()
            if not m.is_test
            and posixpath.basename(_strip_ext(_strip_ext(m.path, PY_EXTS), TS_EXTS)) == stem
        ]
        if len(candidates) > 1:
            candidates = [c for c in candidates if c in imported] or candidates[:0]
        return candidates[0] if len(candidates) == 1 else None

    # ------------------------------------------------------------------ #
    def build(self) -> Graph:
        self._python_names()
        self._ts_names()
        self._definitions()
        self._imports()
        self._inheritance()
        self._uses()
        self._tests()
        return self.graph


def nodes_kind(graph: Graph, qual: str) -> str:
    node = graph.nodes.get(qual)
    return node.kind if node else ""


def build_graph(files: dict[str, tuple[str, Facts]]) -> Graph:
    """Resolve every file's facts into one graph."""
    return Resolver(files).build()
