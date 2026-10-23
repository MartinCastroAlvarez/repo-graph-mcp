"""TypeScript / JavaScript facts via tree-sitter (optional ``typescript`` extra).

tree-sitter builds a concrete syntax tree; nothing is transpiled or executed.
"""

from __future__ import annotations

from functools import cache
from typing import Any

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

try:  # pragma: no cover - exercised by whichever environment runs the tests
    import tree_sitter
    import tree_sitter_javascript
    import tree_sitter_typescript

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False

Node = Any  # tree_sitter.Node; untyped upstream

_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
_CLASS_DECLS = {"class_declaration", "abstract_class_declaration", "class"}
_TEST_CALLS = {"it", "test"}
_SUITE_CALLS = {"describe", "suite", "context"}
_THIS = {"this", "super"}
_MAX_DEPTH = 400
_PARAMETERS = {"required_parameter", "optional_parameter", "formal_parameters", "arrow_function"}


def available() -> bool:
    return _AVAILABLE


@cache
def _parser(language: str) -> Any:
    if language == "typescript":
        lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())
    elif language == "tsx":
        lang = tree_sitter.Language(tree_sitter_typescript.language_tsx())
    else:
        lang = tree_sitter.Language(tree_sitter_javascript.language())
    return tree_sitter.Parser(lang)


def _text(node: Node | None) -> str:
    if node is None:
        return ""
    raw: bytes = node.text or b""
    return raw.decode("utf-8", errors="replace")


def _string_value(node: Node | None) -> str | None:
    if node is None or node.type not in {"string", "template_string"}:
        return None
    if node.type == "template_string" and any(
        c.type == "template_substitution" for c in node.children
    ):
        return None
    return _text(node)[1:-1]


def dotted(node: Node | None) -> str | None:
    """``a.b.c`` -> ``"a.b.c"``; ``this.x`` -> ``"this.x"``; ``new Foo().b`` -> ``"Foo().b"``."""
    if node is None:
        return None
    kind = node.type
    if kind in {"identifier", "type_identifier", "property_identifier", "this", "super"}:
        return _text(node)
    if kind == "member_expression":
        obj = dotted(node.child_by_field_name("object"))
        prop = node.child_by_field_name("property")
        return None if obj is None or prop is None else f"{obj}.{_text(prop)}"
    if kind == "nested_type_identifier":
        return _text(node).replace(" ", "")
    if kind == "new_expression":
        inner = dotted(node.child_by_field_name("constructor"))
        return None if inner is None else f"{inner}()"
    if kind == "call_expression":
        inner = dotted(node.child_by_field_name("function"))
        return None if inner is None or inner.endswith("()") else f"{inner}()"
    if kind == "parenthesized_expression" and node.named_child_count == 1:
        return dotted(node.named_children[0])
    if kind in {"generic_type"}:
        return dotted(node.named_children[0]) if node.named_child_count else None
    return None


def type_text(annotation: Node | None) -> str:
    """The class a ``type_annotation`` names: ``: Foo``, ``: Foo | null``, ``: Foo<T>``."""
    if annotation is None:
        return ""
    node = (
        annotation.named_children[0]
        if annotation.type == "type_annotation" and annotation.named_child_count
        else annotation
    )
    if node.type == "union_type":
        for part in node.named_children:
            text = type_text(part)
            if text:
                return text
        return ""
    if node.type in {"type_identifier", "nested_type_identifier", "generic_type"}:
        return dotted(node) or ""
    return ""


def value_type(value: Node | None) -> str:
    """``new Foo()`` -> ``"Foo()"``; ``makeFoo()`` -> ``"makeFoo()"``."""
    if value is None:
        return ""
    if value.type == "await_expression" and value.named_child_count:
        value = value.named_children[0]
    if value.type == "new_expression":
        ctor = dotted(value.child_by_field_name("constructor"))
        return f"{ctor}()" if ctor else ""
    if value.type == "call_expression":
        fn = dotted(value.child_by_field_name("function"))
        return f"{fn}()" if fn and not fn.endswith("()") else ""
    return ""


class _Walker:
    def __init__(self, *, is_test: bool, docs: bool) -> None:
        self.is_test = is_test
        self.docs = docs
        self.scope: list[tuple[str, str]] = []
        self.defs: list[Def] = []
        self.imports: list[Import] = []
        self.refs: list[Ref] = []
        self.types: list[TypeHint] = []
        self._seen: set[tuple[str, str, str]] = set()
        self._suite: list[str] = []
        self._test_names: set[str] = set()

    @property
    def local(self) -> str:
        return ".".join(name for name, _ in self.scope)

    def ref(self, target: str | None, node: Node, kind: RefKind) -> None:
        if not target:
            return
        key = (self.local, target, kind)
        if key in self._seen:
            return
        self._seen.add(key)
        line = node.start_point[0] + 1
        self.refs.append(Ref(scope=self.local, target=target, line=line, kind=kind))

    def _doc(self, node: Node) -> str:
        if not self.docs:
            return ""
        prev = node.prev_named_sibling
        if prev is None and node.parent is not None and node.parent.type == "export_statement":
            prev = node.parent.prev_named_sibling
        if prev is not None and prev.type == "comment":
            text = _text(prev)
            if text.startswith("/**"):
                body = text[3:-2]
                lines = [ln.strip().lstrip("*").strip() for ln in body.splitlines()]
                return first_line("\n".join(lines))
        return ""

    def hint(self, scope: str, name: str, text: str) -> None:
        if name and text:
            self.types.append(TypeHint(scope=scope, name=name, type=text))

    def _class_scope(self) -> str | None:
        for i in range(len(self.scope) - 1, -1, -1):
            if self.scope[i][1] == "class":
                return ".".join(n for n, _ in self.scope[: i + 1])
        return None

    def add_def(
        self, node: Node, name: str, kind: DefKind, sig: str = "", returns: str = ""
    ) -> str:
        local = f"{self.local}.{name}" if self.scope else name
        self.defs.append(
            Def(
                local=local,
                kind=kind,
                name=name,
                line=node.start_point[0] + 1,
                end=node.end_point[0] + 1,
                sig=clip(sig, SIG_LIMIT),
                doc=self._doc(node),
                returns=returns,
            )
        )
        return local

    def _sig(self, node: Node) -> str:
        params = node.child_by_field_name("parameters")
        ret = node.child_by_field_name("return_type")
        sig = _text(params) if params is not None else "()"
        if ret is not None:
            sig += _text(ret)
        return sig

    # -- the walk --------------------------------------------------------- #
    def visit(self, node: Node, depth: int = 0) -> None:
        if depth > _MAX_DEPTH:
            raise RecursionError("nesting too deep")
        handler = getattr(self, f"on_{node.type}", None)
        if handler is not None and handler(node, depth) is not False:
            return
        for child in node.named_children:
            self.visit(child, depth + 1)

    def on_comment(self, node: Node, depth: int) -> bool:
        return True

    def on_import_statement(self, node: Node, depth: int) -> bool:
        source = _string_value(node.child_by_field_name("source"))
        if source is None:
            return True
        names: list[list[str]] = []
        alias: str | None = None
        for clause in (c for c in node.named_children if c.type == "import_clause"):
            for part in clause.named_children:
                if part.type == "identifier":
                    names.append(["default", _text(part)])
                elif part.type == "namespace_import":
                    ident = next((c for c in part.named_children if c.type == "identifier"), None)
                    alias = _text(ident) or None
                elif part.type == "named_imports":
                    for spec in part.named_children:
                        if spec.type != "import_specifier":
                            continue
                        imported = _text(spec.child_by_field_name("name"))
                        bound = _text(spec.child_by_field_name("alias")) or imported
                        names.append([imported, bound])
        self.imports.append(
            Import(
                module=source,
                level=0,
                names=names if names or alias is None else None,
                alias=alias,
                line=node.start_point[0] + 1,
            )
        )
        return True

    def on_export_statement(self, node: Node, depth: int) -> bool:
        source = _string_value(node.child_by_field_name("source"))
        if source is not None:  # export { a } from "./x"  /  export * from "./x"
            names: list[list[str]] = []
            for clause in (c for c in node.named_children if c.type == "export_clause"):
                for spec in clause.named_children:
                    if spec.type == "export_specifier":
                        imported = _text(spec.child_by_field_name("name"))
                        bound = _text(spec.child_by_field_name("alias")) or imported
                        names.append([imported, bound])
            self.imports.append(
                Import(
                    module=source, level=0, names=names, alias=None, line=node.start_point[0] + 1
                )
            )
            return True
        return False

    def _function_like(self, node: Node, name: str, depth: int, fn: Node) -> None:
        parent_kind = self.scope[-1][1] if self.scope else "module"
        kind: DefKind = "method" if parent_kind == "class" else "function"
        self.add_def(
            node,
            name,
            kind,
            sig=self._sig(fn),
            returns=type_text(fn.child_by_field_name("return_type")),
        )
        self.scope.append((name, kind))
        params = fn.child_by_field_name("parameters")
        for param in params.named_children if params is not None else []:
            pattern = param.child_by_field_name("pattern")
            hint = type_text(param.child_by_field_name("type"))
            if pattern is None or pattern.type != "identifier" or not hint:
                continue
            self.hint(self.local, _text(pattern), hint)
            # constructor(private store: Store) declares a field
            if name == "constructor" and any(
                c.type == "accessibility_modifier" for c in param.children
            ):
                owner = self._class_scope()
                if owner is not None:
                    self.hint(owner, _text(pattern), hint)
        for field in ("parameters", "parameter", "return_type", "body"):
            child = fn.child_by_field_name(field)
            if child is not None:
                self.visit(child, depth + 1)
        self.scope.pop()

    def on_function_declaration(self, node: Node, depth: int) -> bool:
        name = _text(node.child_by_field_name("name"))
        if not name:
            return False
        self._function_like(node, name, depth, node)
        return True

    on_generator_function_declaration = on_function_declaration

    def on_method_definition(self, node: Node, depth: int) -> bool:
        name = _text(node.child_by_field_name("name"))
        if not name:
            return False
        self._function_like(node, name, depth, node)
        return True

    def _class_like(self, node: Node, depth: int, sig: str = "") -> bool:
        name_node = node.child_by_field_name("name")
        name = _text(name_node)
        if not name:
            return False
        bases: list[tuple[str, Node]] = []
        for heritage in (
            c for c in node.named_children if c.type in {"class_heritage", "extends_type_clause"}
        ):
            for clause in (
                heritage.named_children if heritage.type == "class_heritage" else [heritage]
            ):
                for target in clause.named_children:
                    text = dotted(target)
                    if text:
                        bases.append((text, target))
        shown = ", ".join(b for b, _ in bases)
        label = sig or (f"({shown})" if shown else "")
        self.add_def(node, name, "class", sig=label)
        self.scope.append((name, "class"))
        for text, target in bases:
            self.ref(text, target, "base")
        body = node.child_by_field_name("body")
        if body is not None:
            self.visit(body, depth + 1)
        self.scope.pop()
        return True

    def on_class_declaration(self, node: Node, depth: int) -> bool:
        return self._class_like(node, depth)

    on_abstract_class_declaration = on_class_declaration

    def on_interface_declaration(self, node: Node, depth: int) -> bool:
        return self._class_like(node, depth, sig="interface")

    def on_enum_declaration(self, node: Node, depth: int) -> bool:
        name = _text(node.child_by_field_name("name"))
        if name and not self.scope:
            self.add_def(node, name, "symbol", sig="enum")
        return True

    def on_type_alias_declaration(self, node: Node, depth: int) -> bool:
        name = _text(node.child_by_field_name("name"))
        if name and not self.scope:
            self.add_def(node, name, "symbol", sig="type")
        value = node.child_by_field_name("value")
        if value is not None:
            self.visit(value, depth + 1)
        return True

    def on_public_field_definition(self, node: Node, depth: int) -> bool:
        name = _text(node.child_by_field_name("name"))
        value = node.child_by_field_name("value")
        if name and value is not None and value.type in _FUNCTION_VALUES:
            self._function_like(node, name, depth, value)
            return True
        if name and self.scope and self.scope[-1][1] == "class":
            self.hint(
                self.local, name, type_text(node.child_by_field_name("type")) or value_type(value)
            )
        return False

    def on_assignment_expression(self, node: Node, depth: int) -> bool:
        left = node.child_by_field_name("left")
        if left is not None and left.type == "member_expression":
            obj = left.child_by_field_name("object")
            prop = left.child_by_field_name("property")
            owner = self._class_scope()
            if obj is not None and obj.type == "this" and prop is not None and owner is not None:
                self.hint(owner, _text(prop), value_type(node.child_by_field_name("right")))
        return False

    def on_variable_declarator(self, node: Node, depth: int) -> bool:
        name_node = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        # const x = require("./mod") / const { a, b } = require("./mod")
        if value is not None and value.type == "call_expression":
            fn = value.child_by_field_name("function")
            args = value.child_by_field_name("arguments")
            if (
                fn is not None
                and _text(fn) == "require"
                and args is not None
                and args.named_child_count
            ):
                source = _string_value(args.named_children[0])
                if source is not None and name_node is not None:
                    line = node.start_point[0] + 1
                    if name_node.type == "identifier":
                        self.imports.append(
                            Import(
                                module=source,
                                level=0,
                                names=None,
                                alias=_text(name_node),
                                line=line,
                            )
                        )
                    elif name_node.type == "object_pattern":
                        names = []
                        for part in name_node.named_children:
                            if part.type == "shorthand_property_identifier_pattern":
                                names.append([_text(part), _text(part)])
                            elif part.type == "pair_pattern":
                                key = _text(part.child_by_field_name("key"))
                                val = _text(part.child_by_field_name("value"))
                                names.append([key, val])
                        self.imports.append(
                            Import(module=source, level=0, names=names, alias=None, line=line)
                        )
                    return True
        if name_node is None or name_node.type != "identifier":
            return False
        name = _text(name_node)
        if value is not None and value.type in _FUNCTION_VALUES:
            self._function_like(node, name, depth, value)
            return True
        if not self.scope:
            self.add_def(node, name, "symbol")
        self.hint(
            self.local, name, type_text(node.child_by_field_name("type")) or value_type(value)
        )
        if value is not None:
            self.visit(value, depth + 1)
        return True

    def on_call_expression(self, node: Node, depth: int) -> bool:
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        callee = dotted(fn)
        if fn is not None and fn.type == "import" and args is not None and args.named_child_count:
            source = _string_value(args.named_children[0])  # dynamic import("./x")
            if source is not None:
                self.imports.append(
                    Import(
                        module=source, level=0, names=[], alias=None, line=node.start_point[0] + 1
                    )
                )
            return True
        base = callee.split(".", 1)[0] if callee else ""
        if (
            self.is_test
            and base in _TEST_CALLS | _SUITE_CALLS
            and args is not None
            and args.named_child_count
        ):
            title = _string_value(args.named_children[0])
            if title is not None:
                return self._test_block(node, base, clip(title, 80), args, depth)
        self.ref(callee, node, "call")
        if fn is not None and fn.type == "member_expression":
            obj = fn.child_by_field_name("object")
            while obj is not None and obj.type == "member_expression":
                obj = obj.child_by_field_name("object")
            if obj is not None and obj.type not in {"identifier", "this", "super"}:
                self.visit(obj, depth + 1)
        if args is not None:
            self.visit(args, depth + 1)
        return True

    def _test_block(self, node: Node, base: str, title: str, args: Node, depth: int) -> bool:
        if base in _SUITE_CALLS:
            self._suite.append(title)
            # describe("Foo") names the thing under test.
            self.ref(title.split(" ", 1)[0], node, "ref")
            for child in args.named_children[1:]:
                self.visit(child, depth + 1)
            self._suite.pop()
            return True
        name = " > ".join([*self._suite, title])
        unique, n = name, 2
        while unique in self._test_names:
            unique, n = f"{name} #{n}", n + 1
        self._test_names.add(unique)
        self.defs.append(
            Def(
                local=unique,
                kind="test",
                name=title,
                line=node.start_point[0] + 1,
                end=node.end_point[0] + 1,
                sig="",
                doc="",
                returns="",
            )
        )
        self.scope.append((unique, "test"))
        for child in args.named_children[1:]:
            self.visit(child, depth + 1)
        self.scope.pop()
        return True

    def on_new_expression(self, node: Node, depth: int) -> bool:
        ctor = dotted(node.child_by_field_name("constructor"))
        self.ref(ctor, node, "call")
        args = node.child_by_field_name("arguments")
        if args is not None:
            self.visit(args, depth + 1)
        return True

    def on_member_expression(self, node: Node, depth: int) -> bool:
        text = dotted(node)
        if text and text.split(".", 1)[0] not in _THIS:
            self.ref(text, node, "ref")
            return True
        obj = node.child_by_field_name("object")
        if obj is not None and obj.type not in _THIS:
            self.visit(obj, depth + 1)
        return True

    def on_identifier(self, node: Node, depth: int) -> bool:
        parent = node.parent
        if parent is not None and (
            parent.type in _PARAMETERS or parent.child_by_field_name("name") == node
        ):
            return True  # a declaration, not a use
        self.ref(_text(node), node, "ref")
        return True

    def on_type_identifier(self, node: Node, depth: int) -> bool:
        return self.on_identifier(node, depth)

    def on_jsx_opening_element(self, node: Node, depth: int) -> bool:
        name = dotted(node.child_by_field_name("name"))
        if name and name[:1].isupper():
            self.ref(name, node, "call")
        for attr in node.named_children[1:]:
            self.visit(attr, depth + 1)
        return True

    on_jsx_self_closing_element = on_jsx_opening_element

    def on_property_identifier(self, node: Node, depth: int) -> bool:
        return True

    def on_shorthand_property_identifier_pattern(self, node: Node, depth: int) -> bool:
        return True


def parse(source: bytes, language: str, *, is_test: bool, docs: bool = True) -> Facts:
    """Extract definitions, imports and uses from TypeScript / JavaScript source."""
    tree = _parser(language).parse(source)
    walker = _Walker(is_test=is_test, docs=docs)
    try:
        walker.visit(tree.root_node)
    except RecursionError:
        return empty(language, error="RecursionError: nesting too deep")
    facts = empty(language, error="syntax errors (partial)" if tree.root_node.has_error else None)
    facts["defs"] = walker.defs
    facts["imports"] = walker.imports
    facts["refs"] = walker.refs
    facts["types"] = walker.types
    if docs:
        first = tree.root_node.named_children[0] if tree.root_node.named_child_count else None
        if first is not None and first.type == "comment" and _text(first).startswith("/**"):
            facts["doc"] = first_line(_text(first)[3:-2].replace("*", " "))
    return facts
