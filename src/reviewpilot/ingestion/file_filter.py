"""File filter for ReviewPilot.

Filters out files that should not be reviewed: lockfiles, binaries,
generated code, vendor directories, etc.
"""
from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from reviewpilot.ingestion.diff_parser import FileDiff, PatchSet

# Default patterns to always ignore
DEFAULT_IGNORE_PATTERNS: list[str] = [
    # Lockfiles
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
    "Gemfile.lock",
    "composer.lock",
    # Generated / build artifacts
    "*.min.js",
    "*.min.css",
    "*.bundle.js",
    "*.chunk.js",
    "*.generated.*",
    "*.pb.go",
    "*.pb.py",
    # Directories
    "node_modules/**",
    "vendor/**",
    "dist/**",
    "build/**",
    ".next/**",
    "__pycache__/**",
    # Binary / media
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.svg",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
    "*.pdf",
    "*.zip",
    "*.tar.gz",
    # Other
    ".env",
    ".env.*",
]


def should_ignore_file(
    file_path: str, ignore_patterns: list[str] | None = None
) -> bool:
    """Check if a file should be ignored based on glob patterns.

    Checks the file path against DEFAULT_IGNORE_PATTERNS and any additional
    custom patterns. Matching is done against both the full relative path
    and just the filename (basename).

    Args:
        file_path: Relative file path (e.g., 'src/utils/helper.py')
        ignore_patterns: Additional glob patterns to ignore.
                        Combined with DEFAULT_IGNORE_PATTERNS.

    Returns:
        True if the file should be ignored
    """
    all_patterns = DEFAULT_IGNORE_PATTERNS.copy()
    if ignore_patterns:
        all_patterns.extend(ignore_patterns)

    # Normalize path separators to forward slashes
    normalized_path = file_path.replace("\\", "/")
    filename = PurePosixPath(normalized_path).name

    for pattern in all_patterns:
        # Check against full path
        if fnmatch.fnmatch(normalized_path, pattern):
            return True
        # Check against just the filename (for non-path patterns like "*.lock")
        if fnmatch.fnmatch(filename, pattern):
            return True
        # Check if any parent path segment matches directory patterns
        if "**" in pattern:
            # For patterns like "node_modules/**", check if path starts with the prefix
            prefix = pattern.replace("/**", "")
            if normalized_path.startswith(prefix + "/") or normalized_path == prefix:
                return True

    return False


def filter_patch_set(
    patch_set: PatchSet, ignore_patterns: list[str] | None = None
) -> PatchSet:
    """Filter a PatchSet to remove files that should not be reviewed.

    Removes files that match ignore patterns, are binary, are deleted,
    or have no actual changes (empty hunks).

    Args:
        patch_set: The parsed PatchSet to filter
        ignore_patterns: Additional glob patterns to ignore

    Returns:
        A new PatchSet with ignored files removed
    """
    filtered_files: list[FileDiff] = []

    for file_diff in patch_set.files:
        # Skip binary files
        if file_diff.is_binary:
            continue

        # Skip deleted files (nothing to review in the new version)
        if file_diff.is_deleted_file:
            continue

        # Skip files with no hunks (no actual changes)
        if not file_diff.hunks:
            continue

        # Skip files matching ignore patterns
        if file_diff.path and should_ignore_file(file_diff.path, ignore_patterns):
            continue

        filtered_files.append(file_diff)

    return PatchSet(files=filtered_files)
