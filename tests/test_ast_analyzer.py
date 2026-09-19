"""Tests for the Tree-sitter AST analyzer."""
from __future__ import annotations

import pytest

from reviewpilot.analysis.ast_analyzer import (
    ClassInfo,
    FileAnalysis,
    FunctionInfo,
    analyze_file,
    analyze_python_file,
    detect_language,
)


# ── Test Fixtures ──────────────────────────────────────────────────────

SIMPLE_FUNCTIONS = """\
import os
from pathlib import Path

def greet(name: str) -> str:
    \"\"\"Greet someone by name.\"\"\"
    return f"Hello, {name}!"

def add(a: int, b: int) -> int:
    return a + b
"""

CLASS_WITH_METHODS = """\
from dataclasses import dataclass

@dataclass
class Calculator:
    \"\"\"A simple calculator.\"\"\"
    
    precision: int = 2
    
    def add(self, a: float, b: float) -> float:
        \"\"\"Add two numbers.\"\"\"
        return round(a + b, self.precision)
    
    def subtract(self, a: float, b: float) -> float:
        return round(a - b, self.precision)
"""

ASYNC_FUNCTION = """\
import asyncio

async def fetch_data(url: str) -> dict:
    \"\"\"Fetch data from a URL.\"\"\"
    async with aiohttp.ClientSession() as session:
        response = await session.get(url)
        return await response.json()
"""

DECORATED_FUNCTION = """\
from functools import lru_cache

@lru_cache(maxsize=128)
def expensive_computation(n: int) -> int:
    \"\"\"Compute something expensive.\"\"\"
    return sum(range(n))
"""

INHERITANCE_CLASS = """\
class Animal:
    def speak(self) -> str:
        return ""

class Dog(Animal):
    \"\"\"A good boy.\"\"\"
    def speak(self) -> str:
        return "Woof!"
"""


# ── Tests ──────────────────────────────────────────────────────────────


class TestDetectLanguage:
    def test_python(self):
        assert detect_language("src/main.py") == "python"

    def test_javascript(self):
        assert detect_language("app.js") == "javascript"
        assert detect_language("component.jsx") == "javascript"

    def test_typescript(self):
        assert detect_language("utils.ts") == "typescript"
        assert detect_language("App.tsx") == "typescript"

    def test_go(self):
        assert detect_language("main.go") == "go"

    def test_unknown(self):
        assert detect_language("data.csv") is None
        assert detect_language("Makefile") is None


class TestAnalyzePythonFunctions:
    def test_finds_two_functions(self):
        result = analyze_python_file(SIMPLE_FUNCTIONS, "test.py")
        assert len(result.functions) == 2

    def test_function_names(self):
        result = analyze_python_file(SIMPLE_FUNCTIONS, "test.py")
        names = [f.name for f in result.functions]
        assert "greet" in names
        assert "add" in names

    def test_function_parameters(self):
        result = analyze_python_file(SIMPLE_FUNCTIONS, "test.py")
        greet = next(f for f in result.functions if f.name == "greet")
        assert "name: str" in greet.parameters

    def test_function_return_type(self):
        result = analyze_python_file(SIMPLE_FUNCTIONS, "test.py")
        greet = next(f for f in result.functions if f.name == "greet")
        assert greet.return_type == "str"

    def test_function_docstring(self):
        result = analyze_python_file(SIMPLE_FUNCTIONS, "test.py")
        greet = next(f for f in result.functions if f.name == "greet")
        assert greet.docstring == "Greet someone by name."

    def test_function_without_docstring(self):
        result = analyze_python_file(SIMPLE_FUNCTIONS, "test.py")
        add = next(f for f in result.functions if f.name == "add")
        assert add.docstring is None

    def test_functions_are_not_methods(self):
        result = analyze_python_file(SIMPLE_FUNCTIONS, "test.py")
        for f in result.functions:
            assert not f.is_method
            assert f.class_name is None


class TestAnalyzePythonClasses:
    def test_finds_class(self):
        result = analyze_python_file(CLASS_WITH_METHODS, "test.py")
        assert len(result.classes) == 1
        assert result.classes[0].name == "Calculator"

    def test_class_docstring(self):
        result = analyze_python_file(CLASS_WITH_METHODS, "test.py")
        assert result.classes[0].docstring == "A simple calculator."

    def test_class_methods(self):
        result = analyze_python_file(CLASS_WITH_METHODS, "test.py")
        cls = result.classes[0]
        method_names = [m.name for m in cls.methods]
        assert "add" in method_names
        assert "subtract" in method_names

    def test_methods_marked_as_methods(self):
        result = analyze_python_file(CLASS_WITH_METHODS, "test.py")
        for func in result.functions:
            if func.name in ("add", "subtract"):
                assert func.is_method
                assert func.class_name == "Calculator"

    def test_inheritance(self):
        result = analyze_python_file(INHERITANCE_CLASS, "test.py")
        dog = next(c for c in result.classes if c.name == "Dog")
        assert "Animal" in dog.base_classes


class TestAnalyzeAsyncFunction:
    def test_is_async(self):
        result = analyze_python_file(ASYNC_FUNCTION, "test.py")
        func = result.functions[0]
        assert func.is_async
        assert func.name == "fetch_data"


class TestAnalyzeDecorators:
    def test_has_decorator(self):
        result = analyze_python_file(DECORATED_FUNCTION, "test.py")
        func = result.functions[0]
        assert len(func.decorators) > 0
        assert any("lru_cache" in d for d in func.decorators)


class TestAnalyzeImports:
    def test_extracts_imports(self):
        result = analyze_python_file(SIMPLE_FUNCTIONS, "test.py")
        assert len(result.imports) == 2
        assert any("os" in imp for imp in result.imports)
        assert any("pathlib" in imp for imp in result.imports)


class TestFunctionInfoProperties:
    def test_signature(self):
        func = FunctionInfo(
            name="greet", start_line=1, end_line=3,
            parameters="(name: str)", return_type="str",
        )
        assert func.signature == "def greet(name: str) -> str"

    def test_async_signature(self):
        func = FunctionInfo(
            name="fetch", start_line=1, end_line=3,
            parameters="(url: str)", return_type="dict", is_async=True,
        )
        assert func.signature == "async def fetch(url: str) -> dict"

    def test_method_signature(self):
        func = FunctionInfo(
            name="add", start_line=1, end_line=3,
            parameters="(self, a: int, b: int)", return_type="int",
            is_method=True, class_name="Calculator",
        )
        assert "Calculator.add" in func.signature

    def test_skeleton_with_docstring(self):
        func = FunctionInfo(
            name="greet", start_line=1, end_line=3,
            parameters="(name: str)", return_type="str",
            docstring="Say hello.",
        )
        skel = func.skeleton
        assert "def greet(name: str) -> str:" in skel
        assert "Say hello." in skel

    def test_skeleton_without_docstring(self):
        func = FunctionInfo(
            name="add", start_line=1, end_line=2,
            parameters="(a: int, b: int)", return_type="int",
        )
        assert func.skeleton == "def add(a: int, b: int) -> int: ..."


class TestGetModifiedFunctions:
    def test_finds_modified(self):
        analysis = FileAnalysis(
            file_path="test.py", language="python",
            functions=[
                FunctionInfo(name="foo", start_line=1, end_line=5, parameters="()"),
                FunctionInfo(name="bar", start_line=10, end_line=15, parameters="()"),
            ]
        )
        modified = analysis.get_modified_functions({3, 4})  # Lines inside foo
        assert len(modified) == 1
        assert modified[0].name == "foo"

    def test_unmodified_excluded(self):
        analysis = FileAnalysis(
            file_path="test.py", language="python",
            functions=[
                FunctionInfo(name="foo", start_line=1, end_line=5, parameters="()"),
                FunctionInfo(name="bar", start_line=10, end_line=15, parameters="()"),
            ]
        )
        unmodified = analysis.get_unmodified_functions({3, 4})
        assert len(unmodified) == 1
        assert unmodified[0].name == "bar"


class TestAnalyzeFile:
    def test_python_file(self):
        result = analyze_file("x = 1\n", "test.py")
        assert result is not None
        assert result.language == "python"

    def test_unsupported_file(self):
        result = analyze_file("data", "test.csv")
        assert result is None
