"""Tests for model cascading."""
from __future__ import annotations

import pytest

from reviewpilot.review.model_cascade import (
    DEFAULT_TIERS,
    FileComplexity,
    assess_complexity,
    get_tier_for_complexity,
    group_files_by_tier,
)


class TestAssessComplexity:
    def test_small_change_gets_fast(self):
        result = assess_complexity("src/utils.py", changed_line_count=10)
        assert result.estimated_tier == "fast"

    def test_medium_change_gets_standard(self):
        result = assess_complexity("src/service.py", changed_line_count=80)
        assert result.estimated_tier == "standard"

    def test_large_change_gets_deep(self):
        result = assess_complexity("src/engine.py", changed_line_count=300)
        assert result.estimated_tier == "deep"

    def test_security_file_gets_standard_minimum(self):
        result = assess_complexity("src/auth/login.py", changed_line_count=10)
        assert result.estimated_tier == "standard"

    def test_security_file_large_gets_deep(self):
        result = assess_complexity("src/auth/jwt_handler.py", changed_line_count=250)
        assert result.estimated_tier == "deep"

    def test_test_file_always_fast(self):
        result = assess_complexity("tests/test_auth.py", changed_line_count=500)
        assert result.estimated_tier == "fast"

    def test_many_functions_gets_deep(self):
        result = assess_complexity("src/big_module.py", changed_line_count=100, function_count=8)
        assert result.estimated_tier == "deep"

    def test_flags_security_sensitive(self):
        result = assess_complexity("src/crypto/encrypt.py", changed_line_count=5)
        assert result.has_security_sensitive is True

    def test_flags_test_file(self):
        result = assess_complexity("tests/test_utils.py", changed_line_count=5)
        assert result.has_test_file is True


class TestGetTier:
    def test_returns_matching_tier(self):
        fc = FileComplexity(
            file_path="test.py", changed_line_count=10,
            function_count=1, has_security_sensitive=False,
            has_test_file=False, estimated_tier="fast",
        )
        tier = get_tier_for_complexity(fc)
        assert tier.name == "fast"

    def test_returns_correct_model(self):
        fc = FileComplexity(
            file_path="test.py", changed_line_count=100,
            function_count=3, has_security_sensitive=False,
            has_test_file=False, estimated_tier="standard",
        )
        tier = get_tier_for_complexity(fc)
        assert tier.model == "gpt-4o"

    def test_fallback_to_first_tier(self):
        fc = FileComplexity(
            file_path="test.py", changed_line_count=10,
            function_count=1, has_security_sensitive=False,
            has_test_file=False, estimated_tier="nonexistent",
        )
        tier = get_tier_for_complexity(fc)
        assert tier.name == "fast"


class TestGroupFiles:
    def test_groups_by_tier(self):
        complexities = [
            assess_complexity("small.py", 10),
            assess_complexity("medium.py", 80),
            assess_complexity("auth/login.py", 20),
            assess_complexity("tests/test_x.py", 200),
        ]
        groups = group_files_by_tier(complexities)
        assert "fast" in groups
        assert "standard" in groups

    def test_fast_tier_has_test_and_small(self):
        complexities = [
            assess_complexity("small.py", 10),
            assess_complexity("tests/test_x.py", 200),
        ]
        groups = group_files_by_tier(complexities)
        fast_paths = [f.file_path for f in groups.get("fast", [])]
        assert "small.py" in fast_paths
        assert "tests/test_x.py" in fast_paths
