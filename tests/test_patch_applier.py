"""Tests for the patch applier."""
from __future__ import annotations

import pytest
from pathlib import Path

from reviewpilot.sandbox.patch_applier import (
    apply_line_replacement,
    create_patched_copy,
    get_original_lines,
    PatchError,
)
from reviewpilot.review.models import CodeReviewIssue, Severity, Category


SAMPLE_FILE_CONTENT = """\
import os

def greet(name):
    print(f"Hello, {name}")
    return name

def add(a, b):
    return a + b
"""


class TestApplyLineReplacement:
    def test_replace_single_line(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text(SAMPLE_FILE_CONTENT)

        result = apply_line_replacement(f, 4, 4, '    print(f"Hi, {name}")')
        assert '    print(f"Hi, {name}")' in result
        assert '    print(f"Hello, {name}")' not in result

    def test_replace_multiple_lines(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text(SAMPLE_FILE_CONTENT)

        replacement = '    if not name:\n        raise ValueError("name required")\n    print(f"Hello, {name}")'
        result = apply_line_replacement(f, 4, 4, replacement)
        assert "ValueError" in result
        assert result.count("\n") > SAMPLE_FILE_CONTENT.count("\n")

    def test_preserves_surrounding_lines(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text(SAMPLE_FILE_CONTENT)

        result = apply_line_replacement(f, 4, 4, '    print("changed")')
        assert "import os" in result
        assert "def greet(name):" in result
        assert "def add(a, b):" in result

    def test_invalid_start_line_raises(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text(SAMPLE_FILE_CONTENT)

        with pytest.raises(PatchError, match="Invalid line range"):
            apply_line_replacement(f, 0, 5, "replacement")

    def test_start_after_end_raises(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text(SAMPLE_FILE_CONTENT)

        with pytest.raises(PatchError, match="Invalid line range"):
            apply_line_replacement(f, 5, 3, "replacement")

    def test_start_beyond_file_raises(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text(SAMPLE_FILE_CONTENT)

        with pytest.raises(PatchError, match="exceeds file length"):
            apply_line_replacement(f, 999, 999, "replacement")

    def test_nonexistent_file_raises(self, tmp_path):
        f = tmp_path / "missing.py"
        with pytest.raises(PatchError, match="Cannot read"):
            apply_line_replacement(f, 1, 1, "replacement")


class TestCreatePatchedCopy:
    def test_creates_temp_copy(self, tmp_path):
        src = tmp_path / "repo"
        src.mkdir()
        (src / "main.py").write_text(SAMPLE_FILE_CONTENT)

        issue = CodeReviewIssue(
            file_path="main.py", start_line=4, end_line=4,
            severity=Severity.MINOR, category=Category.BUG,
            reasoning="test", explanation="test",
            can_generate_patch=True, replacement_code='    print("patched")',
        )

        patched = create_patched_copy(src, issue)
        try:
            patched_content = (patched / "main.py").read_text()
            assert "patched" in patched_content
            assert "Hello" not in patched_content
        finally:
            import shutil
            shutil.rmtree(patched.parent, ignore_errors=True)

    def test_no_replacement_code_raises(self, tmp_path):
        issue = CodeReviewIssue(
            file_path="main.py", start_line=1, end_line=1,
            severity=Severity.MINOR, category=Category.BUG,
            reasoning="test", explanation="test",
            can_generate_patch=False,
        )
        with pytest.raises(PatchError, match="no replacement_code"):
            create_patched_copy(tmp_path, issue)


class TestGetOriginalLines:
    def test_extracts_lines(self, tmp_path):
        f = tmp_path / "main.py"
        f.write_text(SAMPLE_FILE_CONTENT)

        result = get_original_lines(tmp_path, "main.py", 3, 5)
        assert "def greet" in result
        assert "return name" in result

    def test_missing_file_returns_empty(self, tmp_path):
        result = get_original_lines(tmp_path, "missing.py", 1, 3)
        assert result == ""
