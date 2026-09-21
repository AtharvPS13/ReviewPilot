"""Pydantic data models for ReviewPilot.

Defines the structured schemas for LLM input/output, ensuring every
review suggestion has precise file paths, line numbers, severity,
and optional replacement code for sandbox verification.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """How serious the issue is."""
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    NITPICK = "nitpick"


class Category(str, Enum):
    """What type of issue this is."""
    SECURITY = "security"
    BUG = "bug"
    PERFORMANCE = "performance"
    ERROR_HANDLING = "error-handling"
    STYLE = "style"
    MAINTAINABILITY = "maintainability"


class CodeReviewIssue(BaseModel):
    """A single code review finding from the LLM.

    The schema is designed with 'reasoning' before 'replacement_code'
    so the LLM generates chain-of-thought analysis before committing
    to a code fix — reducing hallucinated suggestions.
    """

    file_path: str = Field(
        description="Relative path to the file (e.g., 'src/auth/login.py')"
    )
    start_line: int = Field(
        description="1-indexed line number where the issue starts"
    )
    end_line: int = Field(
        description="1-indexed line number where the issue ends"
    )
    severity: Severity = Field(
        description="How serious is this issue"
    )
    category: Category = Field(
        description="What type of issue this is"
    )
    reasoning: str = Field(
        description=(
            "Step-by-step analysis of WHY this is a problem. "
            "Think through this carefully before suggesting a fix."
        )
    )
    explanation: str = Field(
        description="Clear, concise explanation for the PR author"
    )
    can_generate_patch: bool = Field(
        description="True if you can provide a concrete code replacement"
    )
    replacement_code: Optional[str] = Field(
        default=None,
        description=(
            "Exact replacement code for lines start_line to end_line. "
            "Only provide if can_generate_patch is True. "
            "No markdown backticks."
        ),
    )


class PRReviewResponse(BaseModel):
    """Complete LLM review response for a PR.

    Returned by the LLM as structured JSON, validated by Pydantic.
    """

    summary: str = Field(
        description="2-3 sentence executive summary of the PR changes"
    )
    risk_level: str = Field(
        description="Overall risk assessment: 'low', 'medium', or 'high'"
    )
    issues: list[CodeReviewIssue] = Field(
        default_factory=list,
        description="List of issues found. Empty list if code looks good.",
    )


class VerificationStatus(str, Enum):
    """Result of sandbox verification for a suggestion."""
    VERIFIED = "verified"              # Tests passed with the suggestion applied
    VERIFIED_AFTER_REPAIR = "repaired" # Failed initially, LLM fixed it, then passed
    FAILED = "failed"                  # Tests failed even after repair attempt
    SKIPPED = "skipped"                # No replacement code / sandbox disabled
    ERROR = "error"                    # Docker/system error during verification


class VerifiedIssue(BaseModel):
    """A code review issue with its verification result attached."""

    issue: CodeReviewIssue
    verification_status: VerificationStatus = VerificationStatus.SKIPPED
    verification_output: Optional[str] = None  # Test output or error message
    repaired_code: Optional[str] = None        # If self-repair succeeded
