"""Integration tests for the ReviewPilot pipeline.

Tests the full pipeline from diff parsing through to comment generation,
with mocked LLM and Docker components. These tests verify that all
modules work together correctly.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from reviewpilot.analysis.context_builder import build_file_context
from reviewpilot.analysis.deterministic import run_deterministic_checks
from reviewpilot.config import ReviewPilotConfig, load_config_from_dict
from reviewpilot.github.comment_formatter import (
    build_review_comments,
    format_inline_comment,
    format_summary_comment,
)
from reviewpilot.ingestion.diff_parser import parse_unified_diff
from reviewpilot.ingestion.file_filter import filter_patch_set
from reviewpilot.review.models import (
    Category,
    CodeReviewIssue,
    PRReviewResponse,
    Severity,
    VerificationStatus,
    VerifiedIssue,
)


# A realistic diff for testing the full pipeline
INTEGRATION_DIFF = """\
diff --git a/src/auth/login.py b/src/auth/login.py
index abc1234..def5678 100644
--- a/src/auth/login.py
+++ b/src/auth/login.py
@@ -1,8 +1,12 @@
+import jwt
 import hashlib
 
 
+API_SECRET = "sk-prod-1234567890abcdef1234567890"
+
 def authenticate(username, password):
     hashed = hashlib.md5(password.encode()).hexdigest()
+    token = jwt.decode(user_token, verify=False)
     return hashed == stored_hash
 
diff --git a/src/utils/helpers.py b/src/utils/helpers.py
index abc1234..def5678 100644
--- a/src/utils/helpers.py
+++ b/src/utils/helpers.py
@@ -5,3 +5,7 @@ def format_name(name):
     return name.strip().title()
 
+def calculate(x):
+    result = eval(str(x))
+    return result
+
diff --git a/package-lock.json b/package-lock.json
index abc..def 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,3 +1,4 @@
+  "new-dep": "1.0.0",
   "existing": "1.0.0"
"""

PYTHON_SOURCE_AUTH = """\
import jwt
import hashlib


API_SECRET = "sk-prod-1234567890abcdef1234567890"

def authenticate(username, password):
    hashed = hashlib.md5(password.encode()).hexdigest()
    token = jwt.decode(user_token, verify=False)
    return hashed == stored_hash
"""

PYTHON_SOURCE_UTILS = """\
import os


def format_name(name):
    return name.strip().title()

def calculate(x):
    result = eval(str(x))
    return result
"""


class TestFullPipelineDiffParsing:
    """Test that the diff parser correctly handles a multi-file PR."""

    def test_parses_all_files(self):
        patch_set = parse_unified_diff(INTEGRATION_DIFF)
        assert len(patch_set.files) == 3

    def test_filtering_removes_lockfile(self):
        patch_set = parse_unified_diff(INTEGRATION_DIFF)
        filtered = filter_patch_set(patch_set)
        paths = filtered.changed_file_paths
        assert "package-lock.json" not in paths
        assert "src/auth/login.py" in paths
        assert "src/utils/helpers.py" in paths

    def test_changed_lines_tracked(self):
        patch_set = parse_unified_diff(INTEGRATION_DIFF)
        filtered = filter_patch_set(patch_set)
        auth_file = next(f for f in filtered.files if "login" in f.path)
        assert len(auth_file.changed_lines) > 0


class TestDeterministicOnRealDiff:
    """Test that deterministic checks catch real issues in the diff."""

    def test_catches_hardcoded_secret(self):
        changed = {4: 'API_SECRET = "sk-prod-1234567890abcdef1234567890"'}
        issues = run_deterministic_checks("src/auth/login.py", changed)
        secret_issues = [i for i in issues if i.category == Category.SECURITY and "secret" in i.explanation.lower() or "key" in i.explanation.lower()]
        assert len(secret_issues) > 0

    def test_catches_eval(self):
        changed = {8: '    result = eval(str(x))'}
        issues = run_deterministic_checks("src/utils/helpers.py", changed)
        eval_issues = [i for i in issues if "eval" in i.explanation.lower()]
        assert len(eval_issues) > 0

    def test_does_not_flag_clean_lines(self):
        changed = {6: '    return name.strip().title()'}
        issues = run_deterministic_checks("src/utils/helpers.py", changed)
        # No issues on clean code
        critical_issues = [i for i in issues if i.severity in (Severity.CRITICAL, Severity.MAJOR)]
        assert len(critical_issues) == 0


class TestContextBuildingPipeline:
    """Test AST analysis and context building on real source code."""

    def test_builds_context_for_python(self):
        patch_set = parse_unified_diff(INTEGRATION_DIFF)
        filtered = filter_patch_set(patch_set)
        auth_file = next(f for f in filtered.files if "login" in f.path)

        ctx = build_file_context(auth_file, PYTHON_SOURCE_AUTH)
        assert ctx is not None
        assert ctx.language == "python"

    def test_context_prompt_contains_function(self):
        patch_set = parse_unified_diff(INTEGRATION_DIFF)
        filtered = filter_patch_set(patch_set)
        utils_file = next(f for f in filtered.files if "helpers" in f.path)

        ctx = build_file_context(utils_file, PYTHON_SOURCE_UTILS)
        assert ctx is not None
        prompt = ctx.to_prompt_text()
        assert "calculate" in prompt or "format_name" in prompt


class TestCommentFormattingPipeline:
    """Test that review issues get properly formatted for GitHub."""

    def test_format_verified_comment(self):
        issue = CodeReviewIssue(
            file_path="src/auth/login.py",
            start_line=9,
            end_line=9,
            severity=Severity.CRITICAL,
            category=Category.SECURITY,
            reasoning="JWT verify=False is dangerous",
            explanation="JWT decoded without signature verification.",
            can_generate_patch=True,
            replacement_code='    token = jwt.decode(user_token, key=SECRET, algorithms=["HS256"])',
        )
        vi = VerifiedIssue(
            issue=issue,
            verification_status=VerificationStatus.VERIFIED,
        )
        comment = format_inline_comment(vi)
        assert "Critical" in comment
        assert "Security" in comment
        assert "```suggestion" in comment
        assert "Verified" in comment

    def test_format_advisory_comment(self):
        issue = CodeReviewIssue(
            file_path="src/utils/helpers.py",
            start_line=8,
            end_line=8,
            severity=Severity.CRITICAL,
            category=Category.SECURITY,
            reasoning="eval is dangerous",
            explanation="eval() executes arbitrary code.",
            can_generate_patch=False,
        )
        vi = VerifiedIssue(
            issue=issue,
            verification_status=VerificationStatus.SKIPPED,
        )
        comment = format_inline_comment(vi)
        assert "Critical" in comment
        assert "eval" in comment

    def test_summary_with_mixed_results(self):
        issues = [
            VerifiedIssue(
                issue=CodeReviewIssue(
                    file_path="a.py", start_line=1, end_line=1,
                    severity=Severity.CRITICAL, category=Category.SECURITY,
                    reasoning="r", explanation="e",
                    can_generate_patch=True, replacement_code="fix",
                ),
                verification_status=VerificationStatus.VERIFIED,
            ),
            VerifiedIssue(
                issue=CodeReviewIssue(
                    file_path="b.py", start_line=1, end_line=1,
                    severity=Severity.MAJOR, category=Category.BUG,
                    reasoning="r", explanation="e", can_generate_patch=False,
                ),
                verification_status=VerificationStatus.FAILED,
            ),
        ]
        summary = format_summary_comment(issues, "Auth refactor PR", "high")
        assert "Critical" in summary
        assert "Major" in summary
        assert "High" in summary
        assert "sandbox-verified" in summary

    def test_build_review_comments_validates_lines(self):
        issue = CodeReviewIssue(
            file_path="src/auth/login.py",
            start_line=4, end_line=4,
            severity=Severity.CRITICAL, category=Category.SECURITY,
            reasoning="r", explanation="e", can_generate_patch=False,
        )
        vi = VerifiedIssue(issue=issue, verification_status=VerificationStatus.SKIPPED)

        valid_lines = {"src/auth/login.py": {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}}
        comments = build_review_comments([vi], valid_lines)
        assert len(comments) == 1
        assert comments[0]["path"] == "src/auth/login.py"
        assert comments[0]["line"] == 4


class TestConfigIntegration:
    """Test config loading with various scenarios."""

    def test_config_flows_to_reviewer(self):
        config = load_config_from_dict({
            "llm": {"provider": "anthropic", "model": "claude-3-haiku"},
            "sandbox": {"enabled": False},
            "review": {"max_comments": 5, "severity_threshold": "major"},
            "ignore": ["*.md"],
        })
        assert config.llm.provider == "anthropic"
        assert config.sandbox.enabled is False
        assert config.review.max_comments == 5
        assert config.review.severity_threshold == Severity.MAJOR
        assert "*.md" in config.ignore

    def test_default_config_works(self):
        config = ReviewPilotConfig()
        assert config.llm.model == "gpt-4o-mini"
        assert config.sandbox.enabled is True
        assert config.review.max_comments == 15


class TestEndToEndPipeline:
    """Test the full pipeline from diff to comments (mocked LLM)."""

    def test_deterministic_only_pipeline(self):
        """Run the pipeline with deterministic checks only (no LLM needed)."""
        # Step 1: Parse diff
        patch_set = parse_unified_diff(INTEGRATION_DIFF)
        assert len(patch_set.files) > 0

        # Step 2: Filter
        filtered = filter_patch_set(patch_set)
        assert len(filtered.files) == 2  # lockfile removed

        # Step 3: Deterministic checks
        all_issues: list[CodeReviewIssue] = []
        source_map = {
            "src/auth/login.py": PYTHON_SOURCE_AUTH,
            "src/utils/helpers.py": PYTHON_SOURCE_UTILS,
        }

        for file_diff in filtered.files:
            source = source_map.get(file_diff.path, "")
            if not source:
                continue

            source_lines = source.splitlines()
            changed_lines_map = {}
            for ln in file_diff.changed_lines:
                if 1 <= ln <= len(source_lines):
                    changed_lines_map[ln] = source_lines[ln - 1]

            issues = run_deterministic_checks(file_diff.path, changed_lines_map)
            all_issues.extend(issues)

        # Should find at least: hardcoded secret + eval
        assert len(all_issues) >= 2
        categories = {i.category for i in all_issues}
        assert Category.SECURITY in categories

        # Step 4: Format as verified issues (all skipped since no sandbox)
        verified = [
            VerifiedIssue(issue=i, verification_status=VerificationStatus.SKIPPED)
            for i in all_issues
        ]

        # Step 5: Build comments
        valid_lines = {}
        for fd in filtered.files:
            valid_lines[fd.path] = fd.all_new_file_lines

        comments = build_review_comments(verified, valid_lines)
        assert len(comments) > 0

        # Step 6: Build summary
        summary = format_summary_comment(verified, "Security fixes", "high")
        assert "Critical" in summary
        assert "ReviewPilot" in summary
