"""Sandbox verification orchestrator for ReviewPilot.

Ties together patch application, Docker execution, and LLM self-repair
into a complete verification pipeline. For each suggestion with replacement
code: apply patch → run tests → if fail, repair → retry → if still fail,
downgrade to advisory.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from reviewpilot.config import ReviewPilotConfig
from reviewpilot.review.llm_reviewer import LLMReviewer
from reviewpilot.review.models import (
    CodeReviewIssue,
    VerificationStatus,
    VerifiedIssue,
)
from reviewpilot.sandbox.docker_runner import DockerError, DockerRunner, SandboxResult
from reviewpilot.sandbox.patch_applier import PatchError, create_patched_copy, get_original_lines

logger = logging.getLogger(__name__)


class Verifier:
    """Orchestrates sandbox verification of code review suggestions.

    Pipeline per suggestion:
    1. Apply replacement code to a temporary repo copy
    2. Run tests in Docker sandbox
    3. If tests pass → VERIFIED
    4. If tests fail → feed error to LLM for self-repair
    5. Re-verify repaired code
    6. If repair passes → VERIFIED_AFTER_REPAIR
    7. If repair fails → FAILED (downgraded to advisory comment)
    """

    def __init__(
        self,
        config: ReviewPilotConfig,
        repo_path: Path,
        llm_reviewer: Optional[LLMReviewer] = None,
    ) -> None:
        """Initialize the verifier.

        Args:
            config: Full ReviewPilot configuration
            repo_path: Path to the repository being reviewed
            llm_reviewer: LLM reviewer for self-repair attempts (optional)
        """
        self.config = config
        self.repo_path = repo_path
        self.llm_reviewer = llm_reviewer

        # Initialize Docker runner (may fail if Docker not available)
        self._docker: Optional[DockerRunner] = None
        if config.sandbox.enabled:
            try:
                self._docker = DockerRunner(config.sandbox)
            except DockerError as e:
                logger.warning("Docker not available, sandbox verification disabled: %s", e)

    async def verify_issue(self, issue: CodeReviewIssue) -> VerifiedIssue:
        """Verify a single code review issue.

        If the issue has replacement_code and sandbox is enabled,
        applies the patch and runs tests. Otherwise, skips verification.

        Args:
            issue: The code review issue to verify

        Returns:
            VerifiedIssue with verification status and details
        """
        # Skip if no replacement code
        if not issue.can_generate_patch or not issue.replacement_code:
            return VerifiedIssue(
                issue=issue,
                verification_status=VerificationStatus.SKIPPED,
            )

        # Skip if sandbox is disabled or Docker unavailable
        if not self._docker or not self.config.sandbox.enabled:
            return VerifiedIssue(
                issue=issue,
                verification_status=VerificationStatus.SKIPPED,
                verification_output="Sandbox verification disabled.",
            )

        # Attempt 1: Apply and test
        result = await self._apply_and_test(issue, issue.replacement_code)

        if result.verification_status == VerificationStatus.VERIFIED:
            return result

        # Attempt 2: Self-repair (if configured and LLM available)
        if (
            self.config.sandbox.max_repair_attempts > 0
            and self.llm_reviewer
            and result.verification_output
        ):
            logger.info("Suggestion failed tests, attempting self-repair for %s", issue.file_path)

            original_code = get_original_lines(
                self.repo_path, issue.file_path, issue.start_line, issue.end_line
            )

            repaired_code = await self.llm_reviewer.repair_suggestion(
                original_code=original_code,
                suggested_code=issue.replacement_code,
                error_output=result.verification_output or "",
                file_path=issue.file_path,
            )

            if repaired_code:
                repair_result = await self._apply_and_test(issue, repaired_code)
                if repair_result.verification_status == VerificationStatus.VERIFIED:
                    return VerifiedIssue(
                        issue=issue,
                        verification_status=VerificationStatus.VERIFIED_AFTER_REPAIR,
                        verification_output=repair_result.verification_output,
                        repaired_code=repaired_code,
                    )

        # All attempts failed — downgrade to advisory
        return VerifiedIssue(
            issue=issue,
            verification_status=VerificationStatus.FAILED,
            verification_output=result.verification_output,
        )

    async def verify_all(self, issues: list[CodeReviewIssue]) -> list[VerifiedIssue]:
        """Verify all issues that have replacement code.

        Args:
            issues: List of code review issues

        Returns:
            List of VerifiedIssue objects with verification results
        """
        results: list[VerifiedIssue] = []
        for issue in issues:
            verified = await self.verify_issue(issue)
            results.append(verified)
            logger.info(
                "Verified %s:%d-%d → %s",
                issue.file_path,
                issue.start_line,
                issue.end_line,
                verified.verification_status.value,
            )
        return results

    async def _apply_and_test(
        self,
        issue: CodeReviewIssue,
        replacement_code: str,
    ) -> VerifiedIssue:
        """Apply a replacement and run tests.

        Args:
            issue: The original issue
            replacement_code: Code to test (may be original or repaired)

        Returns:
            VerifiedIssue with the result
        """
        temp_dir: Optional[Path] = None
        try:
            # Create a patched copy with this replacement code
            patched_issue = CodeReviewIssue(
                file_path=issue.file_path,
                start_line=issue.start_line,
                end_line=issue.end_line,
                severity=issue.severity,
                category=issue.category,
                reasoning=issue.reasoning,
                explanation=issue.explanation,
                can_generate_patch=True,
                replacement_code=replacement_code,
            )

            patched_repo = create_patched_copy(self.repo_path, patched_issue)
            temp_dir = patched_repo.parent

            # Run tests in sandbox
            assert self._docker is not None
            sandbox_result: SandboxResult = self._docker.run_tests(patched_repo)

            if sandbox_result.success:
                return VerifiedIssue(
                    issue=issue,
                    verification_status=VerificationStatus.VERIFIED,
                    verification_output="Tests passed.",
                )
            else:
                return VerifiedIssue(
                    issue=issue,
                    verification_status=VerificationStatus.FAILED,
                    verification_output=sandbox_result.output[:3000],  # Truncate long output
                )

        except PatchError as e:
            return VerifiedIssue(
                issue=issue,
                verification_status=VerificationStatus.ERROR,
                verification_output=f"Patch application failed: {e}",
            )
        except Exception as e:
            return VerifiedIssue(
                issue=issue,
                verification_status=VerificationStatus.ERROR,
                verification_output=f"Verification error: {e}",
            )
        finally:
            # Always clean up temp directory
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
