"""Tests for the file filter."""
from __future__ import annotations

import pytest

from reviewpilot.ingestion.diff_parser import DiffHunk, FileDiff, PatchSet, ChangedLine
from reviewpilot.ingestion.file_filter import (
    DEFAULT_IGNORE_PATTERNS,
    filter_patch_set,
    should_ignore_file,
)


class TestShouldIgnoreFile:
    """Tests for the should_ignore_file function."""

    def test_lockfiles_ignored(self):
        assert should_ignore_file("package-lock.json")
        assert should_ignore_file("yarn.lock")
        assert should_ignore_file("poetry.lock")
        assert should_ignore_file("Cargo.lock")

    def test_minified_files_ignored(self):
        assert should_ignore_file("app.min.js")
        assert should_ignore_file("styles.min.css")

    def test_binary_files_ignored(self):
        assert should_ignore_file("logo.png")
        assert should_ignore_file("photo.jpg")
        assert should_ignore_file("icon.svg")
        assert should_ignore_file("font.woff2")

    def test_generated_files_ignored(self):
        assert should_ignore_file("schema.generated.ts")
        assert should_ignore_file("models.pb.go")
        assert should_ignore_file("types.pb.py")

    def test_vendor_dirs_ignored(self):
        assert should_ignore_file("node_modules/lodash/index.js")
        assert should_ignore_file("vendor/github.com/pkg/errors/errors.go")

    def test_source_files_not_ignored(self):
        assert not should_ignore_file("src/main.py")
        assert not should_ignore_file("src/utils/helper.ts")
        assert not should_ignore_file("cmd/server/main.go")
        assert not should_ignore_file("tests/test_auth.py")

    def test_custom_patterns(self):
        assert should_ignore_file("docs/guide.md", ignore_patterns=["*.md"])
        assert not should_ignore_file("docs/guide.md")  # Without custom pattern

    def test_env_files_ignored(self):
        assert should_ignore_file(".env")
        assert should_ignore_file(".env.local")


def _make_file_diff(path: str, has_hunks: bool = True, is_binary: bool = False,
                    is_deleted: bool = False) -> FileDiff:
    """Helper to create a FileDiff for testing."""
    hunks = []
    if has_hunks:
        hunks = [DiffHunk(
            old_start=1, old_count=3, new_start=1, new_count=4, header="@@ -1,3 +1,4 @@",
            changes=[ChangedLine(line_number=2, content="new line", change_type="add")]
        )]
    return FileDiff(
        old_path=path, new_path=None if is_deleted else path,
        hunks=hunks, is_binary=is_binary, is_deleted_file=is_deleted,
    )


class TestFilterPatchSet:
    """Tests for the filter_patch_set function."""

    def test_keeps_source_files(self):
        ps = PatchSet(files=[_make_file_diff("src/main.py")])
        result = filter_patch_set(ps)
        assert len(result.files) == 1

    def test_removes_lockfiles(self):
        ps = PatchSet(files=[
            _make_file_diff("src/main.py"),
            _make_file_diff("package-lock.json"),
        ])
        result = filter_patch_set(ps)
        assert len(result.files) == 1
        assert result.files[0].path == "src/main.py"

    def test_removes_binary_files(self):
        ps = PatchSet(files=[_make_file_diff("logo.png", is_binary=True)])
        result = filter_patch_set(ps)
        assert len(result.files) == 0

    def test_removes_deleted_files(self):
        ps = PatchSet(files=[_make_file_diff("old.py", is_deleted=True)])
        result = filter_patch_set(ps)
        assert len(result.files) == 0

    def test_removes_empty_diffs(self):
        ps = PatchSet(files=[_make_file_diff("empty.py", has_hunks=False)])
        result = filter_patch_set(ps)
        assert len(result.files) == 0

    def test_custom_ignore_patterns(self):
        ps = PatchSet(files=[
            _make_file_diff("src/main.py"),
            _make_file_diff("docs/README.md"),
        ])
        result = filter_patch_set(ps, ignore_patterns=["*.md"])
        assert len(result.files) == 1
        assert result.files[0].path == "src/main.py"
