"""Deterministic pre-filter checks for ReviewPilot.

Catches obvious code issues using regex patterns and simple AST queries
BEFORE calling any LLM. Zero cost, instant results. These checks are
high-confidence and low false-positive — things like hardcoded secrets,
eval() usage, SQL injection patterns, and missing error handling.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from reviewpilot.review.models import Category, CodeReviewIssue, Severity


@dataclass
class PatternRule:
    """A deterministic pattern-matching rule."""

    name: str
    pattern: re.Pattern[str]
    severity: Severity
    category: Category
    explanation: str
    file_extensions: list[str] | None = None  # None = apply to all files
    negative_pattern: re.Pattern[str] | None = None  # If matches, skip (false positive filter)


# ── Pattern Rules ──────────────────────────────────────────────────────

RULES: list[PatternRule] = [
    # Security: Hardcoded secrets
    PatternRule(
        name="hardcoded-api-key",
        pattern=re.compile(
            r"""(?:api[_-]?key|api[_-]?secret|auth[_-]?token|access[_-]?token|secret[_-]?key)"""
            r"""\s*[=:]\s*['\"][A-Za-z0-9_\-]{16,}['\"]""",
            re.IGNORECASE,
        ),
        severity=Severity.CRITICAL,
        category=Category.SECURITY,
        explanation=(
            "Hardcoded API key or secret detected. Use environment variables "
            "or a secrets manager instead of embedding credentials in source code."
        ),
        negative_pattern=re.compile(r"(example|placeholder|test|dummy|fake|xxx)", re.IGNORECASE),
    ),
    PatternRule(
        name="hardcoded-password",
        pattern=re.compile(
            r"""(?:password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{4,}['\"]""",
            re.IGNORECASE,
        ),
        severity=Severity.CRITICAL,
        category=Category.SECURITY,
        explanation=(
            "Hardcoded password detected. Never store passwords in source code. "
            "Use environment variables or a secrets manager."
        ),
        negative_pattern=re.compile(r"(example|placeholder|test|dummy|fake|xxx|hash|hashed)", re.IGNORECASE),
    ),
    # Security: Dangerous functions
    PatternRule(
        name="eval-usage",
        pattern=re.compile(r"\beval\s*\("),
        severity=Severity.CRITICAL,
        category=Category.SECURITY,
        explanation=(
            "Use of eval() is dangerous — it executes arbitrary code and can lead "
            "to remote code execution (RCE) vulnerabilities. Use ast.literal_eval() "
            "for safe evaluation of literals, or find an alternative approach."
        ),
        file_extensions=[".py"],
    ),
    PatternRule(
        name="exec-usage",
        pattern=re.compile(r"\bexec\s*\("),
        severity=Severity.MAJOR,
        category=Category.SECURITY,
        explanation=(
            "Use of exec() executes arbitrary code strings. This is almost always "
            "a security risk. Consider using safer alternatives."
        ),
        file_extensions=[".py"],
    ),
    # Security: SQL injection
    PatternRule(
        name="sql-injection-python",
        pattern=re.compile(
            r"""(?:execute|cursor\.execute)\s*\(\s*(?:f['\"]|['\"].*%s|.*\.format\()""",
            re.IGNORECASE,
        ),
        severity=Severity.CRITICAL,
        category=Category.SECURITY,
        explanation=(
            "Possible SQL injection vulnerability. String interpolation in SQL queries "
            "allows attackers to inject malicious SQL. Use parameterized queries instead: "
            "cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))"
        ),
        file_extensions=[".py"],
    ),
    PatternRule(
        name="sql-injection-js",
        pattern=re.compile(
            r"""(?:query|execute)\s*\(\s*(?:`[^`]*\$\{|['\"].*\+)""",
            re.IGNORECASE,
        ),
        severity=Severity.CRITICAL,
        category=Category.SECURITY,
        explanation=(
            "Possible SQL injection. Use parameterized queries instead of "
            "string concatenation or template literals in SQL statements."
        ),
        file_extensions=[".js", ".ts", ".jsx", ".tsx"],
    ),
    # Error handling: Bare except
    PatternRule(
        name="bare-except",
        pattern=re.compile(r"^\s*except\s*:\s*$"),
        severity=Severity.MAJOR,
        category=Category.ERROR_HANDLING,
        explanation=(
            "Bare 'except:' catches ALL exceptions including KeyboardInterrupt "
            "and SystemExit. Always specify the exception type: 'except ValueError:' "
            "or at minimum 'except Exception:'."
        ),
        file_extensions=[".py"],
    ),
    # Error handling: Empty catch block
    PatternRule(
        name="empty-catch-js",
        pattern=re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}"),
        severity=Severity.MAJOR,
        category=Category.ERROR_HANDLING,
        explanation=(
            "Empty catch block silently swallows errors. At minimum, log the error "
            "or add a comment explaining why it's intentionally ignored."
        ),
        file_extensions=[".js", ".ts", ".jsx", ".tsx"],
    ),
    # Bug: Console/print left in code
    PatternRule(
        name="console-log",
        pattern=re.compile(r"\bconsole\.log\s*\("),
        severity=Severity.MINOR,
        category=Category.MAINTAINABILITY,
        explanation=(
            "console.log() left in production code. Remove debug logging or "
            "replace with a proper logging library."
        ),
        file_extensions=[".js", ".ts", ".jsx", ".tsx"],
        negative_pattern=re.compile(r"(test|spec|debug|logger)", re.IGNORECASE),
    ),
    # Style: TODO/FIXME/HACK comments
    PatternRule(
        name="todo-fixme",
        pattern=re.compile(r"#\s*(?:TODO|FIXME|HACK|XXX)\b", re.IGNORECASE),
        severity=Severity.NITPICK,
        category=Category.MAINTAINABILITY,
        explanation=(
            "TODO/FIXME comment found in new code. Consider resolving this "
            "before merging, or create a tracked issue for follow-up."
        ),
    ),
]


def _get_file_extension(file_path: str) -> str:
    """Extract file extension from path."""
    dot_idx = file_path.rfind(".")
    if dot_idx == -1:
        return ""
    return file_path[dot_idx:].lower()


def _rule_applies_to_file(rule: PatternRule, file_path: str) -> bool:
    """Check if a rule should apply to the given file type."""
    if rule.file_extensions is None:
        return True
    ext = _get_file_extension(file_path)
    return ext in rule.file_extensions


def run_deterministic_checks(
    file_path: str,
    changed_lines: dict[int, str],
    min_severity: Severity = Severity.NITPICK,
) -> list[CodeReviewIssue]:
    """Run all deterministic pattern checks on changed lines of a file.

    Only checks lines that were actually added/modified in the diff —
    we don't flag pre-existing issues in unchanged code.

    Args:
        file_path: Relative path to the file
        changed_lines: Dict mapping line number (1-indexed) to line content
        min_severity: Minimum severity to report (filters out lower severity)

    Returns:
        List of CodeReviewIssue objects for deterministic findings
    """
    severity_order = [Severity.NITPICK, Severity.MINOR, Severity.MAJOR, Severity.CRITICAL]
    min_idx = severity_order.index(min_severity)

    issues: list[CodeReviewIssue] = []

    for rule in RULES:
        # Skip if rule doesn't apply to this file type
        if not _rule_applies_to_file(rule, file_path):
            continue

        # Skip if below minimum severity
        rule_idx = severity_order.index(rule.severity)
        if rule_idx < min_idx:
            continue

        # Check each changed line
        for line_num, line_content in sorted(changed_lines.items()):
            if rule.pattern.search(line_content):
                # Check negative pattern (false positive filter)
                if rule.negative_pattern and rule.negative_pattern.search(line_content):
                    continue

                issues.append(
                    CodeReviewIssue(
                        file_path=file_path,
                        start_line=line_num,
                        end_line=line_num,
                        severity=rule.severity,
                        category=rule.category,
                        reasoning=f"Deterministic check '{rule.name}' matched on line {line_num}.",
                        explanation=rule.explanation,
                        can_generate_patch=False,
                        replacement_code=None,
                    )
                )

    return issues
