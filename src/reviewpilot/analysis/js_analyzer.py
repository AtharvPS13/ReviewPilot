"""JavaScript/TypeScript AST analyzer for ReviewPilot.

Uses tree-sitter-javascript and tree-sitter-typescript to parse JS/TS
source files and extract functions, classes, and imports, similar to
the Python analyzer.
"""
from __future__ import annotations

from typing import Optional

import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

from reviewpilot.analysis.ast_analyzer import (
    ClassInfo,
    FileAnalysis,
    FunctionInfo,
    _extract_text,
)


def _get_js_parser() -> tuple[Parser, Language]:
    """Get a Tree-sitter parser for JavaScript."""
    lang = Language(tsjavascript.language())
    parser = Parser(lang)
    return parser, lang


def _get_ts_parser() -> tuple[Parser, Language]:
    """Get a Tree-sitter parser for TypeScript."""
    lang = Language(tstypescript.language_typescript())
    parser = Parser(lang)
    return parser, lang


def _extract_js_docstring(node: Node, source: bytes) -> Optional[str]:
    """Extract JSDoc comment preceding a function/class node."""
    # Walk backward through siblings to find a comment node
    prev = node.prev_named_sibling
    if prev and prev.type == "comment":
        text = _extract_text(prev, source)
        # Strip /** */ markers
        if text.startswith("/**") and text.endswith("*/"):
            inner = text[3:-2].strip()
            # Clean up leading asterisks from each line
            lines = []
            for line in inner.splitlines():
                cleaned = line.strip().lstrip("* ").strip()
                if cleaned:
                    lines.append(cleaned)
            return " ".join(lines) if lines else None
    return None


def _get_func_name(node: Node, source: bytes) -> str:
    """Extract function name from various JS/TS function node types."""
    name_node = node.child_by_field_name("name")
    if name_node:
        return _extract_text(name_node, source)

    # Arrow functions assigned to variables: const foo = () => {}
    parent = node.parent
    if parent and parent.type == "variable_declarator":
        var_name = parent.child_by_field_name("name")
        if var_name:
            return _extract_text(var_name, source)

    # Property assignment: foo: function() {} or foo: () => {}
    if parent and parent.type == "pair":
        key_node = parent.child_by_field_name("key")
        if key_node:
            return _extract_text(key_node, source)

    return "<anonymous>"


def _get_params(node: Node, source: bytes) -> str:
    """Extract parameter list as a string."""
    params_node = node.child_by_field_name("parameters")
    if params_node:
        return _extract_text(params_node, source)

    # Arrow functions sometimes have a single param without parens
    param_node = node.child_by_field_name("parameter")
    if param_node:
        return f"({_extract_text(param_node, source)})"

    return "()"


def _get_return_type(node: Node, source: bytes) -> Optional[str]:
    """Extract TypeScript return type annotation if present."""
    ret_node = node.child_by_field_name("return_type")
    if ret_node:
        text = _extract_text(ret_node, source).lstrip(":").strip()
        return text
    return None


def _is_async(node: Node, source: bytes) -> bool:
    """Check if a function node is async."""
    # Check for 'async' keyword in children
    for child in node.children:
        if child.type == "async":
            return True
        text = _extract_text(child, source)
        if text == "async":
            return True
    return False


def _extract_js_function(
    node: Node,
    source: bytes,
    is_method: bool = False,
    class_name: Optional[str] = None,
) -> FunctionInfo:
    """Extract function info from a JS/TS function node."""
    name = _get_func_name(node, source)
    params = _get_params(node, source)
    return_type = _get_return_type(node, source)
    docstring = _extract_js_docstring(node, source)
    async_flag = _is_async(node, source)

    # Determine the actual display range (include variable declaration if arrow fn)
    display_node = node
    parent = node.parent
    if parent and parent.type == "variable_declarator":
        grandparent = parent.parent
        if grandparent and grandparent.type in ("lexical_declaration", "variable_declaration"):
            display_node = grandparent
    elif parent and parent.type == "export_statement":
        display_node = parent

    return FunctionInfo(
        name=name,
        start_line=display_node.start_point.row + 1,
        end_line=display_node.end_point.row + 1,
        parameters=params,
        return_type=return_type,
        docstring=docstring,
        is_method=is_method,
        is_async=async_flag,
        class_name=class_name,
    )


def _extract_js_class(node: Node, source: bytes) -> ClassInfo:
    """Extract class info from a JS/TS class_declaration node."""
    name_node = node.child_by_field_name("name")
    name = _extract_text(name_node, source) if name_node else "Unknown"

    # Extract superclass
    bases: list[str] = []
    heritage_node = None
    for child in node.children:
        if child.type == "class_heritage":
            heritage_node = child
            break
    if heritage_node:
        for child in heritage_node.children:
            if child.type == "identifier":
                bases.append(_extract_text(child, source))

    docstring = _extract_js_docstring(node, source)

    display_node = node
    parent = node.parent
    if parent and parent.type == "export_statement":
        display_node = parent

    return ClassInfo(
        name=name,
        start_line=display_node.start_point.row + 1,
        end_line=display_node.end_point.row + 1,
        base_classes=bases,
        docstring=docstring,
    )


FUNCTION_TYPES = frozenset({
    "function_declaration",
    "function",
    "arrow_function",
    "generator_function_declaration",
    "generator_function",
})

METHOD_TYPES = frozenset({
    "method_definition",
})


def analyze_js_file(source_code: str, file_path: str = "", is_typescript: bool = False) -> FileAnalysis:
    """Analyze a JavaScript or TypeScript file.

    Args:
        source_code: Source code string
        file_path: File path for metadata
        is_typescript: Use TypeScript parser if True

    Returns:
        FileAnalysis with extracted functions, classes, and imports
    """
    if is_typescript:
        parser, lang = _get_ts_parser()
    else:
        parser, lang = _get_js_parser()

    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    language_name = "typescript" if is_typescript else "javascript"
    analysis = FileAnalysis(file_path=file_path, language=language_name)

    def walk_node(node: Node, class_name: Optional[str] = None) -> None:
        for child in node.children:
            actual = child

            # Unwrap export statements
            if child.type == "export_statement":
                for export_child in child.children:
                    if export_child.type in FUNCTION_TYPES | METHOD_TYPES | {"class_declaration"}:
                        actual = export_child
                        break

            if actual.type in FUNCTION_TYPES:
                func = _extract_js_function(actual, source_bytes, is_method=False, class_name=class_name)
                if func.name != "<anonymous>":
                    analysis.functions.append(func)

            elif actual.type in METHOD_TYPES:
                func = _extract_js_function(actual, source_bytes, is_method=True, class_name=class_name)
                analysis.functions.append(func)
                if class_name:
                    for cls in analysis.classes:
                        if cls.name == class_name:
                            cls.methods.append(func)
                            break

            elif actual.type == "class_declaration":
                cls_info = _extract_js_class(actual, source_bytes)
                analysis.classes.append(cls_info)
                # Recurse into class body
                body = actual.child_by_field_name("body")
                if body:
                    walk_node(body, class_name=cls_info.name)

            elif actual.type == "import_statement":
                analysis.imports.append(_extract_text(actual, source_bytes))

            # Handle variable declarations that contain arrow functions
            elif actual.type in ("lexical_declaration", "variable_declaration"):
                for declarator in actual.children:
                    if declarator.type == "variable_declarator":
                        value = declarator.child_by_field_name("value")
                        if value and value.type == "arrow_function":
                            func = _extract_js_function(
                                value, source_bytes, is_method=False, class_name=class_name
                            )
                            if func.name != "<anonymous>":
                                analysis.functions.append(func)

    walk_node(tree.root_node)
    return analysis
