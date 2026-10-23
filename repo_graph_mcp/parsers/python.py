"""Python facts via the standard library :mod:`ast` (parse only, never compile or run)."""

from __future__ import annotations

import ast
import warnings

from repo_graph_mcp.facts import SIG_LIMIT
from repo_graph_mcp.facts import Def
from repo_graph_mcp.facts import DefKind
from repo_graph_mcp.facts import Facts
from repo_graph_mcp.facts import Import
from repo_graph_mcp.facts import Ref
from repo_graph_mcp.facts import RefKind
from repo_graph_mcp.facts import TypeHint
from repo_graph_mcp.facts import clip
from repo_graph_mcp.facts import empty
from repo_graph_mcp.facts import first_line

_SELF = {"self", "cls"}


def dotted(node: ast.expr) -> str | None:
    """Static text of a callee / base / reference, or ``None`` when it is dynamic.

    ``a.b.c`` -> ``"a.b.c"``; ``super().x`` -> ``"super.x"``; ``Foo().bar`` -> ``"Foo().bar"``.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = dotted(node.value)
        return None if inner is None else f"{inner}.{node.attr}"
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "super":
            return "super"
        inner = dotted(node.func)
        return None if inner is None or inner.endswith("()") else f"{inner}()"
    if isinstance(node, ast.Subscript):  # Generic[T] as a base, list[int] in an annotation
        return dotted(node.value)
    return None


def annotation(node: ast.expr | None) -> str:
    """The class an annotation names: ``Foo``, ``"Foo"``, ``Foo | None``, ``Optional[Foo]``."""
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return ""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = annotation(node.left), annotation(node.right)
        return left if left and left != "None" else right
    if isinstance(node, ast.Subscript):
        head = dotted(node.value) or ""
        if head.rsplit(".", 1)[-1] == "Optional":
            return annotation(node.slice)
        return ""  # list[Foo], dict[...]: not a Foo
    text = dotted(node) or ""
    return "" if text == "None" or text.endswith("()") else text


class _Visitor(ast.NodeVisitor):
    def __init__(self, *, is_test: bool, docs: bool) -> None:
        self.is_test = is_test
        self.docs = docs
        self.scope: list[tuple[str, str]] = []  # (name, kind)
        self.defs: list[Def] = []
        self.imports: list[Import] = []
        self.refs: list[Ref] = []
        self.types: list[TypeHint] = []
        self._locals: list[dict[str, str]] = []  # per function: name -> type text
        self._seen: set[tuple[str, str, str]] = set()

    # -- helpers ---------------------------------------------------------- #
    @property
    def local(self) -> str:
        return ".".join(name for name, _ in self.scope)

    def _ref(self, target: str | None, line: int, kind: RefKind) -> None:
        if not target:
            return
        key = (self.local, target, kind)
        if key in self._seen:
            return
        self._seen.add(key)
        self.refs.append(Ref(scope=self.local, target=target, line=line, kind=kind))

    def _doc(self, node: ast.AST) -> str:
        if not self.docs or not isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module
        ):
            return ""
        return first_line(ast.get_docstring(node, clean=True))

    def _hint(self, scope: str, name: str, type_text: str) -> None:
        if type_text:
            self.types.append(TypeHint(scope=scope, name=name, type=type_text))

    def _class_scope(self) -> str | None:
        """Local path of the class whose method we are in (for ``self.x = ...``)."""
        if len(self.scope) >= 2 and self.scope[-2][1] == "class":
            return ".".join(name for name, _ in self.scope[:-1])
        return None

    def _value_type(self, value: ast.expr | None) -> str:
        if isinstance(value, ast.Call):
            callee = dotted(value.func)
            return f"{callee}()" if callee and not callee.endswith("()") else ""
        if isinstance(value, ast.Name) and self._locals:
            return self._locals[-1].get(value.id, "")
        return ""

    def _bind(self, target: ast.expr, type_text: str) -> None:
        if not type_text:
            return
        if isinstance(target, ast.Name):
            if self._locals:
                self._locals[-1][target.id] = type_text
                self._hint(self.local, target.id, type_text)
            elif not self.scope:  # module-level instance: app = App()
                self._hint("", target.id, type_text)
        elif (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            owner = self._class_scope()
            if owner is not None:
                self._hint(owner, target.attr, type_text)

    def _add_def(
        self, node: ast.stmt, name: str, kind: DefKind, sig: str = "", returns: str = ""
    ) -> None:
        local = f"{self.local}.{name}" if self.scope else name
        self.defs.append(
            Def(
                local=local,
                kind=kind,
                name=name,
                line=node.lineno,
                end=getattr(node, "end_lineno", None) or node.lineno,
                sig=clip(sig, SIG_LIMIT),
                doc=self._doc(node),
                returns=returns,
            )
        )

    # -- definitions ------------------------------------------------------ #
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [dotted(b) for b in node.bases]
        shown = ", ".join(b for b in bases if b)
        self._add_def(node, node.name, "class", sig=f"({shown})" if shown else "")
        for deco in node.decorator_list:
            self._ref(dotted(deco), deco.lineno, "ref")
        self.scope.append((node.name, "class"))
        for base, expr in zip(bases, node.bases, strict=True):
            self._ref(base, expr.lineno, "base")
        for kw in node.keywords:
            self.visit(kw.value)
        for stmt in node.body:
            self.visit(stmt)
        self.scope.pop()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        parent_kind = self.scope[-1][1] if self.scope else "module"
        kind: DefKind
        if self.is_test and node.name.startswith("test"):
            kind = "test"
        elif parent_kind == "class":
            kind = "method"
        else:
            kind = "function"
        try:
            sig = f"({ast.unparse(node.args)})"
            if node.returns is not None:
                sig += f" -> {ast.unparse(node.returns)}"
        except (ValueError, RecursionError):  # pragma: no cover - pathological AST
            sig = "(...)"
        if isinstance(node, ast.AsyncFunctionDef):
            sig = "async " + sig
        for deco in node.decorator_list:
            self._ref(dotted(deco), deco.lineno, "ref")
            if isinstance(deco, ast.Call):
                for arg in deco.args:
                    self.visit(arg)
        self._add_def(node, node.name, kind, sig=sig, returns=annotation(node.returns))
        self.scope.append((node.name, kind))
        params: dict[str, str] = {}
        all_args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        for param in all_args:
            hint = annotation(param.annotation)
            if hint:
                params[param.arg] = hint
                self._hint(self.local, param.arg, hint)
        self._locals.append(params)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        for stmt in node.body:
            self.visit(stmt)
        self._locals.pop()
        self.scope.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def _symbols(self, node: ast.stmt, targets: list[ast.expr]) -> None:
        if self.scope:  # only module-level names become symbol nodes
            return
        for target in targets:
            elts = target.elts if isinstance(target, ast.Tuple | ast.List) else [target]
            for elt in elts:
                if isinstance(elt, ast.Name):
                    self._add_def(node, elt.id, "symbol")

    def visit_Assign(self, node: ast.Assign) -> None:
        self._symbols(node, node.targets)
        if len(node.targets) == 1:
            self._bind(node.targets[0], self._value_type(node.value))
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._symbols(node, [node.target])
        hint = annotation(node.annotation) or self._value_type(node.value)
        if self.scope and self.scope[-1][1] == "class" and isinstance(node.target, ast.Name):
            self._hint(self.local, node.target.id, hint)  # dataclass-style field
        else:
            self._bind(node.target, hint)
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)

    # -- imports ---------------------------------------------------------- #
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(
                Import(module=alias.name, level=0, names=None, alias=alias.asname, line=node.lineno)
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = [[a.name, a.asname or a.name] for a in node.names]
        self.imports.append(
            Import(
                module=node.module or "",
                level=node.level,
                names=names,
                alias=None,
                line=node.lineno,
            )
        )

    # -- uses ------------------------------------------------------------- #
    def visit_Call(self, node: ast.Call) -> None:
        self._ref(dotted(node.func), node.lineno, "call")
        # Visit what the callee hangs off (``a().b()``) but not the chain itself.
        func = node.func
        while isinstance(func, ast.Attribute):
            func = func.value
        if not isinstance(func, ast.Name):
            self.visit(func)
        for arg in node.args:
            self.visit(arg)
        for kw in node.keywords:
            self.visit(kw.value)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id not in _SELF:
            self._ref(node.id, node.lineno, "ref")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            text = dotted(node)
            if text and text.split(".", 1)[0] not in _SELF:
                self._ref(text, node.lineno, "ref")
                return
        self.visit(node.value)


def parse(source: bytes, *, is_test: bool, docs: bool = True) -> Facts:
    """Extract definitions, imports and uses from Python source."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, type_comments=False)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return empty("python", error=f"{type(exc).__name__}: {clip(str(exc), 120)}")
    visitor = _Visitor(is_test=is_test, docs=docs)
    try:
        for stmt in tree.body:
            visitor.visit(stmt)
    except RecursionError:
        return empty("python", error="RecursionError: nesting too deep")
    facts = empty("python")
    facts["doc"] = visitor._doc(tree)
    facts["defs"] = visitor.defs
    facts["imports"] = visitor.imports
    facts["refs"] = visitor.refs
    facts["types"] = visitor.types
    return facts
