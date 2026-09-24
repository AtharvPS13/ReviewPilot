"""Tests for token optimization."""
from __future__ import annotations

import pytest

from reviewpilot.review.token_optimizer import (
    FileTokenEstimate,
    TokenBudget,
    build_optimized_prompt_chunks,
    chunk_for_context_window,
    estimate_tokens,
    prioritize_files,
    truncate_context,
)


class TestEstimateTokens:
    def test_short_text(self):
        tokens = estimate_tokens("hello world")
        assert tokens > 0
        assert tokens < 20

    def test_long_text(self):
        text = "a" * 3500  # ~1000 tokens
        tokens = estimate_tokens(text)
        assert 900 < tokens < 1200

    def test_empty_text(self):
        tokens = estimate_tokens("")
        assert tokens >= 0


class TestPrioritizeFiles:
    def test_security_files_ranked_higher(self):
        estimates = [
            FileTokenEstimate.from_context("src/utils.py", "code", priority=0),
            FileTokenEstimate.from_context("src/auth/login.py", "code", priority=0),
        ]
        result = prioritize_files(estimates)
        assert result[0].file_path == "src/auth/login.py"

    def test_test_files_ranked_lower(self):
        estimates = [
            FileTokenEstimate.from_context("tests/test_main.py", "code", priority=0),
            FileTokenEstimate.from_context("src/main.py", "code", priority=0),
        ]
        result = prioritize_files(estimates)
        assert result[0].file_path == "src/main.py"

    def test_infra_files_ranked_lowest(self):
        estimates = [
            FileTokenEstimate.from_context("docker-compose.yml", "config", priority=0),
            FileTokenEstimate.from_context("src/app.py", "code", priority=0),
        ]
        result = prioritize_files(estimates)
        assert result[0].file_path == "src/app.py"


class TestChunking:
    def test_single_chunk_fits(self):
        budget = TokenBudget(max_prompt_tokens=5000, system_tokens=500)
        estimates = [
            FileTokenEstimate.from_context("a.py", "x" * 100),
            FileTokenEstimate.from_context("b.py", "x" * 100),
        ]
        chunks = chunk_for_context_window(estimates, budget)
        assert len(chunks) == 1

    def test_splits_when_overflow(self):
        budget = TokenBudget(max_prompt_tokens=200, system_tokens=50)
        estimates = [
            FileTokenEstimate.from_context("a.py", "x" * 500),
            FileTokenEstimate.from_context("b.py", "x" * 500),
        ]
        chunks = chunk_for_context_window(estimates, budget)
        assert len(chunks) == 2

    def test_empty_input(self):
        chunks = chunk_for_context_window([])
        assert len(chunks) == 0


class TestTruncateContext:
    def test_no_truncation_needed(self):
        text = "short text"
        result = truncate_context(text, 1000)
        assert result == text

    def test_truncates_long_text(self):
        text = "line\n" * 500
        result = truncate_context(text, 100)
        assert len(result) < len(text)
        assert "truncated" in result

    def test_truncates_at_newline(self):
        text = "line one\nline two\nline three\nline four\n"
        result = truncate_context(text, 25)
        assert "truncated" in result


class TestBuildOptimizedChunks:
    def test_returns_context_strings(self):
        contexts = [
            ("a.py", "content of a"),
            ("b.py", "content of b"),
        ]
        chunks = build_optimized_prompt_chunks(contexts)
        assert len(chunks) >= 1
        assert isinstance(chunks[0], list)
        assert isinstance(chunks[0][0], str)

    def test_prioritizes_correctly(self):
        contexts = [
            ("tests/test_x.py", "test code"),
            ("src/auth/login.py", "auth code"),
        ]
        chunks = build_optimized_prompt_chunks(contexts)
        # Auth file should be first in the first chunk
        assert "auth code" in chunks[0][0]
