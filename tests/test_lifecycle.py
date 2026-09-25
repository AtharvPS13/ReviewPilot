"""Tests for the comment lifecycle manager."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from reviewpilot.github.lifecycle import (
    MARKER_PATTERN,
    SUMMARY_MARKER,
    filter_new_comments,
    find_resolved_comments,
)


class TestMarkerPattern:
    def test_matches_valid_marker(self):
        body = "some text\n<!-- reviewpilot:id=abc123def456 -->\nmore text"
        match = MARKER_PATTERN.search(body)
        assert match is not None
        assert match.group(1) == "abc123def456"

    def test_no_match_without_marker(self):
        body = "just a regular comment with no markers"
        match = MARKER_PATTERN.search(body)
        assert match is None

    def test_summary_marker_is_a_string(self):
        assert "reviewpilot:summary" in SUMMARY_MARKER


class TestFilterNewComments:
    def test_all_new(self):
        new_ids = ["aaa", "bbb", "ccc"]
        existing = {}
        result = filter_new_comments(new_ids, existing)
        assert result == ["aaa", "bbb", "ccc"]

    def test_all_duplicates(self):
        new_ids = ["aaa", "bbb"]
        existing = {"aaa": MagicMock(), "bbb": MagicMock()}
        result = filter_new_comments(new_ids, existing)
        assert result == []

    def test_mixed(self):
        new_ids = ["aaa", "bbb", "ccc"]
        existing = {"bbb": MagicMock()}
        result = filter_new_comments(new_ids, existing)
        assert result == ["aaa", "ccc"]

    def test_empty_new(self):
        result = filter_new_comments([], {"aaa": MagicMock()})
        assert result == []


class TestFindResolvedComments:
    def test_all_resolved(self):
        current_ids = set()
        existing = {"aaa": MagicMock(), "bbb": MagicMock()}
        resolved = find_resolved_comments(current_ids, existing)
        assert len(resolved) == 2

    def test_none_resolved(self):
        current_ids = {"aaa", "bbb"}
        existing = {"aaa": MagicMock(), "bbb": MagicMock()}
        resolved = find_resolved_comments(current_ids, existing)
        assert len(resolved) == 0

    def test_partial_resolved(self):
        current_ids = {"bbb"}
        existing = {"aaa": MagicMock(), "bbb": MagicMock(), "ccc": MagicMock()}
        resolved = find_resolved_comments(current_ids, existing)
        assert len(resolved) == 2
        resolved_ids = [c for c in existing if c not in current_ids]
        assert "aaa" in resolved_ids
        assert "ccc" in resolved_ids

    def test_new_ids_not_in_existing(self):
        current_ids = {"aaa", "ddd"}  # ddd is new, not in existing
        existing = {"aaa": MagicMock(), "bbb": MagicMock()}
        resolved = find_resolved_comments(current_ids, existing)
        assert len(resolved) == 1  # Only bbb is resolved
