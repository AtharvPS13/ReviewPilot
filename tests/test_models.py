"""Tests for Pydantic review models."""
from __future__ import annotations

import pytest
import json

from reviewpilot.review.models import (
    Category,
    CodeReviewIssue,
    PRReviewResponse,
    Severity,
    VerificationStatus,
    VerifiedIssue,
)


class TestCodeReviewIssue:
    def test_create_minimal(self):
        issue = CodeReviewIssue(
            file_path="main.py", start_line=1, end_line=5,
            severity=Severity.MAJOR, category=Category.BUG,
            reasoning="Found a bug", explanation="Off by one",
            can_generate_patch=False,
        )
        assert issue.file_path == "main.py"
        assert issue.replacement_code is None

    def test_create_with_replacement(self):
        issue = CodeReviewIssue(
            file_path="auth.py", start_line=10, end_line=10,
            severity=Severity.CRITICAL, category=Category.SECURITY,
            reasoning="JWT not verified", explanation="Add verification",
            can_generate_patch=True,
            replacement_code='    payload = jwt.decode(token, key=KEY, algorithms=["HS256"])',
        )
        assert issue.can_generate_patch is True
        assert "HS256" in issue.replacement_code

    def test_serialization(self):
        issue = CodeReviewIssue(
            file_path="test.py", start_line=1, end_line=1,
            severity=Severity.MINOR, category=Category.STYLE,
            reasoning="r", explanation="e", can_generate_patch=False,
        )
        data = issue.model_dump()
        assert data["severity"] == "minor"
        assert data["category"] == "style"

    def test_deserialization(self):
        data = {
            "file_path": "app.py", "start_line": 5, "end_line": 8,
            "severity": "critical", "category": "security",
            "reasoning": "bad", "explanation": "fix it",
            "can_generate_patch": False,
        }
        issue = CodeReviewIssue(**data)
        assert issue.severity == Severity.CRITICAL
        assert issue.category == Category.SECURITY


class TestPRReviewResponse:
    def test_empty_issues(self):
        response = PRReviewResponse(
            summary="Clean PR", risk_level="low", issues=[]
        )
        assert len(response.issues) == 0

    def test_with_issues(self):
        response = PRReviewResponse(
            summary="Found problems", risk_level="high",
            issues=[
                CodeReviewIssue(
                    file_path="a.py", start_line=1, end_line=1,
                    severity=Severity.CRITICAL, category=Category.SECURITY,
                    reasoning="r", explanation="e", can_generate_patch=False,
                )
            ],
        )
        assert len(response.issues) == 1
        assert response.risk_level == "high"

    def test_json_roundtrip(self):
        response = PRReviewResponse(
            summary="Test", risk_level="medium", issues=[]
        )
        json_str = response.model_dump_json()
        parsed = PRReviewResponse.model_validate_json(json_str)
        assert parsed.summary == "Test"


class TestVerifiedIssue:
    def test_default_skipped(self):
        issue = CodeReviewIssue(
            file_path="t.py", start_line=1, end_line=1,
            severity=Severity.MINOR, category=Category.BUG,
            reasoning="r", explanation="e", can_generate_patch=False,
        )
        vi = VerifiedIssue(issue=issue)
        assert vi.verification_status == VerificationStatus.SKIPPED
        assert vi.repaired_code is None

    def test_verified_with_repair(self):
        issue = CodeReviewIssue(
            file_path="t.py", start_line=1, end_line=1,
            severity=Severity.MAJOR, category=Category.BUG,
            reasoning="r", explanation="e",
            can_generate_patch=True, replacement_code="original",
        )
        vi = VerifiedIssue(
            issue=issue,
            verification_status=VerificationStatus.VERIFIED_AFTER_REPAIR,
            repaired_code="fixed",
        )
        assert vi.repaired_code == "fixed"


class TestSeverityEnum:
    def test_values(self):
        assert Severity.CRITICAL.value == "critical"
        assert Severity.MAJOR.value == "major"
        assert Severity.MINOR.value == "minor"
        assert Severity.NITPICK.value == "nitpick"


class TestCategoryEnum:
    def test_values(self):
        assert Category.SECURITY.value == "security"
        assert Category.BUG.value == "bug"
        assert Category.PERFORMANCE.value == "performance"
