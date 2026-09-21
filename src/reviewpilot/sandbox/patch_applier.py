"""Patch applier for ReviewPilot sandbox verification.

Applies line-range code replacements to files. Used to test LLM suggestions
by applying them to a copy of the codebase before running tests.
Uses simple line replacement (not unified diff format) because LLMs
are more reliable at generating replacement code than diff patches.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from reviewpilot.review.models import CodeReviewIssue

logger = logging.getLogger(__name__)


class PatchError(Exception):
    """Raised when a patch cannot be applied."""
    pass


def apply_line_replacement(
    file_path: Path,
    start_line: int,
    end_line: int,
    replacement_code: str,
) -> str:
    """Apply a line-range replacement to a file.

    Replaces lines start_line through end_line (1-indexed, inclusive)
    with the replacement code. Returns the new file content.

    Args:
        file_path: Path to the file to modify
        start_line: First line to replace (1-indexed)
        end_line: Last line to replace (1-indexed, inclusive)
        replacement_code: Code to insert in place of the replaced lines

    Returns:
        The new file content with the replacement applied

    Raises:
        PatchError: If the replacement cannot be applied
    """
    try:
        original_content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        raise PatchError(f"Cannot read file {file_path}: {e}") from e

    lines = original_content.splitlines(keepends=True)

    # Validate line range
    if start_line < 1 or end_line < start_line:
        raise PatchError(
            f"Invalid line range: {start_line}-{end_line}. "
            f"Must satisfy 1 <= start_line <= end_line."
        )
    if start_line > len(lines):
        raise PatchError(
            f"start_line {start_line} exceeds file length ({len(lines)} lines)."
        )

    # Clamp end_line to file length
    end_line = min(end_line, len(lines))

    # Build replacement lines (ensure they end with newlines)
    replacement_lines = replacement_code.splitlines(keepends=True)
    if replacement_lines and not replacement_lines[-1].endswith("\n"):
        replacement_lines[-1] += "\n"

    # Splice: keep lines before start, insert replacement, keep lines after end
    new_lines = lines[: start_line - 1] + replacement_lines + lines[end_line:]
    return "".join(new_lines)


def create_patched_copy(
    repo_path: Path,
    issue: CodeReviewIssue,
    work_dir: Optional[Path] = None,
) -> Path:
    """Create a copy of the repo with the suggestion applied.

    Makes a temporary copy of the repository and applies the code
    replacement to the target file. The caller is responsible for
    cleaning up the temporary directory.

    Args:
        repo_path: Path to the original repository
        issue: The code review issue with replacement_code
        work_dir: Optional base directory for the temp copy.
                  Defaults to system temp dir.

    Returns:
        Path to the temporary repo copy with the patch applied

    Raises:
        PatchError: If the patch cannot be applied
    """
    if not issue.replacement_code:
        raise PatchError("Issue has no replacement_code to apply.")

    # Create temp copy
    prefix = "reviewpilot_verify_"
    if work_dir:
        work_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=prefix, dir=work_dir))
    else:
        temp_dir = Path(tempfile.mkdtemp(prefix=prefix))

    try:
        # Copy repo to temp dir (ignore .git to save time/space)
        dest = temp_dir / "repo"
        shutil.copytree(
            repo_path,
            dest,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules", ".venv", "venv"),
        )

        # Apply the replacement
        target_file = dest / issue.file_path
        if not target_file.exists():
            raise PatchError(f"Target file does not exist: {issue.file_path}")

        new_content = apply_line_replacement(
            target_file,
            issue.start_line,
            issue.end_line,
            issue.replacement_code,
        )
        target_file.write_text(new_content, encoding="utf-8")

        logger.info(
            "Applied patch to %s (lines %d-%d) in %s",
            issue.file_path, issue.start_line, issue.end_line, dest,
        )
        return dest

    except Exception:
        # Clean up on failure
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def get_original_lines(
    repo_path: Path,
    file_path: str,
    start_line: int,
    end_line: int,
) -> str:
    """Extract the original lines from a file for comparison.

    Args:
        repo_path: Repository root path
        file_path: Relative path to the file
        start_line: First line (1-indexed)
        end_line: Last line (1-indexed, inclusive)

    Returns:
        The original source code for the specified line range
    """
    full_path = repo_path / file_path
    try:
        lines = full_path.read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[start_line - 1 : end_line])
    except (OSError, IndexError) as e:
        logger.warning("Could not read original lines from %s: %s", file_path, e)
        return ""
