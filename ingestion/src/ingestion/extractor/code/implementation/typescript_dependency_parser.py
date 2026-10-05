"""Build static TypeScript/JavaScript symbol and dependency relationships from syntax trees."""

from dataclasses import dataclass
from typing import Literal

from tree_sitter import Node
from tree_sitter_language_pack import get_parser


@dataclass(frozen=True)
class Symbol:
    id: str
    name: str
    kind: Literal["Function", "Component", "Method", "Class", "Interface"]
    line: int
    end_line: int


@dataclass(frozen=True)
class ImportBinding:
    module: str
    local_name: str
    imported_name: str


@dataclass(frozen=True)
class Reference:
    source_symbol_id: str
    target_name: str
    relationship: Literal["CALLS", "RENDERS", "EXTENDS", "IMPLEMENTS"]
    line: int


@dataclass(frozen=True)
class FileAnalysis:
    symbols: tuple[Symbol, ...]
    imports: tuple[ImportBinding, ...]
    references: tuple[Reference, ...]


class TypeScriptDependencyParser:
    """Extract declared functions/components and statically resolvable code references."""

    def __init__(self) -> None:
        self._parsers = {
            language: get_parser(language)
            for language in ("tsx", "typescript", "javascript")
        }

    def parse(self, path: str, content: str) -> FileAnalysis:
        language = self._language(path)
        source = content.encode("utf-8")
        root = self._parsers[language].parse(source).root_node
        symbols: list[Symbol] = []
        imports: list[ImportBinding] = []
        references: list[Reference] = []
        pending: list[tuple[Node, Symbol | None]] = [(root, None)]
        while pending:
            node, current_symbol = pending.pop()
            symbol = self._declared_symbol(path, source, node)
            if symbol is not None:
                symbols.append(symbol)
                current_symbol = symbol
            if node.type == "call_expression" and current_symbol is not None:
                function = node.child_by_field_name("function")
                if function is not None:
                    name = self._reference_name(source[function.start_byte : function.end_byte].decode())
                    if name:
                        references.append(
                            Reference(current_symbol.id, name, "CALLS", node.start_point.row + 1)
                        )
            if current_symbol is not None and node.type == "extends_clause":
                target = node.child_by_field_name("value")
                if target is not None:
                    name = self._reference_name(
                        source[target.start_byte : target.end_byte].decode().split("<", 1)[0]
                    )
                    if name:
                        references.append(
                            Reference(
                                current_symbol.id,
                                name,
                                "EXTENDS",
                                node.start_point.row + 1,
                            )
                        )
            if current_symbol is not None and node.type == "implements_clause":
                for target in node.named_children:
                    name = self._reference_name(
                        source[target.start_byte : target.end_byte].decode().split("<", 1)[0]
                    )
                    if name:
                        references.append(
                            Reference(
                                current_symbol.id,
                                name,
                                "IMPLEMENTS",
                                node.start_point.row + 1,
                            )
                        )
            if node.type in {"jsx_opening_element", "jsx_self_closing_element"} and current_symbol:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name = source[name_node.start_byte : name_node.end_byte].decode()
                    if name and name[0].isupper():
                        references.append(
                            Reference(current_symbol.id, name, "RENDERS", node.start_point.row + 1)
                        )
            if node.type in {"import_statement", "export_statement"}:
                imports.extend(self._imports(node, source))
            if node.type == "call_expression":
                function = node.child_by_field_name("function")
                arguments = node.child_by_field_name("arguments")
                if (
                    function is not None
                    and function.type == "import"
                    and arguments is not None
                ):
                    module_node = next((child for child in arguments.children if child.type == "string"), None)
                    if module_node is not None:
                        module = self._unquote(source[module_node.start_byte : module_node.end_byte].decode())
                        imports.append(ImportBinding(module, "", ""))
            pending.extend((child, current_symbol) for child in reversed(node.children))
        return FileAnalysis(
            symbols=tuple({item.id: item for item in symbols}.values()),
            imports=tuple(imports),
            references=tuple(references),
        )

    @staticmethod
    def _language(path: str) -> str:
        if path.endswith(".tsx"):
            return "tsx"
        if path.endswith(".jsx"):
            return "javascript"
        if path.endswith((".js", ".mjs", ".cjs")):
            return "javascript"
        return "typescript"

    @staticmethod
    def _declared_symbol(path: str, source: bytes, node: Node) -> Symbol | None:
        symbol_kind: Literal["Function", "Component", "Method", "Class", "Interface"] | None = None
        if node.type in {"function_declaration", "generator_function_declaration"}:
            name_node = next(iter(node.named_children), None)
            symbol_kind = "Function"
        elif node.type == "method_definition":
            name_node = next(iter(node.named_children), None)
            symbol_kind = "Method"
        elif node.type in {"class_declaration", "class"}:
            name_node = next(iter(node.named_children), None)
            symbol_kind = "Class"
        elif node.type == "interface_declaration":
            name_node = next(iter(node.named_children), None)
            symbol_kind = "Interface"
        elif node.type == "variable_declarator":
            named_children = node.named_children
            if len(named_children) < 2:
                return None
            name_node, value_node = named_children[0], named_children[-1]
            if value_node is None or value_node.type not in {
                "arrow_function",
                "function_expression",
                "function",
            }:
                return None
            symbol_kind = "Function"
        else:
            return None
        if name_node is None:
            return None
        name = source[name_node.start_byte : name_node.end_byte].decode()
        if symbol_kind == "Function":
            symbol_kind = "Component" if name[:1].isupper() else "Function"
        if symbol_kind is None or not name:
            return None
        line = node.start_point.row + 1
        return Symbol(
            id=f"symbol:{path}:{name}:{line}",
            name=name,
            kind=symbol_kind,
            line=line,
            end_line=node.end_point.row + 1,
        )

    @classmethod
    def _imports(cls, node: Node, source: bytes) -> tuple[ImportBinding, ...]:
        source_node = node.child_by_field_name("source")
        if source_node is None:
            return ()
        module = cls._unquote(source[source_node.start_byte : source_node.end_byte].decode())
        clause = next((child for child in node.children if child.type == "import_clause"), None)
        if clause is None:
            return (ImportBinding(module, "", ""),)
        bindings: list[ImportBinding] = []
        for child in clause.children:
            if child.type == "identifier":
                name = source[child.start_byte : child.end_byte].decode()
                bindings.append(ImportBinding(module, name, "default"))
            if child.type == "named_imports":
                for specifier in child.children:
                    if specifier.type != "import_specifier":
                        continue
                    names = [
                        source[part.start_byte : part.end_byte].decode()
                        for part in specifier.children
                        if part.type in {"identifier", "type_identifier"}
                    ]
                    if names:
                        bindings.append(
                            ImportBinding(module, names[-1], names[0])
                        )
        return tuple(bindings) or (ImportBinding(module, "", ""),)

    @staticmethod
    def _unquote(value: str) -> str:
        return value[1:-1] if len(value) >= 2 and value[0] in {"'", '"', "`"} else value

    @staticmethod
    def _reference_name(value: str) -> str:
        parts = value.replace("?.", ".").split(".")
        return parts[-1] if parts else ""
