"""Unified diff parser for ReviewPilot.

Parses raw git unified diffs into structured data with precise line number
tracking for both old and new files. Supports multiple files, multiple hunks,
new/deleted/renamed/binary files.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# Regex patterns for diff parsing
DIFF_HEADER_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")
HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
OLD_FILE_RE = re.compile(r"^--- (?:a/(.+)|(/dev/null))$")
NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/(.+)|(/dev/null))$")
RENAME_FROM_RE = re.compile(r"^rename from (.+)$")
RENAME_TO_RE = re.compile(r"^rename to (.+)$")
BINARY_RE = re.compile(r"^Binary files .* differ$")


@dataclass
class ChangedLine:
    """A single changed line in a diff."""

    line_number: int  # 1-indexed line number in the NEW file (for add/context)
    content: str  # The line content (without +/- prefix)
    change_type: str  # 'add', 'delete', or 'context'
    old_line_number: Optional[int] = None  # Line number in OLD file (for context/delete)


@dataclass
class DiffHunk:
    """A single hunk from a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str  # The full @@ line
    changes: list[ChangedLine] = field(default_factory=list)

    @property
    def added_lines(self) -> set[int]:
        """Line numbers of added lines in the new file."""
        return {c.line_number for c in self.changes if c.change_type == "add"}

    @property
    def modified_line_range(self) -> tuple[int, int]:
        """The range of lines in the new file that this hunk touches."""
        new_lines = [
            c.line_number
            for c in self.changes
            if c.line_number is not None and c.change_type != "delete"
        ]
        if not new_lines:
            return (self.new_start, self.new_start)
        return (min(new_lines), max(new_lines))


@dataclass
class FileDiff:
    """Parsed diff for a single file."""

    old_path: Optional[str]  # None for new files
    new_path: Optional[str]  # None for deleted files
    hunks: list[DiffHunk] = field(default_factory=list)
    is_new_file: bool = False
    is_deleted_file: bool = False
    is_renamed: bool = False
    is_binary: bool = False

    @property
    def path(self) -> str:
        """The current path of the file (new_path or old_path)."""
        return self.new_path or self.old_path or ""

    @property
    def changed_lines(self) -> set[int]:
        """All line numbers that were added in the new file."""
        result: set[int] = set()
        for hunk in self.hunks:
            result.update(hunk.added_lines)
        return result

    @property
    def all_new_file_lines(self) -> set[int]:
        """All line numbers that appear in the diff (added + context) for the new file.

        These are the valid lines for posting GitHub review comments.
        """
        result: set[int] = set()
        for hunk in self.hunks:
            for change in hunk.changes:
                if change.change_type in ("add", "context") and change.line_number is not None:
                    result.add(change.line_number)
        return result


@dataclass
class PatchSet:
    """A complete parsed patch containing multiple file diffs."""

    files: list[FileDiff] = field(default_factory=list)

    @property
    def changed_file_paths(self) -> list[str]:
        """List of all file paths that were changed."""
        return [f.path for f in self.files if f.path]


def parse_unified_diff(diff_text: str) -> PatchSet:
    """Parse a unified diff string into a structured PatchSet.

    Handles standard git unified diffs including:
    - File additions, deletions, modifications, and renames
    - Multiple hunks per file
    - Binary file markers
    - Proper line number tracking for both old and new files

    Args:
        diff_text: Raw unified diff string (e.g., from `git diff` or GitHub API)

    Returns:
        PatchSet containing all parsed file diffs
    """
    if not diff_text or not diff_text.strip():
        return PatchSet()

    patch_set = PatchSet()
    lines = diff_text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]

        # Look for diff header
        header_match = DIFF_HEADER_RE.match(line)
        if not header_match:
            i += 1
            continue

        file_diff = FileDiff(
            old_path=header_match.group(1),
            new_path=header_match.group(2),
        )
        i += 1

        # Parse extended headers (index, mode, rename, new file, etc.)
        while i < len(lines) and not lines[i].startswith("--- ") and not lines[i].startswith("diff --git"):
            ext_line = lines[i]

            if ext_line.startswith("new file"):
                file_diff.is_new_file = True
            elif ext_line.startswith("deleted file"):
                file_diff.is_deleted_file = True
            elif ext_line.startswith("rename from"):
                file_diff.is_renamed = True
                rename_match = RENAME_FROM_RE.match(ext_line)
                if rename_match:
                    file_diff.old_path = rename_match.group(1)
            elif ext_line.startswith("rename to"):
                rename_match = RENAME_TO_RE.match(ext_line)
                if rename_match:
                    file_diff.new_path = rename_match.group(1)
            elif BINARY_RE.match(ext_line):
                file_diff.is_binary = True
                patch_set.files.append(file_diff)
                i += 1
                break
            elif ext_line.startswith("@@ "):
                # Some diffs skip --- +++ and go straight to hunks
                break

            i += 1

            if file_diff.is_binary:
                continue

        if file_diff.is_binary:
            continue

        # Check if we hit the next diff header (file with no content changes)
        if i >= len(lines) or lines[i].startswith("diff --git"):
            patch_set.files.append(file_diff)
            continue

        # Parse --- line
        if i < len(lines) and lines[i].startswith("--- "):
            old_match = OLD_FILE_RE.match(lines[i])
            if old_match:
                if old_match.group(2):  # /dev/null
                    file_diff.old_path = None
                    file_diff.is_new_file = True
                elif old_match.group(1):
                    file_diff.old_path = old_match.group(1)
            i += 1

        # Parse +++ line
        if i < len(lines) and lines[i].startswith("+++ "):
            new_match = NEW_FILE_RE.match(lines[i])
            if new_match:
                if new_match.group(2):  # /dev/null
                    file_diff.new_path = None
                    file_diff.is_deleted_file = True
                elif new_match.group(1):
                    file_diff.new_path = new_match.group(1)
            i += 1

        # Parse hunks
        while i < len(lines) and not lines[i].startswith("diff --git"):
            hunk_match = HUNK_HEADER_RE.match(lines[i])
            if hunk_match:
                hunk = DiffHunk(
                    old_start=int(hunk_match.group(1)),
                    old_count=int(hunk_match.group(2) or "1"),
                    new_start=int(hunk_match.group(3)),
                    new_count=int(hunk_match.group(4) or "1"),
                    header=lines[i],
                )

                old_line = hunk.old_start
                new_line = hunk.new_start
                i += 1

                # Parse hunk content
                while i < len(lines):
                    hunk_line = lines[i]

                    if hunk_line.startswith("diff --git") or HUNK_HEADER_RE.match(hunk_line):
                        break

                    if hunk_line.startswith("+"):
                        hunk.changes.append(
                            ChangedLine(
                                line_number=new_line,
                                content=hunk_line[1:],
                                change_type="add",
                                old_line_number=None,
                            )
                        )
                        new_line += 1
                    elif hunk_line.startswith("-"):
                        hunk.changes.append(
                            ChangedLine(
                                line_number=new_line,  # Position in new file for reference
                                content=hunk_line[1:],
                                change_type="delete",
                                old_line_number=old_line,
                            )
                        )
                        old_line += 1
                    elif hunk_line.startswith("\\"):
                        # '\ No newline at end of file' — skip
                        i += 1
                        continue
                    else:
                        # Context line (starts with ' ' or is plain text)
                        content = hunk_line[1:] if hunk_line.startswith(" ") else hunk_line
                        hunk.changes.append(
                            ChangedLine(
                                line_number=new_line,
                                content=content,
                                change_type="context",
                                old_line_number=old_line,
                            )
                        )
                        old_line += 1
                        new_line += 1

                    i += 1

                file_diff.hunks.append(hunk)
            else:
                i += 1

        patch_set.files.append(file_diff)

    return patch_set
