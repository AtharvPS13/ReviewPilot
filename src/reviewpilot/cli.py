"""Command-line interface for ReviewPilot.

Provides a local review mode for testing and a GitHub Action mode.
Users can run ReviewPilot locally on a diff file or against a PR
without deploying the Action.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from reviewpilot.analysis.context_builder import build_file_context
from reviewpilot.analysis.deterministic import run_deterministic_checks
from reviewpilot.config import ReviewPilotConfig, load_config, load_config_from_dict
from reviewpilot.ingestion.diff_parser import parse_unified_diff
from reviewpilot.ingestion.file_filter import filter_patch_set
from reviewpilot.review.models import CodeReviewIssue, Severity


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="reviewpilot",
        description="AI code review with sandbox verification",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── review (local diff) ───────────────────────────────────────────
    review_parser = subparsers.add_parser(
        "review",
        help="Review a local diff file or piped diff",
    )
    review_parser.add_argument(
        "diff_file",
        nargs="?",
        default="-",
        help="Path to a unified diff file (or '-' to read from stdin)",
    )
    review_parser.add_argument(
        "--repo",
        type=str,
        default=".",
        help="Path to the repository root (for source file reading)",
    )
    review_parser.add_argument(
        "--deterministic-only",
        action="store_true",
        help="Only run deterministic checks, skip LLM review",
    )
    review_parser.add_argument(
        "--severity",
        type=str,
        choices=["critical", "major", "minor", "nitpick"],
        default="minor",
        help="Minimum severity to report",
    )
    review_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON",
    )

    # ── check (deterministic only, fast) ──────────────────────────────
    check_parser = subparsers.add_parser(
        "check",
        help="Run only deterministic checks (no LLM, no cost)",
    )
    check_parser.add_argument(
        "diff_file",
        nargs="?",
        default="-",
        help="Path to a unified diff file (or '-' to read from stdin)",
    )
    check_parser.add_argument(
        "--repo",
        type=str,
        default=".",
        help="Path to the repository root",
    )
    check_parser.add_argument(
        "--severity",
        type=str,
        choices=["critical", "major", "minor", "nitpick"],
        default="minor",
        help="Minimum severity to report",
    )

    # ── parse (debug diff parsing) ────────────────────────────────────
    parse_parser = subparsers.add_parser(
        "parse",
        help="Parse a diff and show the extracted structure (debugging)",
    )
    parse_parser.add_argument(
        "diff_file",
        nargs="?",
        default="-",
        help="Path to a unified diff file (or '-' to read from stdin)",
    )

    # ── analyze (debug AST analysis) ──────────────────────────────────
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Analyze a source file and show extracted functions/classes",
    )
    analyze_parser.add_argument(
        "source_file",
        help="Path to the source file to analyze",
    )

    return parser


def _read_diff(path: str) -> str:
    """Read diff from file path or stdin."""
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _severity_from_str(s: str) -> Severity:
    """Convert severity string to enum."""
    return Severity(s.lower())


def cmd_review(args: argparse.Namespace) -> int:
    """Handle the 'review' command."""
    raw_diff = _read_diff(args.diff_file)
    patch_set = parse_unified_diff(raw_diff)
    repo_path = Path(args.repo)
    config = load_config(repo_path)

    filtered = filter_patch_set(patch_set, config.ignore)
    if not filtered.files:
        print("No reviewable files in this diff.")
        return 0

    min_severity = _severity_from_str(args.severity)
    all_issues: list[CodeReviewIssue] = []

    # Run deterministic checks
    for file_diff in filtered.files:
        source_path = repo_path / file_diff.path
        if not source_path.exists():
            continue

        source = source_path.read_text(encoding="utf-8")
        source_lines = source.splitlines()
        changed_lines_map: dict[int, str] = {}
        for ln in file_diff.changed_lines:
            if 1 <= ln <= len(source_lines):
                changed_lines_map[ln] = source_lines[ln - 1]

        det_issues = run_deterministic_checks(file_diff.path, changed_lines_map, min_severity)
        all_issues.extend(det_issues)

    # LLM review (unless deterministic-only)
    if not args.deterministic_only:
        from reviewpilot.review.llm_reviewer import LLMReviewer

        file_contexts: list[str] = []
        for file_diff in filtered.files:
            source_path = repo_path / file_diff.path
            if not source_path.exists():
                continue
            source = source_path.read_text(encoding="utf-8")
            ctx = build_file_context(file_diff, source)
            if ctx:
                file_contexts.append(ctx.to_prompt_text())

        if file_contexts:
            reviewer = LLMReviewer(config.llm)
            response = asyncio.run(reviewer.review(file_contexts))
            all_issues.extend(response.issues)

    # Output
    if args.json_output:
        output = [issue.model_dump() for issue in all_issues]
        print(json.dumps(output, indent=2))
    else:
        _print_issues(all_issues)

    return 1 if any(i.severity in (Severity.CRITICAL, Severity.MAJOR) for i in all_issues) else 0


def cmd_check(args: argparse.Namespace) -> int:
    """Handle the 'check' command (deterministic only)."""
    raw_diff = _read_diff(args.diff_file)
    patch_set = parse_unified_diff(raw_diff)
    repo_path = Path(args.repo)
    config = load_config(repo_path)

    filtered = filter_patch_set(patch_set, config.ignore)
    if not filtered.files:
        print("No reviewable files in this diff.")
        return 0

    min_severity = _severity_from_str(args.severity)
    all_issues: list[CodeReviewIssue] = []

    for file_diff in filtered.files:
        source_path = repo_path / file_diff.path
        if not source_path.exists():
            continue

        source = source_path.read_text(encoding="utf-8")
        source_lines = source.splitlines()
        changed_lines_map: dict[int, str] = {}
        for ln in file_diff.changed_lines:
            if 1 <= ln <= len(source_lines):
                changed_lines_map[ln] = source_lines[ln - 1]

        det_issues = run_deterministic_checks(file_diff.path, changed_lines_map, min_severity)
        all_issues.extend(det_issues)

    _print_issues(all_issues)
    return 1 if any(i.severity in (Severity.CRITICAL, Severity.MAJOR) for i in all_issues) else 0


def cmd_parse(args: argparse.Namespace) -> int:
    """Handle the 'parse' command (debug diff parsing)."""
    raw_diff = _read_diff(args.diff_file)
    patch_set = parse_unified_diff(raw_diff)

    print(f"Files: {len(patch_set.files)}")
    for f in patch_set.files:
        status = ""
        if f.is_new_file:
            status = " [NEW]"
        elif f.is_deleted_file:
            status = " [DELETED]"
        elif f.is_binary:
            status = " [BINARY]"

        print(f"\n  {f.path}{status}")
        print(f"    Hunks: {len(f.hunks)}")
        print(f"    Changed lines: {sorted(f.changed_lines)[:20]}{'...' if len(f.changed_lines) > 20 else ''}")

    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Handle the 'analyze' command (debug AST analysis)."""
    from reviewpilot.analysis.ast_analyzer import analyze_file

    source_path = Path(args.source_file)
    if not source_path.exists():
        print(f"File not found: {source_path}")
        return 1

    source = source_path.read_text(encoding="utf-8")
    result = analyze_file(source, str(source_path))

    if result is None:
        print(f"Unsupported language for: {source_path}")
        return 1

    print(f"Language: {result.language}")
    print(f"Functions: {len(result.functions)}")
    for func in result.functions:
        method_label = f" (method of {func.class_name})" if func.is_method else ""
        async_label = "async " if func.is_async else ""
        print(f"  {async_label}{func.name}{func.parameters} [{func.start_line}-{func.end_line}]{method_label}")

    print(f"\nClasses: {len(result.classes)}")
    for cls in result.classes:
        bases_str = f" extends {', '.join(cls.base_classes)}" if cls.base_classes else ""
        print(f"  {cls.name}{bases_str} [{cls.start_line}-{cls.end_line}]")

    print(f"\nImports: {len(result.imports)}")
    for imp in result.imports:
        print(f"  {imp}")

    return 0


def _print_issues(issues: list[CodeReviewIssue]) -> None:
    """Pretty-print issues to the terminal."""
    if not issues:
        print("No issues found.")
        return

    severity_colors = {
        Severity.CRITICAL: "\033[91m",  # Red
        Severity.MAJOR: "\033[93m",     # Yellow
        Severity.MINOR: "\033[96m",     # Cyan
        Severity.NITPICK: "\033[90m",   # Gray
    }
    reset = "\033[0m"

    print(f"\nFound {len(issues)} issue(s):\n")
    for i, issue in enumerate(issues, 1):
        color = severity_colors.get(issue.severity, "")
        print(f"  {color}[{issue.severity.value.upper()}]{reset} {issue.file_path}:{issue.start_line}")
        print(f"    {issue.explanation}")
        if issue.replacement_code:
            print(f"    Suggested fix available")
        print()


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    command_map = {
        "review": cmd_review,
        "check": cmd_check,
        "parse": cmd_parse,
        "analyze": cmd_analyze,
    }

    handler = command_map.get(args.command)
    if handler:
        exit_code = handler(args)
        sys.exit(exit_code)
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
