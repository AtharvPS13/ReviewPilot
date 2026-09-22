"""Tests for comment formatter."""
from __future__ import annotations

import pytest

from reviewpilot.github.comment_formatter import (
    format_inline_comment,
    format_summary_comment,
    build_review_comments,
)
from reviewpilot.review.models import (
    Category,
    CodeReviewIssue,
    Severity,
    VerificationStatus,
    VerifiedIssue,
)


def _make_issue(
    severity=Severity.MAJOR,
    category=Category.BUG,
    replacement=None,
    start=10,
    end=12,
    path="src/main.py",
):
    return CodeReviewIssue(
        file_path=path, start_line=start, end_line=end,
        severity=severity, category=category,
        reasoning="test reasoning", explanation="Fix this bug.",
        can_generate_patch=replacement is not None,
        replacement_code=replacement,
    )


def _make_verified(issue, status=VerificationStatus.VERIFIED, repaired=None):
    return VerifiedIssue(
        issue=issue, verification_status=status,
        repaired_code=repaired,
    )


class TestFormatInlineComment:
    def test_contains_severity_badge(self):
        issue = _make_issue(severity=Severity.CRITICAL, category=Category.SECURITY)
        vi = _make_verified(issue)
        result = format_inline_comment(vi)
        assert "Critical" in result

    def test_contains_category(self):
        issue = _make_issue(category=Category.SECURITY)
        vi = _make_verified(issue)
        result = format_inline_comment(vi)
        assert "Security" in result

    def test_contains_explanation(self):
        issue = _make_issue()
        vi = _make_verified(issue)
        result = format_inline_comment(vi)
        assert "Fix this bug." in result

    def test_contains_suggestion_block(self):
        issue = _make_issue(replacement="    return fixed_value")
        vi = _make_verified(issue)
        result = format_inline_comment(vi)
        assert "```suggestion" in result
        assert "return fixed_value" in result

    def test_verified_badge(self):
        issue = _make_issue(replacement="fix")
        vi = _make_verified(issue, VerificationStatus.VERIFIED)
        result = format_inline_comment(vi)
        assert "Verified" in result

    def test_advisory_badge(self):
        issue = _make_issue()
        vi = _make_verified(issue, VerificationStatus.FAILED)
        result = format_inline_comment(vi)
        assert "Advisory" in result

    def test_deduplication_marker(self):
        issue = _make_issue()
        vi = _make_verified(issue)
        result = format_inline_comment(vi)
        assert "<!-- reviewpilot:" in result

    def test_uses_repaired_code_over_original(self):
        issue = _make_issue(replacement="original_fix")
        vi = _make_verified(issue, VerificationStatus.VERIFIED_AFTER_REPAIR, repaired="better_fix")
        result = format_inline_comment(vi)
        assert "better_fix" in result


class TestFormatSummaryComment:
    def test_no_issues_clean(self):
        result = format_summary_comment([])
        assert "No issues found" in result

    def test_includes_summary(self):
        result = format_summary_comment([], pr_summary="Refactored auth module.")
        assert "Refactored auth module" in result

    def test_risk_level(self):
        result = format_summary_comment([], risk_level="high")
        assert "High" in result

    def test_severity_table(self):
        issues = [
            _make_verified(_make_issue(severity=Severity.CRITICAL)),
            _make_verified(_make_issue(severity=Severity.MAJOR)),
            _make_verified(_make_issue(severity=Severity.MAJOR)),
        ]
        result = format_summary_comment(issues)
        assert "Critical" in result
        assert "Major" in result

    def test_verification_stats(self):
        issues = [
            _make_verified(_make_issue(replacement="fix"), VerificationStatus.VERIFIED),
            _make_verified(_make_issue(), VerificationStatus.FAILED),
        ]
        result = format_summary_comment(issues)
        assert "1" in result  # 1 verified
        assert "sandbox-verified" in result


class TestBuildReviewComments:
    def test_builds_comment_dict(self):
        issue = _make_issue(path="src/app.py", start=5, end=7)
        vi = _make_verified(issue)
        valid = {"src/app.py": {5, 6, 7, 8, 9}}

        comments = build_review_comments([vi], valid)
        assert len(comments) == 1
        assert comments[0]["path"] == "src/app.py"
        assert comments[0]["line"] == 7
        assert comments[0]["side"] == "RIGHT"

    def test_skips_out_of_bounds(self):
        issue = _make_issue(path="src/app.py", start=50, end=55)
        vi = _make_verified(issue)
        valid = {"src/app.py": {1, 2, 3, 4, 5}}

        comments = build_review_comments([vi], valid)
        assert len(comments) == 0

    def test_multi_line_comment(self):
        issue = _make_issue(path="src/app.py", start=5, end=8)
        vi = _make_verified(issue)
        valid = {"src/app.py": {4, 5, 6, 7, 8, 9}}

        comments = build_review_comments([vi], valid)
        assert len(comments) == 1
        assert comments[0].get("start_line") == 5
