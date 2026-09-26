"""Tests for context builder."""
from __future__ import annotations

import pytest

from reviewpilot.analysis.context_builder import (
    FileContext,
    FunctionContext,
    build_file_context,
    _add_line_numbers,
)
from reviewpilot.ingestion.diff_parser import parse_unified_diff


SAMPLE_DIFF = """\
diff --git a/src/calc.py b/src/calc.py
index abc..def 100644
--- a/src/calc.py
+++ b/src/calc.py
@@ -3,4 +3,6 @@ def add(a, b):
     return a + b
 
 def multiply(a, b):
+    if a == 0 or b == 0:
+        return 0
     return a * b
"""

SAMPLE_SOURCE = """\
import math

def add(a, b):
    return a + b

def multiply(a, b):
    if a == 0 or b == 0:
        return 0
    return a * b
"""


class TestAddLineNumbers:
    def test_basic_numbering(self):
        result = _add_line_numbers("line1\nline2\nline3", 10)
        assert "  10 | line1" in result
        assert "  11 | line2" in result
        assert "  12 | line3" in result

    def test_single_line(self):
        result = _add_line_numbers("only line", 1)
        assert "   1 | only line" in result


class TestBuildFileContext:
    def test_returns_context_for_python(self):
        patch_set = parse_unified_diff(SAMPLE_DIFF)
        file_diff = patch_set.files[0]
        ctx = build_file_context(file_diff, SAMPLE_SOURCE)
        assert ctx is not None
        assert ctx.language == "python"
        assert ctx.file_path == "src/calc.py"

    def test_modified_functions_found(self):
        patch_set = parse_unified_diff(SAMPLE_DIFF)
        file_diff = patch_set.files[0]
        ctx = build_file_context(file_diff, SAMPLE_SOURCE)
        assert ctx is not None
        modified_names = [f.name for f in ctx.modified_functions]
        assert "multiply" in modified_names

    def test_unmodified_as_skeleton(self):
        patch_set = parse_unified_diff(SAMPLE_DIFF)
        file_diff = patch_set.files[0]
        ctx = build_file_context(file_diff, SAMPLE_SOURCE)
        assert ctx is not None
        # "add" was not modified, should be in skeletons
        assert any("add" in s for s in ctx.skeleton_functions)

    def test_prompt_text_output(self):
        patch_set = parse_unified_diff(SAMPLE_DIFF)
        file_diff = patch_set.files[0]
        ctx = build_file_context(file_diff, SAMPLE_SOURCE)
        assert ctx is not None
        text = ctx.to_prompt_text()
        assert "Modified Functions" in text
        assert "multiply" in text
        assert "src/calc.py" in text

    def test_returns_none_for_unsupported(self):
        patch_set = parse_unified_diff(SAMPLE_DIFF.replace("calc.py", "calc.xyz"))
        file_diff = patch_set.files[0]
        ctx = build_file_context(file_diff, SAMPLE_SOURCE)
        assert ctx is None


class TestFunctionContext:
    def test_prompt_text(self):
        fc = FunctionContext(
            name="multiply",
            full_source="   6 | def multiply(a, b):\n   7 |     return a * b",
            start_line=6,
            end_line=7,
            signature="def multiply(a, b)",
            changed_line_numbers=[7],
        )
        text = fc.to_prompt_text()
        assert "multiply" in text
        assert "6-7" in text
        assert "7" in text  # changed line


class TestFileContext:
    def test_to_prompt_with_all_sections(self):
        ctx = FileContext(
            file_path="src/app.py",
            language="python",
            modified_functions=[
                FunctionContext(
                    name="run", full_source="def run(): pass",
                    start_line=1, end_line=1, signature="def run()",
                    changed_line_numbers=[1],
                )
            ],
            skeleton_functions=["def helper(): ..."],
            imports=["import os"],
            raw_diff_hunks=["+    new line"],
        )
        text = ctx.to_prompt_text()
        assert "Imports" in text
        assert "Modified Functions" in text
        assert "Dependency Context" in text
        assert "Diff Hunks" in text
