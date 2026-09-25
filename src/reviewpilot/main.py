"""Main orchestrator for ReviewPilot.

Ties together all pipeline stages:
1. Fetch PR diff from GitHub
2. Parse and filter the diff
3. AST analysis + context building
4. Deterministic pre-filter checks
5. LLM semantic review
6. Sandbox verification of suggestions
7. Post verified comments to GitHub
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from reviewpilot.analysis.ast_analyzer import analyze_file
from reviewpilot.analysis.context_builder import build_file_context
from reviewpilot.analysis.deterministic import run_deterministic_checks
from reviewpilot.config import ReviewPilotConfig, load_config
from reviewpilot.github.api_client import GitHubClient
from reviewpilot.github.comment_formatter import (
    build_review_comments,
    format_summary_comment,
)
from reviewpilot.github.lifecycle import run_lifecycle
from reviewpilot.ingestion.diff_parser import parse_unified_diff
from reviewpilot.ingestion.file_filter import filter_patch_set
from reviewpilot.review.llm_reviewer import LLMReviewer
from reviewpilot.review.models import (
    CodeReviewIssue,
    VerificationStatus,
    VerifiedIssue,
)
from reviewpilot.sandbox.verifier import Verifier

logger = logging.getLogger(__name__)


async def run_review(
    repo_full_name: str,
    pr_number: int,
    repo_path: Optional[str] = None,
    config_override: Optional[ReviewPilotConfig] = None,
) -> dict:
    """Run the complete ReviewPilot pipeline on a Pull Request.

    Args:
        repo_full_name: Repository in 'owner/repo' format
        pr_number: PR number to review
        repo_path: Local path to the repository (for sandbox verification)
        config_override: Optional config override (for testing)

    Returns:
        Dict with review results:
        - total_issues: number of issues found
        - verified_count: number of sandbox-verified suggestions
        - advisory_count: number of advisory comments
        - posted: whether comments were posted
    """
    # ── Stage 0: Setup ──────────────────────────────────────────────
    logger.info("Starting ReviewPilot review for %s#%d", repo_full_name, pr_number)

    github_client = GitHubClient()
    repo_root = Path(repo_path) if repo_path else Path(".")
    config = config_override or load_config(repo_root)

    # ── Stage 1: Fetch & Parse Diff ─────────────────────────────────
    logger.info("Stage 1: Fetching PR diff...")
    raw_diff = github_client.get_pr_diff(repo_full_name, pr_number)
    patch_set = parse_unified_diff(raw_diff)
    filtered = filter_patch_set(patch_set, config.ignore)

    if not filtered.files:
        logger.info("No reviewable files in this PR. Exiting.")
        return {"total_issues": 0, "verified_count": 0, "advisory_count": 0, "posted": False}

    logger.info("Found %d reviewable files (filtered from %d total)",
                len(filtered.files), len(patch_set.files))

    # ── Stage 2: Fetch File Contents ────────────────────────────────
    logger.info("Stage 2: Fetching file contents at PR HEAD...")
    file_paths = filtered.changed_file_paths
    file_contents = github_client.get_pr_files_content(
        repo_full_name, pr_number, file_paths
    )

    # ── Stage 3: AST Analysis + Context Building ────────────────────
    logger.info("Stage 3: AST analysis and context building...")
    file_contexts: list[str] = []
    for file_diff in filtered.files:
        source = file_contents.get(file_diff.path, "")
        if not source:
            continue

        context = build_file_context(file_diff, source)
        if context:
            file_contexts.append(context.to_prompt_text())

    # ── Stage 4: Deterministic Pre-Filter ───────────────────────────
    logger.info("Stage 4: Running deterministic checks...")
    all_issues: list[CodeReviewIssue] = []

    for file_diff in filtered.files:
        source = file_contents.get(file_diff.path, "")
        if not source:
            continue

        # Build changed lines map: line_number -> content
        source_lines = source.splitlines()
        changed_lines_map: dict[int, str] = {}
        for line_num in file_diff.changed_lines:
            if 1 <= line_num <= len(source_lines):
                changed_lines_map[line_num] = source_lines[line_num - 1]

        det_issues = run_deterministic_checks(
            file_diff.path,
            changed_lines_map,
            min_severity=config.review.severity_threshold,
        )
        all_issues.extend(det_issues)

    logger.info("Deterministic checks found %d issues", len(all_issues))

    # ── Stage 5: LLM Review ─────────────────────────────────────────
    llm_reviewer: Optional[LLMReviewer] = None

    if not config.review.deterministic_only and file_contexts:
        logger.info("Stage 5: Running LLM review...")
        llm_reviewer = LLMReviewer(config.llm)

        pr = github_client.get_pr(repo_full_name, pr_number)
        llm_response = await llm_reviewer.review(
            file_contexts=file_contexts,
            pr_title=pr.title or "",
            pr_body=pr.body or "",
        )

        all_issues.extend(llm_response.issues)
        pr_summary = llm_response.summary
        risk_level = llm_response.risk_level
        logger.info("LLM review found %d issues", len(llm_response.issues))
    else:
        pr_summary = ""
        risk_level = "low"
        logger.info("Stage 5: Skipped (deterministic_only=%s, contexts=%d)",
                     config.review.deterministic_only, len(file_contexts))

    # ── Stage 6: Sandbox Verification ───────────────────────────────
    logger.info("Stage 6: Sandbox verification...")
    verifier = Verifier(config, repo_root, llm_reviewer)
    verified_issues = await verifier.verify_all(all_issues)

    verified_count = sum(
        1 for vi in verified_issues
        if vi.verification_status in (VerificationStatus.VERIFIED, VerificationStatus.VERIFIED_AFTER_REPAIR)
    )
    advisory_count = sum(
        1 for vi in verified_issues
        if vi.verification_status in (VerificationStatus.FAILED, VerificationStatus.ERROR)
    )

    logger.info(
        "Verification complete: %d verified, %d advisory, %d skipped",
        verified_count, advisory_count,
        len(verified_issues) - verified_count - advisory_count,
    )

    # ── Stage 7: Post Comments ──────────────────────────────────────
    logger.info("Stage 7: Posting review comments...")

    # Enforce max comments limit
    verified_issues = verified_issues[: config.review.max_comments]

    # Build valid lines map for 422 prevention
    valid_lines: dict[str, set[int]] = {}
    for file_diff in filtered.files:
        valid_lines[file_diff.path] = file_diff.all_new_file_lines

    # Build and post comments
    comments = build_review_comments(verified_issues, valid_lines)

    if comments:
        github_client.post_review(repo_full_name, pr_number, comments)

    # Post summary comment
    if config.review.post_summary:
        summary = format_summary_comment(verified_issues, pr_summary, risk_level)
        github_client.post_summary_comment(repo_full_name, pr_number, summary)

    github_client.close()

    result = {
        "total_issues": len(all_issues),
        "verified_count": verified_count,
        "advisory_count": advisory_count,
        "posted": len(comments) > 0,
    }
    logger.info("ReviewPilot complete: %s", result)
    return result


def main() -> None:
    """Entry point for GitHub Action execution.

    Reads inputs from environment variables set by the GitHub Action:
    - GITHUB_REPOSITORY: 'owner/repo'
    - PR_NUMBER: PR number
    - GITHUB_WORKSPACE: path to checked-out repo
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr_number_str = os.environ.get("PR_NUMBER", "")
    workspace = os.environ.get("GITHUB_WORKSPACE", ".")

    if not repo or not pr_number_str:
        logger.error("Missing required env vars: GITHUB_REPOSITORY, PR_NUMBER")
        sys.exit(1)

    try:
        pr_number = int(pr_number_str)
    except ValueError:
        logger.error("PR_NUMBER must be an integer, got: %s", pr_number_str)
        sys.exit(1)

    result = asyncio.run(run_review(repo, pr_number, workspace))

    if result["total_issues"] == 0:
        logger.info("No issues found — PR looks clean!")
    else:
        logger.info(
            "Review complete: %d issues, %d verified, %d advisory",
            result["total_issues"], result["verified_count"], result["advisory_count"],
        )


# CLI entry point
def cli() -> None:
    """CLI entry point (registered in pyproject.toml)."""
    main()


if __name__ == "__main__":
    main()
