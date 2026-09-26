"""Tests for prompt templates."""
from __future__ import annotations

import pytest

from reviewpilot.review.prompt_templates import (
    SYSTEM_PROMPT,
    FEW_SHOT_EXAMPLE,
    build_review_prompt,
    build_repair_prompt,
)


class TestSystemPrompt:
    def test_contains_instructions(self):
        assert "code review" in SYSTEM_PROMPT.lower()
        assert "CHANGED lines" in SYSTEM_PROMPT

    def test_mentions_security(self):
        assert "Security" in SYSTEM_PROMPT or "security" in SYSTEM_PROMPT

    def test_mentions_line_numbers(self):
        assert "line numbers" in SYSTEM_PROMPT.lower()

    def test_discourages_style_nitpicks(self):
        assert "style" in SYSTEM_PROMPT.lower()


class TestFewShotExample:
    def test_contains_json(self):
        assert "file_path" in FEW_SHOT_EXAMPLE
        assert "severity" in FEW_SHOT_EXAMPLE
        assert "reasoning" in FEW_SHOT_EXAMPLE

    def test_shows_security_example(self):
        assert "jwt" in FEW_SHOT_EXAMPLE.lower() or "security" in FEW_SHOT_EXAMPLE.lower()


class TestBuildReviewPrompt:
    def test_includes_file_contexts(self):
        contexts = ["## File: src/app.py\ndef main(): pass"]
        prompt = build_review_prompt(contexts)
        assert "src/app.py" in prompt
        assert "def main" in prompt

    def test_includes_pr_title(self):
        prompt = build_review_prompt(["context"], pr_title="Fix auth bug")
        assert "Fix auth bug" in prompt

    def test_includes_pr_body(self):
        prompt = build_review_prompt(["context"], pr_body="This PR fixes the login flow")
        assert "login flow" in prompt

    def test_truncates_long_body(self):
        long_body = "x" * 1000
        prompt = build_review_prompt(["context"], pr_body=long_body)
        assert "..." in prompt
        assert len(prompt) < len(long_body) + 500

    def test_multiple_contexts_joined(self):
        contexts = ["## File A\ncode a", "## File B\ncode b"]
        prompt = build_review_prompt(contexts)
        assert "File A" in prompt
        assert "File B" in prompt

    def test_includes_instructions(self):
        prompt = build_review_prompt(["context"])
        assert "issues" in prompt.lower()
        assert "summary" in prompt.lower()


class TestBuildRepairPrompt:
    def test_includes_all_sections(self):
        prompt = build_repair_prompt(
            original_code="x = 1",
            suggested_code="x = 2",
            error_output="NameError: undefined",
            file_path="app.py",
        )
        assert "app.py" in prompt
        assert "x = 1" in prompt
        assert "x = 2" in prompt
        assert "NameError" in prompt

    def test_truncates_long_errors(self):
        long_error = "error line\n" * 500
        prompt = build_repair_prompt("code", "fix", long_error, "f.py")
        assert len(prompt) < len(long_error)

    def test_asks_for_code_only(self):
        prompt = build_repair_prompt("a", "b", "err", "f.py")
        assert "code" in prompt.lower()
