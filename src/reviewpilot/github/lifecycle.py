"""Comment lifecycle management for ReviewPilot.

Handles deduplication and cleanup of ReviewPilot comments across
multiple PR pushes. When a PR is updated and ReviewPilot re-runs,
this module ensures:
1. Duplicate comments are not posted (via hidden HTML markers)
2. Resolved issues (fixed by the developer) are marked as such
3. Old summary comments are updated instead of creating new ones
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from github.PullRequest import PullRequest
from github.PullRequestComment import PullRequestComment
from github.IssueComment import IssueComment

logger = logging.getLogger(__name__)

# Pattern to match our hidden markers: <!-- reviewpilot:id=abc123def456 -->
MARKER_PATTERN = re.compile(r"<!-- reviewpilot:id=(\w+) -->")
SUMMARY_MARKER = "<!-- reviewpilot:summary -->"


def find_existing_comments(pr: PullRequest) -> dict[str, PullRequestComment]:
    """Find all existing ReviewPilot inline comments on a PR.

    Scans review comments for hidden marker tags and returns
    a mapping of comment_id to the GitHub comment object.

    Args:
        pr: PyGithub PullRequest object

    Returns:
        Dict mapping ReviewPilot comment IDs to GitHub comment objects
    """
    existing: dict[str, PullRequestComment] = {}

    try:
        for comment in pr.get_review_comments():
            body = comment.body or ""
            match = MARKER_PATTERN.search(body)
            if match:
                comment_id = match.group(1)
                existing[comment_id] = comment
    except Exception as e:
        logger.warning("Could not fetch existing comments: %s", e)

    return existing


def find_summary_comment(pr: PullRequest) -> Optional[IssueComment]:
    """Find the existing ReviewPilot summary comment on a PR.

    Args:
        pr: PyGithub PullRequest object

    Returns:
        The existing summary comment, or None
    """
    try:
        for comment in pr.get_issue_comments():
            if SUMMARY_MARKER in (comment.body or ""):
                return comment
    except Exception as e:
        logger.warning("Could not fetch issue comments: %s", e)

    return None


def filter_new_comments(
    comment_ids: list[str],
    existing: dict[str, PullRequestComment],
) -> list[str]:
    """Filter out comment IDs that already exist on the PR.

    Args:
        comment_ids: List of ReviewPilot comment IDs we want to post
        existing: Existing comments from find_existing_comments()

    Returns:
        List of comment IDs that are NEW (not yet posted)
    """
    return [cid for cid in comment_ids if cid not in existing]


def find_resolved_comments(
    current_ids: set[str],
    existing: dict[str, PullRequestComment],
) -> list[PullRequestComment]:
    """Find comments from previous runs that are no longer relevant.

    These are comments where the developer has already fixed
    the issue (the issue no longer appears in the new review).

    Args:
        current_ids: Set of comment IDs from the current review
        existing: Existing comments on the PR

    Returns:
        List of GitHub comments that are now resolved
    """
    resolved = []
    for comment_id, comment in existing.items():
        if comment_id not in current_ids:
            resolved.append(comment)
    return resolved


def mark_resolved(comments: list[PullRequestComment]) -> int:
    """Add a 'resolved' note to comments for issues that have been fixed.

    Rather than deleting comments (which loses context), we prepend
    a resolved badge to signal the developer fixed the issue.

    Args:
        comments: List of resolved comments to update

    Returns:
        Number of comments successfully updated
    """
    updated = 0
    resolved_badge = "> :white_check_mark: **Resolved** -- This issue appears to have been fixed.\n\n"

    for comment in comments:
        body = comment.body or ""
        # Don't re-mark already resolved comments
        if "Resolved" in body and "white_check_mark" in body:
            continue

        try:
            comment.edit(resolved_badge + body)
            updated += 1
        except Exception as e:
            logger.warning("Could not mark comment as resolved: %s", e)

    return updated


def update_summary(pr: PullRequest, new_summary: str) -> None:
    """Update the existing summary comment, or create a new one.

    Args:
        pr: PyGithub PullRequest object
        new_summary: New summary comment body (must contain SUMMARY_MARKER)
    """
    existing = find_summary_comment(pr)

    if existing:
        try:
            existing.edit(new_summary)
            logger.info("Updated existing summary comment")
        except Exception as e:
            logger.warning("Could not update summary, creating new: %s", e)
            pr.create_issue_comment(new_summary)
    else:
        pr.create_issue_comment(new_summary)
        logger.info("Created new summary comment")


def run_lifecycle(
    pr: PullRequest,
    new_comment_ids: list[str],
    new_comments: list[dict],
    summary_body: str,
) -> dict:
    """Run the full comment lifecycle for a PR review.

    1. Find existing ReviewPilot comments
    2. Filter out duplicates
    3. Mark resolved issues
    4. Update or create summary

    Args:
        pr: PyGithub PullRequest object
        new_comment_ids: IDs from the current review
        new_comments: Comment dicts ready for GitHub API (parallel with IDs)
        summary_body: Summary comment markdown

    Returns:
        Dict with lifecycle stats
    """
    # Find existing comments
    existing = find_existing_comments(pr)
    logger.info("Found %d existing ReviewPilot comments", len(existing))

    # Filter duplicates
    new_ids = filter_new_comments(new_comment_ids, existing)
    deduplicated_comments = [
        comment for cid, comment in zip(new_comment_ids, new_comments)
        if cid in new_ids
    ]

    # Mark resolved
    current_set = set(new_comment_ids)
    resolved = find_resolved_comments(current_set, existing)
    resolved_count = mark_resolved(resolved)

    # Update summary
    update_summary(pr, summary_body)

    stats = {
        "existing_comments": len(existing),
        "new_comments": len(deduplicated_comments),
        "duplicates_skipped": len(new_comment_ids) - len(new_ids),
        "resolved_marked": resolved_count,
    }
    logger.info("Lifecycle stats: %s", stats)

    return {**stats, "comments_to_post": deduplicated_comments}
