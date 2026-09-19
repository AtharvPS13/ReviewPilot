"""Context builder for ReviewPilot.

Builds structured LLM prompt context by combining AST analysis with diff
information. Modified functions get full source code, while unmodified
dependencies get only their signatures (skeletons) to save tokens.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from reviewpilot.analysis.ast_analyzer import FileAnalysis, FunctionInfo, analyze_file
from reviewpilot.ingestion.diff_parser import FileDiff


@dataclass
class FileContext:
    """Structured context for a single file to send to the LLM."""

    file_path: str
    language: str
    modified_functions: list[FunctionContext] = field(default_factory=list)
    skeleton_functions: list[str] = field(default_factory=list)  # Signature-only
    imports: list[str] = field(default_factory=list)
    raw_diff_hunks: list[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Convert to a formatted string for LLM prompt injection."""
        parts = [f"## File: {self.file_path} ({self.language})"]

        if self.imports:
            parts.append("\n### Imports")
            parts.append("\n".join(self.imports))

        if self.modified_functions:
            parts.append("\n### Modified Functions (REVIEW THESE)")
            for func_ctx in self.modified_functions:
                parts.append(func_ctx.to_prompt_text())

        if self.skeleton_functions:
            parts.append("\n### Dependency Context (signatures only, DO NOT review)")
            parts.append("\n".join(self.skeleton_functions))

        if self.raw_diff_hunks:
            parts.append("\n### Diff Hunks")
            for hunk in self.raw_diff_hunks:
                parts.append(f"```diff\n{hunk}\n```")

        return "\n".join(parts)


@dataclass
class FunctionContext:
    """Context for a single modified function."""

    name: str
    full_source: str  # Full source code with line numbers
    start_line: int
    end_line: int
    signature: str
    changed_line_numbers: list[int]  # Which lines within this function changed

    def to_prompt_text(self) -> str:
        """Format for LLM prompt with line numbers."""
        changed_str = ", ".join(str(ln) for ln in sorted(self.changed_line_numbers))
        header = f"\n#### `{self.name}` (lines {self.start_line}-{self.end_line}, changed: [{changed_str}])"
        return f"{header}\n```\n{self.full_source}\n```"


def _add_line_numbers(source: str, start_line: int) -> str:
    """Add line numbers to source code for precise LLM references.

    Example:
        10 |     result = x * 2
        11 |     if result > 100:
        12 |         return 100
    """
    lines = source.splitlines()
    numbered_lines = []
    for i, line in enumerate(lines):
        line_num = start_line + i
        numbered_lines.append(f"{line_num:>4} | {line}")
    return "\n".join(numbered_lines)


def build_file_context(
    file_diff: FileDiff,
    source_code: str,
) -> Optional[FileContext]:
    """Build structured LLM context for a single file.

    Combines diff information with AST analysis to produce a context
    that gives the LLM maximum information with minimum tokens.

    Args:
        file_diff: Parsed diff for this file
        source_code: Full source code of the file (new version)

    Returns:
        FileContext ready for prompt injection, or None if language unsupported
    """
    analysis = analyze_file(source_code, file_diff.path)
    if analysis is None:
        return None

    changed_lines = file_diff.changed_lines
    source_lines = source_code.splitlines()

    # Build context for modified functions (full source with line numbers)
    modified_funcs = analysis.get_modified_functions(changed_lines)
    modified_contexts = []
    for func in modified_funcs:
        func_source = "\n".join(source_lines[func.start_line - 1 : func.end_line])
        numbered_source = _add_line_numbers(func_source, func.start_line)

        func_changed_lines = [
            ln for ln in sorted(changed_lines) if func.start_line <= ln <= func.end_line
        ]

        modified_contexts.append(
            FunctionContext(
                name=func.name,
                full_source=numbered_source,
                start_line=func.start_line,
                end_line=func.end_line,
                signature=func.signature,
                changed_line_numbers=func_changed_lines,
            )
        )

    # Build skeletons for unmodified functions (signatures only — saves tokens)
    unmodified_funcs = analysis.get_unmodified_functions(changed_lines)
    skeletons = [func.skeleton for func in unmodified_funcs]

    # Extract raw diff hunk text for additional context
    raw_hunks = []
    for hunk in file_diff.hunks:
        hunk_lines = []
        for change in hunk.changes:
            prefix = {"add": "+", "delete": "-", "context": " "}.get(change.change_type, " ")
            hunk_lines.append(f"{prefix}{change.content}")
        raw_hunks.append("\n".join(hunk_lines))

    return FileContext(
        file_path=file_diff.path,
        language=analysis.language,
        modified_functions=modified_contexts,
        skeleton_functions=skeletons,
        imports=analysis.imports,
        raw_diff_hunks=raw_hunks,
    )
