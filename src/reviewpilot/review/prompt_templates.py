"""Prompt templates for ReviewPilot LLM integration.

Structured prompts designed for consistent, high-quality code review.
The system prompt is static (cache-eligible), while the user prompt
is dynamically built from diff context.
"""
from __future__ import annotations

SYSTEM_PROMPT = """\
You are an expert senior software engineer performing a code review on a Pull Request.

## Your Task
Review the code changes provided and identify real, actionable issues. Focus on:
1. **Security vulnerabilities** — injection, auth flaws, secret exposure, unsafe deserialization
2. **Bugs** — logic errors, off-by-one, null/undefined access, race conditions, resource leaks
3. **Performance** — N+1 queries, unnecessary allocations, blocking I/O in async code
4. **Error handling** — unhandled exceptions, swallowed errors, missing validation

## Rules
- ONLY flag issues in the CHANGED lines (lines marked as modified). Do NOT review unchanged code.
- Be PRECISE with line numbers. Your start_line and end_line must exactly match the code you're referencing.
- If you suggest replacement code, it must be a DROP-IN replacement for lines start_line through end_line.
- Do NOT flag style issues (formatting, naming conventions) — linters handle those.
- Do NOT suggest adding comments or documentation unless there's a real clarity issue.
- If the code looks correct and well-written, return an EMPTY issues list. Do not invent problems.
- Quality over quantity: 3 real findings beat 10 nitpicks.

## Output Format
Return a JSON object matching the schema exactly. The 'reasoning' field must contain your \
step-by-step analysis BEFORE you write any replacement_code. Think first, then code.
"""

FEW_SHOT_EXAMPLE = """\
Example of a good review finding:

```json
{
  "file_path": "src/auth/jwt.py",
  "start_line": 45,
  "end_line": 45,
  "severity": "critical",
  "category": "security",
  "reasoning": "The jwt.decode() call uses verify=False which disables signature verification. \
An attacker could forge any JWT token by simply base64-encoding a payload, gaining unauthorized \
access to any account. The SECRET_KEY is available in the same module, so verification should \
be enabled.",
  "explanation": "JWT decoded without signature verification — any client can forge tokens.",
  "can_generate_patch": true,
  "replacement_code": "    payload = jwt.decode(token, key=SECRET_KEY, algorithms=[\\"HS256\\"])"
}
```
"""


def build_review_prompt(file_contexts: list[str], pr_title: str = "", pr_body: str = "") -> str:
    """Build the user prompt for LLM code review.

    Args:
        file_contexts: List of formatted file context strings (from context_builder)
        pr_title: PR title for additional context
        pr_body: PR description for additional context

    Returns:
        Complete user prompt string
    """
    parts = []

    if pr_title:
        parts.append(f"## Pull Request: {pr_title}")
        if pr_body:
            # Truncate long PR bodies to save tokens
            body = pr_body[:500] + "..." if len(pr_body) > 500 else pr_body
            parts.append(f"\n{body}\n")

    parts.append("## Code Changes to Review\n")
    parts.append("\n---\n".join(file_contexts))

    parts.append("\n## Instructions")
    parts.append(
        "Review the code changes above. Return a JSON object with:\n"
        '- "summary": 2-3 sentence overview of the changes\n'
        '- "risk_level": "low", "medium", or "high"\n'
        '- "issues": array of issues found (empty if code is clean)\n\n'
        "Remember: only flag issues in CHANGED lines. Be precise with line numbers."
    )

    return "\n".join(parts)


def build_repair_prompt(
    original_code: str,
    suggested_code: str,
    error_output: str,
    file_path: str,
) -> str:
    """Build a prompt for LLM self-repair when a suggestion fails tests.

    Args:
        original_code: The original code before the suggestion
        suggested_code: The code suggestion that failed
        error_output: Test/compilation error output
        file_path: Path to the file

    Returns:
        Repair prompt string
    """
    return f"""\
Your previous code suggestion for `{file_path}` failed verification.

## Original Code
```
{original_code}
```

## Your Suggestion (FAILED)
```
{suggested_code}
```

## Error Output
```
{error_output[:2000]}
```

## Task
Fix your suggestion so it compiles and passes tests. Return ONLY the corrected \
replacement code — no explanations, no markdown backticks, just the code.
"""
