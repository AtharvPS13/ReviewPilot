"""Comment formatter for ReviewPilot.

Formats verified code review issues into markdown comments with
severity badges, verification status, and GitHub suggestion blocks.
Also handles deduplication via hidden HTML markers.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from reviewpilot.review.models import (
    Category,
    CodeReviewIssue,
    Severity,
    VerificationStatus,
    VerifiedIssue,
)

# Severity badge emojis
SEVERITY_BADGES: dict[Severity, str] = {
    Severity.CRITICAL: "🔴 Critical",
    Severity.MAJOR: "🟠 Major",
    Severity.MINOR: "🟡 Minor",
    Severity.NITPICK: "💭 Nitpick",
}

# Category labels
CATEGORY_LABELS: dict[Category, str] = {
    Category.SECURITY: "🔒 Security",
    Category.BUG: "🐛 Bug",
    Category.PERFORMANCE: "⚡ Performance",
    Category.ERROR_HANDLING: "🛡️ Error Handling",
    Category.STYLE: "🎨 Style",
    Category.MAINTAINABILITY: "🔧 Maintainability",
}

# Verification status badges
VERIFICATION_BADGES: dict[VerificationStatus, str] = {
    VerificationStatus.VERIFIED: "✅ **Sandbox Verified** — tests pass with this change",
    VerificationStatus.VERIFIED_AFTER_REPAIR: "✅ **Verified (Auto-Repaired)** — fixed and tested",
    VerificationStatus.FAILED: "⚠️ **Advisory** — suggestion could not be verified",
    VerificationStatus.SKIPPED: "",
    VerificationStatus.ERROR: "⚠️ **Advisory** — verification encountered an error",
}

# Bot marker prefix for deduplication
BOT_MARKER = "reviewpilot"


def _generate_comment_id(issue: CodeReviewIssue) -> str:
    """Generate a stable ID for deduplication across re-runs."""
    raw = f"{issue.file_path}:{issue.start_line}:{issue.end_line}:{issue.category.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def format_inline_comment(verified_issue: VerifiedIssue) -> str:
    """Format a verified issue as a GitHub inline review comment.

    Args:
        verified_issue: The issue with verification status

    Returns:
        Markdown-formatted comment body
    """
    issue = verified_issue.issue
    comment_id = _generate_comment_id(issue)

    # Hidden HTML marker for deduplication
    marker = f"<!-- {BOT_MARKER}:id={comment_id} -->"

    # Header with severity and category badges
    severity_badge = SEVERITY_BADGES.get(issue.severity, str(issue.severity.value))
    category_label = CATEGORY_LABELS.get(issue.category, str(issue.category.value))
    header = f"**{severity_badge}** | {category_label}"

    # Explanation
    explanation = issue.explanation

    # Build the comment body
    parts = [marker, header, "", explanation]

    # Add replacement code as GitHub suggestion block
    replacement = verified_issue.repaired_code or issue.replacement_code
    if replacement:
        parts.append("")
        parts.append("```suggestion")
        parts.append(replacement)
        parts.append("```")

    # Add verification badge
    verification_badge = VERIFICATION_BADGES.get(verified_issue.verification_status, "")
    if verification_badge:
        parts.append("")
        parts.append(f"> {verification_badge}")

    return "\n".join(parts)


def format_summary_comment(
    verified_issues: list[VerifiedIssue],
    pr_summary: str = "",
    risk_level: str = "low",
) -> str:
    """Format a top-level PR summary comment.

    Args:
        verified_issues: All verified issues
        pr_summary: LLM-generated PR summary
        risk_level: Overall risk level

    Returns:
        Markdown-formatted summary comment
    """
    # Count by severity
    severity_counts: dict[str, int] = {}
    for vi in verified_issues:
        sev = vi.issue.severity.value
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    # Count by verification status
    verified_count = sum(
        1 for vi in verified_issues
        if vi.verification_status in (VerificationStatus.VERIFIED, VerificationStatus.VERIFIED_AFTER_REPAIR)
    )
    advisory_count = sum(
        1 for vi in verified_issues
        if vi.verification_status in (VerificationStatus.FAILED, VerificationStatus.ERROR)
    )

    # Risk level emoji
    risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(risk_level, "⚪")

    parts = [
        f"<!-- {BOT_MARKER}:summary -->",
        "## 🔍 ReviewPilot Summary",
        "",
    ]

    if pr_summary:
        parts.append(pr_summary)
        parts.append("")

    parts.append(f"**Risk Level:** {risk_emoji} {risk_level.capitalize()}")
    parts.append("")

    if not verified_issues:
        parts.append("✨ **No issues found!** This PR looks clean.")
        return "\n".join(parts)

    # Findings table
    parts.append("### Findings")
    parts.append("")
    parts.append("| Severity | Count |")
    parts.append("|:---------|------:|")
    for sev in ["critical", "major", "minor", "nitpick"]:
        count = severity_counts.get(sev, 0)
        if count > 0:
            parts.append(f"| {sev.capitalize()} | {count} |")

    parts.append("")

    # Verification stats
    if verified_count > 0 or advisory_count > 0:
        parts.append("### Verification")
        parts.append(f"- ✅ **{verified_count}** suggestions sandbox-verified (tests pass)")
        if advisory_count > 0:
            parts.append(f"- ⚠️ **{advisory_count}** advisory comments (could not verify)")
        parts.append("")

    parts.append("---")
    parts.append("*Powered by [ReviewPilot](https://github.com/AtharvPS13/reviewpilot) — AI code review with sandbox verification*")

    return "\n".join(parts)


def build_review_comments(
    verified_issues: list[VerifiedIssue],
    valid_lines: dict[str, set[int]],
) -> list[dict]:
    """Build GitHub API review comment objects from verified issues.

    Validates that comment line numbers are within diff bounds
    to prevent 422 errors.

    Args:
        verified_issues: Verified issues to post
        valid_lines: Dict mapping file_path -> set of valid line numbers in diff

    Returns:
        List of comment dicts ready for GitHub API
    """
    comments: list[dict] = []

    for vi in verified_issues:
        issue = vi.issue
        file_valid_lines = valid_lines.get(issue.file_path, set())

        # Validate end_line is within diff bounds
        if issue.end_line not in file_valid_lines:
            # Try to find the closest valid line
            closest = _find_closest_valid_line(issue.end_line, file_valid_lines)
            if closest is None:
                logger.warning(
                    "Skipping comment on %s:%d — line not in diff bounds",
                    issue.file_path, issue.end_line,
                )
                continue
            comment_line = closest
        else:
            comment_line = issue.end_line

        # Format the comment body
        body = format_inline_comment(vi)

        comment: dict = {
            "path": issue.file_path,
            "line": comment_line,
            "side": "RIGHT",
            "body": body,
        }

        # Multi-line comment if start_line != end_line and both in diff
        if issue.start_line < comment_line and issue.start_line in file_valid_lines:
            comment["start_line"] = issue.start_line
            comment["start_side"] = "RIGHT"

        comments.append(comment)

    return comments


def _find_closest_valid_line(target: int, valid_lines: set[int]) -> Optional[int]:
    """Find the closest valid line number to the target."""
    if not valid_lines:
        return None

    # Look within ±5 lines
    for offset in range(6):
        if (target + offset) in valid_lines:
            return target + offset
        if (target - offset) in valid_lines:
            return target - offset

    return None


# Import at bottom to avoid circular imports
import logging
logger = logging.getLogger(__name__)
