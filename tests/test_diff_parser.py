"""Tests for the unified diff parser."""
from __future__ import annotations

import pytest

from reviewpilot.ingestion.diff_parser import (
    ChangedLine,
    FileDiff,
    PatchSet,
    parse_unified_diff,
)


# ── Test Fixtures ──────────────────────────────────────────────────────

SIMPLE_MODIFICATION_DIFF = """\
diff --git a/src/math.py b/src/math.py
index abc1234..def5678 100644
--- a/src/math.py
+++ b/src/math.py
@@ -10,6 +10,9 @@ def calculate(x: int) -> int:
     result = x * 2
+    # Added input validation
+    if result > 100:
+        return 100
     return result
"""

NEW_FILE_DIFF = """\
diff --git a/src/new_module.py b/src/new_module.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/src/new_module.py
@@ -0,0 +1,5 @@
+\"\"\"A brand new module.\"\"\"
+
+
+def hello() -> str:
+    return "world"
"""

DELETED_FILE_DIFF = """\
diff --git a/src/old_module.py b/src/old_module.py
deleted file mode 100644
index abc1234..0000000
--- a/src/old_module.py
+++ /dev/null
@@ -1,3 +0,0 @@
-\"\"\"This module is being removed.\"\"\"
-
-OLD_CONSTANT = 42
"""

MULTIPLE_FILES_DIFF = """\
diff --git a/src/auth.py b/src/auth.py
index abc1234..def5678 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -5,3 +5,4 @@ def login(user: str) -> bool:
     token = create_token(user)
+    log_login_attempt(user)
     return True
diff --git a/src/utils.py b/src/utils.py
index abc1234..def5678 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -1,3 +1,4 @@
+import logging
 
 def format_name(name: str) -> str:
     return name.strip().title()
"""

MULTIPLE_HUNKS_DIFF = """\
diff --git a/src/calculator.py b/src/calculator.py
index abc1234..def5678 100644
--- a/src/calculator.py
+++ b/src/calculator.py
@@ -3,4 +3,5 @@ def add(a, b):
     \"\"\"Add two numbers.\"\"\"
+    # Validate inputs
     return a + b
 
@@ -15,4 +16,5 @@ def multiply(a, b):
     \"\"\"Multiply two numbers.\"\"\"
+    # Validate inputs
     return a * b
"""

BINARY_FILE_DIFF = """\
diff --git a/assets/logo.png b/assets/logo.png
new file mode 100644
index 0000000..abc1234
Binary files /dev/null and b/assets/logo.png differ
"""


# ── Tests ──────────────────────────────────────────────────────────────


class TestParseSimpleModification:
    def test_parses_single_file(self):
        result = parse_unified_diff(SIMPLE_MODIFICATION_DIFF)
        assert len(result.files) == 1

    def test_file_path(self):
        result = parse_unified_diff(SIMPLE_MODIFICATION_DIFF)
        assert result.files[0].path == "src/math.py"

    def test_not_new_or_deleted(self):
        result = parse_unified_diff(SIMPLE_MODIFICATION_DIFF)
        f = result.files[0]
        assert not f.is_new_file
        assert not f.is_deleted_file
        assert not f.is_binary

    def test_has_one_hunk(self):
        result = parse_unified_diff(SIMPLE_MODIFICATION_DIFF)
        assert len(result.files[0].hunks) == 1

    def test_hunk_header_values(self):
        result = parse_unified_diff(SIMPLE_MODIFICATION_DIFF)
        hunk = result.files[0].hunks[0]
        assert hunk.old_start == 10
        assert hunk.new_start == 10

    def test_changed_lines_are_additions(self):
        result = parse_unified_diff(SIMPLE_MODIFICATION_DIFF)
        changed = result.files[0].changed_lines
        # Lines 11, 12, 13 are the three added lines
        assert 11 in changed
        assert 12 in changed
        assert 13 in changed

    def test_context_lines_in_all_new_file_lines(self):
        result = parse_unified_diff(SIMPLE_MODIFICATION_DIFF)
        all_lines = result.files[0].all_new_file_lines
        # Context lines should also be present (line 10 = "result = x * 2")
        assert 10 in all_lines


class TestParseNewFile:
    def test_is_new_file(self):
        result = parse_unified_diff(NEW_FILE_DIFF)
        assert result.files[0].is_new_file

    def test_old_path_is_none(self):
        result = parse_unified_diff(NEW_FILE_DIFF)
        assert result.files[0].old_path is None

    def test_new_path(self):
        result = parse_unified_diff(NEW_FILE_DIFF)
        assert result.files[0].new_path == "src/new_module.py"

    def test_all_lines_are_additions(self):
        result = parse_unified_diff(NEW_FILE_DIFF)
        hunk = result.files[0].hunks[0]
        assert all(c.change_type == "add" for c in hunk.changes)


class TestParseDeletedFile:
    def test_is_deleted_file(self):
        result = parse_unified_diff(DELETED_FILE_DIFF)
        assert result.files[0].is_deleted_file

    def test_new_path_is_none(self):
        result = parse_unified_diff(DELETED_FILE_DIFF)
        assert result.files[0].new_path is None

    def test_all_lines_are_deletions(self):
        result = parse_unified_diff(DELETED_FILE_DIFF)
        hunk = result.files[0].hunks[0]
        assert all(c.change_type == "delete" for c in hunk.changes)


class TestParseMultipleFiles:
    def test_parses_two_files(self):
        result = parse_unified_diff(MULTIPLE_FILES_DIFF)
        assert len(result.files) == 2

    def test_file_paths(self):
        result = parse_unified_diff(MULTIPLE_FILES_DIFF)
        paths = result.changed_file_paths
        assert "src/auth.py" in paths
        assert "src/utils.py" in paths

    def test_each_file_has_changes(self):
        result = parse_unified_diff(MULTIPLE_FILES_DIFF)
        for f in result.files:
            assert len(f.changed_lines) > 0


class TestParseMultipleHunks:
    def test_two_hunks(self):
        result = parse_unified_diff(MULTIPLE_HUNKS_DIFF)
        assert len(result.files[0].hunks) == 2

    def test_hunks_have_different_line_ranges(self):
        result = parse_unified_diff(MULTIPLE_HUNKS_DIFF)
        h1, h2 = result.files[0].hunks
        assert h1.new_start != h2.new_start


class TestParseBinaryFile:
    def test_is_binary(self):
        result = parse_unified_diff(BINARY_FILE_DIFF)
        assert result.files[0].is_binary

    def test_no_hunks(self):
        result = parse_unified_diff(BINARY_FILE_DIFF)
        assert len(result.files[0].hunks) == 0


class TestEmptyDiff:
    def test_empty_string(self):
        result = parse_unified_diff("")
        assert len(result.files) == 0

    def test_whitespace_only(self):
        result = parse_unified_diff("   \n  \n  ")
        assert len(result.files) == 0


class TestPatchSetProperties:
    def test_changed_file_paths(self):
        result = parse_unified_diff(MULTIPLE_FILES_DIFF)
        paths = result.changed_file_paths
        assert len(paths) == 2
        assert "src/auth.py" in paths
