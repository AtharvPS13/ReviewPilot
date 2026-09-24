"""AST analyzer for ReviewPilot using Tree-sitter.

Parses source code into ASTs and extracts function/class information
with precise line numbers for mapping back to diff changes.
Currently supports Python. Other languages will be added in Week 5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser


@dataclass
class FunctionInfo:
    """Information about a function/method extracted from AST."""

    name: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    parameters: str  # Parameter string e.g., '(self, x: int, y: int)'
    return_type: Optional[str] = None
    docstring: Optional[str] = None
    is_method: bool = False  # True if inside a class
    is_async: bool = False
    class_name: Optional[str] = None  # Parent class name if method
    decorators: list[str] = field(default_factory=list)

    @property
    def signature(self) -> str:
        """Human-readable function signature for skeleton context."""
        prefix = "async " if self.is_async else ""
        ret = f" -> {self.return_type}" if self.return_type else ""
        class_prefix = f"{self.class_name}." if self.class_name else ""
        decorators_str = "".join(f"@{d}\n" for d in self.decorators)
        return f"{decorators_str}{prefix}def {class_prefix}{self.name}{self.parameters}{ret}"

    @property
    def skeleton(self) -> str:
        """Skeleton representation: signature + docstring (no body).

        Used for token-efficient context in LLM prompts.
        """
        sig = self.signature
        if self.docstring:
            return f'{sig}:\n    """{self.docstring}"""'
        return f"{sig}: ..."


@dataclass
class ClassInfo:
    """Information about a class extracted from AST."""

    name: str
    start_line: int
    end_line: int
    methods: list[FunctionInfo] = field(default_factory=list)
    base_classes: list[str] = field(default_factory=list)
    docstring: Optional[str] = None


@dataclass
class FileAnalysis:
    """Complete AST analysis result for a single file."""

    file_path: str
    language: str
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)

    def get_functions_in_range(self, start_line: int, end_line: int) -> list[FunctionInfo]:
        """Get all functions that overlap with the given line range."""
        result = []
        for func in self.functions:
            if not (func.end_line < start_line or func.start_line > end_line):
                result.append(func)
        return result

    def get_modified_functions(self, changed_lines: set[int]) -> list[FunctionInfo]:
        """Get functions that contain any of the changed lines."""
        result = []
        for func in self.functions:
            func_lines = set(range(func.start_line, func.end_line + 1))
            if func_lines & changed_lines:
                result.append(func)
        return result

    def get_unmodified_functions(self, changed_lines: set[int]) -> list[FunctionInfo]:
        """Get functions that were NOT modified — these become skeleton context."""
        modified = self.get_modified_functions(changed_lines)
        return [f for f in self.functions if f not in modified]


# --- Language Detection ---


EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
    ".rb": "ruby",
    ".cpp": "cpp",
    ".c": "c",
    ".cs": "csharp",
}


def detect_language(file_path: str) -> Optional[str]:
    """Detect programming language from file extension."""
    suffix = Path(file_path).suffix.lower()
    return EXTENSION_MAP.get(suffix)


# --- Tree-sitter Helpers ---


def _get_python_parser() -> tuple[Parser, Language]:
    """Get a Tree-sitter parser for Python."""
    lang = Language(tspython.language())
    parser = Parser(lang)
    return parser, lang


def _extract_text(node: Node, source: bytes) -> str:
    """Extract the text of a node from source bytes."""
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _extract_docstring(body_node: Node, source: bytes) -> Optional[str]:
    """Extract docstring from a function/class body node."""
    if body_node is None or body_node.child_count == 0:
        return None

    first_stmt = body_node.children[0]
    if first_stmt.type == "expression_statement" and first_stmt.child_count > 0:
        expr = first_stmt.children[0]
        if expr.type == "string":
            text = _extract_text(expr, source)
            for quote in ['"""', "'''"]:
                if text.startswith(quote) and text.endswith(quote):
                    return text[3:-3].strip()
    return None


def _extract_decorators(node: Node, source: bytes) -> list[str]:
    """Extract decorator names from a decorated definition."""
    decorators: list[str] = []
    # Check the parent or preceding siblings for decorator nodes
    # In Tree-sitter Python, decorators are part of `decorated_definition`
    parent = node.parent
    if parent and parent.type == "decorated_definition":
        for child in parent.children:
            if child.type == "decorator":
                # The decorator text without the '@' prefix
                dec_text = _extract_text(child, source).lstrip("@").strip()
                decorators.append(dec_text)
    return decorators


def _extract_function_info(
    node: Node,
    source: bytes,
    is_method: bool = False,
    class_name: Optional[str] = None,
) -> FunctionInfo:
    """Extract function information from a function_definition node."""
    name_node = node.child_by_field_name("name")
    params_node = node.child_by_field_name("parameters")
    return_type_node = node.child_by_field_name("return_type")
    body_node = node.child_by_field_name("body")

    name = _extract_text(name_node, source) if name_node else "unknown"
    parameters = _extract_text(params_node, source) if params_node else "()"
    return_type = _extract_text(return_type_node, source) if return_type_node else None
    docstring = _extract_docstring(body_node, source) if body_node else None
    decorators = _extract_decorators(node, source)

    is_async = node.type in ("async_function_definition",)

    # Use the decorated_definition's line range if it exists
    display_node = node
    if node.parent and node.parent.type == "decorated_definition":
        display_node = node.parent

    return FunctionInfo(
        name=name,
        start_line=display_node.start_point.row + 1,
        end_line=display_node.end_point.row + 1,
        parameters=parameters,
        return_type=return_type,
        docstring=docstring,
        is_method=is_method,
        is_async=is_async,
        class_name=class_name,
        decorators=decorators,
    )


def analyze_python_file(source_code: str, file_path: str = "") -> FileAnalysis:
    """Analyze a Python file using Tree-sitter and extract functions, classes, imports.

    Args:
        source_code: The Python source code as a string
        file_path: Path to the file (for metadata)

    Returns:
        FileAnalysis containing all extracted information
    """
    parser, lang = _get_python_parser()
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    analysis = FileAnalysis(file_path=file_path, language="python")

    def walk_node(node: Node, class_name: Optional[str] = None) -> None:
        """Recursively walk the AST and extract information."""
        for child in node.children:
            # Handle decorated definitions — unwrap to get the actual definition
            actual_child = child
            if child.type == "decorated_definition":
                # Find the actual function/class definition inside
                for deco_child in child.children:
                    if deco_child.type in (
                        "function_definition",
                        "async_function_definition",
                        "class_definition",
                    ):
                        actual_child = deco_child
                        break

            if actual_child.type in ("function_definition", "async_function_definition"):
                is_method = class_name is not None
                func_info = _extract_function_info(
                    actual_child, source_bytes, is_method=is_method, class_name=class_name
                )
                analysis.functions.append(func_info)

                # If inside a class, also add to the class's methods
                if class_name is not None:
                    for cls in analysis.classes:
                        if cls.name == class_name:
                            cls.methods.append(func_info)
                            break

            elif actual_child.type == "class_definition":
                cls_name_node = actual_child.child_by_field_name("name")
                cls_name = _extract_text(cls_name_node, source_bytes) if cls_name_node else "Unknown"

                # Extract base classes
                bases: list[str] = []
                superclasses_node = actual_child.child_by_field_name("superclasses")
                if superclasses_node:
                    # argument_list contains the base class identifiers
                    for base_child in superclasses_node.children:
                        if base_child.type in ("identifier", "attribute"):
                            bases.append(_extract_text(base_child, source_bytes))

                body_node = actual_child.child_by_field_name("body")
                cls_docstring = _extract_docstring(body_node, source_bytes) if body_node else None

                display_node = actual_child
                if actual_child.parent and actual_child.parent.type == "decorated_definition":
                    display_node = actual_child.parent

                cls_info = ClassInfo(
                    name=cls_name,
                    start_line=display_node.start_point.row + 1,
                    end_line=display_node.end_point.row + 1,
                    base_classes=bases,
                    docstring=cls_docstring,
                )
                analysis.classes.append(cls_info)

                # Recurse into class body to find methods
                if body_node:
                    walk_node(body_node, class_name=cls_name)

            elif actual_child.type == "import_statement":
                analysis.imports.append(_extract_text(actual_child, source_bytes))

            elif actual_child.type == "import_from_statement":
                analysis.imports.append(_extract_text(actual_child, source_bytes))

            # Don't recurse into function/class bodies for top-level scan
            # (class bodies are handled via explicit recursion above)

    walk_node(tree.root_node)
    return analysis


def analyze_file(source_code: str, file_path: str) -> Optional[FileAnalysis]:
    """Analyze a source file using the appropriate language parser.

    Supports Python, JavaScript, and TypeScript. More languages planned.

    Args:
        source_code: The source code as a string
        file_path: Path to the file (used for language detection)

    Returns:
        FileAnalysis if the language is supported, None otherwise
    """
    language = detect_language(file_path)
    if language is None:
        return None

    if language == "python":
        return analyze_python_file(source_code, file_path)

    if language in ("javascript", "typescript"):
        from reviewpilot.analysis.js_analyzer import analyze_js_file
        return analyze_js_file(source_code, file_path, is_typescript=(language == "typescript"))

    # Other languages to be added later
    return None
