"""Tests for the JavaScript/TypeScript AST analyzer."""
from __future__ import annotations

import pytest

from reviewpilot.analysis.js_analyzer import analyze_js_file
from reviewpilot.analysis.ast_analyzer import analyze_file


JS_FUNCTIONS = """\
import { readFile } from 'fs/promises';

function greet(name) {
    return `Hello, ${name}!`;
}

const add = (a, b) => {
    return a + b;
};

async function fetchData(url) {
    const response = await fetch(url);
    return response.json();
}
"""

JS_CLASS = """\
class Calculator {
    constructor(precision) {
        this.precision = precision;
    }

    add(a, b) {
        return parseFloat((a + b).toFixed(this.precision));
    }

    subtract(a, b) {
        return parseFloat((a - b).toFixed(this.precision));
    }
}
"""

JS_EXPORTS = """\
export function publicFn(x) {
    return x * 2;
}

export class UserService {
    getUser(id) {
        return { id, name: 'test' };
    }
}
"""

JS_INHERITANCE = """\
class Animal {
    speak() {
        return '';
    }
}

class Dog extends Animal {
    speak() {
        return 'Woof!';
    }
}
"""

TS_TYPED = """\
interface User {
    id: number;
    name: string;
}

function getUser(id: number): User {
    return { id, name: 'test' };
}

const multiply = (a: number, b: number): number => {
    return a * b;
};
"""


class TestJSFunctions:
    def test_finds_regular_function(self):
        result = analyze_js_file(JS_FUNCTIONS, "app.js")
        names = [f.name for f in result.functions]
        assert "greet" in names

    def test_finds_arrow_function(self):
        result = analyze_js_file(JS_FUNCTIONS, "app.js")
        names = [f.name for f in result.functions]
        assert "add" in names

    def test_finds_async_function(self):
        result = analyze_js_file(JS_FUNCTIONS, "app.js")
        fetch_fn = next(f for f in result.functions if f.name == "fetchData")
        assert fetch_fn.is_async

    def test_extracts_parameters(self):
        result = analyze_js_file(JS_FUNCTIONS, "app.js")
        greet = next(f for f in result.functions if f.name == "greet")
        assert "name" in greet.parameters

    def test_extracts_imports(self):
        result = analyze_js_file(JS_FUNCTIONS, "app.js")
        assert len(result.imports) >= 1
        assert any("readFile" in imp for imp in result.imports)


class TestJSClasses:
    def test_finds_class(self):
        result = analyze_js_file(JS_CLASS, "calc.js")
        assert len(result.classes) == 1
        assert result.classes[0].name == "Calculator"

    def test_finds_methods(self):
        result = analyze_js_file(JS_CLASS, "calc.js")
        cls = result.classes[0]
        method_names = [m.name for m in cls.methods]
        assert "add" in method_names
        assert "subtract" in method_names

    def test_methods_marked_as_methods(self):
        result = analyze_js_file(JS_CLASS, "calc.js")
        for f in result.functions:
            if f.name in ("add", "subtract", "constructor"):
                assert f.is_method
                assert f.class_name == "Calculator"


class TestJSExports:
    def test_exported_function(self):
        result = analyze_js_file(JS_EXPORTS, "module.js")
        names = [f.name for f in result.functions]
        assert "publicFn" in names

    def test_exported_class(self):
        result = analyze_js_file(JS_EXPORTS, "module.js")
        class_names = [c.name for c in result.classes]
        assert "UserService" in class_names


class TestJSInheritance:
    def test_base_class(self):
        result = analyze_js_file(JS_INHERITANCE, "animals.js")
        dog = next(c for c in result.classes if c.name == "Dog")
        assert "Animal" in dog.base_classes


class TestTypeScript:
    def test_typed_function(self):
        result = analyze_js_file(TS_TYPED, "utils.ts", is_typescript=True)
        get_user = next(f for f in result.functions if f.name == "getUser")
        assert get_user.return_type is not None
        assert "User" in get_user.return_type

    def test_typed_arrow_function(self):
        result = analyze_js_file(TS_TYPED, "utils.ts", is_typescript=True)
        multiply = next(f for f in result.functions if f.name == "multiply")
        assert multiply.return_type is not None
        assert "number" in multiply.return_type


class TestAnalyzeFileDispatch:
    """Test that analyze_file correctly dispatches to JS/TS analyzers."""

    def test_js_file(self):
        result = analyze_file("function foo() { return 1; }", "app.js")
        assert result is not None
        assert result.language == "javascript"

    def test_ts_file(self):
        result = analyze_file("function bar(): number { return 1; }", "app.ts")
        assert result is not None
        assert result.language == "typescript"

    def test_jsx_file(self):
        result = analyze_file("function App() { return null; }", "App.jsx")
        assert result is not None
        assert result.language == "javascript"

    def test_tsx_file(self):
        result = analyze_file("function App(): JSX.Element { return null; }", "App.tsx")
        assert result is not None
        assert result.language == "typescript"
