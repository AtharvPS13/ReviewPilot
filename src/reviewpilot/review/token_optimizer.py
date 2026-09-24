"""Token optimization for ReviewPilot.

Manages LLM context window budgets by chunking large PRs into
multiple review calls and prioritizing which files get reviewed.
This prevents context overflow and keeps costs predictable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Rough token-to-character ratios by language
# (1 token ~ 4 chars for English, but code varies)
CHARS_PER_TOKEN = 3.5

# Default context budgets (in tokens)
DEFAULT_MAX_PROMPT_TOKENS = 12000   # Max tokens for the user prompt
DEFAULT_SYSTEM_PROMPT_TOKENS = 1500  # Reserved for system prompt + few-shot
DEFAULT_RESPONSE_TOKENS = 4096      # Reserved for LLM response


@dataclass
class TokenBudget:
    """Token budget for a single LLM call."""

    max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS
    system_tokens: int = DEFAULT_SYSTEM_PROMPT_TOKENS
    response_tokens: int = DEFAULT_RESPONSE_TOKENS

    @property
    def available_context_tokens(self) -> int:
        """Tokens available for file context in the user prompt."""
        return self.max_prompt_tokens - self.system_tokens

    @property
    def available_context_chars(self) -> int:
        """Approximate characters available for context."""
        return int(self.available_context_tokens * CHARS_PER_TOKEN)


@dataclass
class FileTokenEstimate:
    """Estimated token usage for a single file's context."""

    file_path: str
    context_text: str
    estimated_tokens: int
    priority: int  # Higher = more important to include

    @staticmethod
    def from_context(file_path: str, context_text: str, priority: int = 0) -> "FileTokenEstimate":
        """Create from a context text string."""
        estimated = int(len(context_text) / CHARS_PER_TOKEN)
        return FileTokenEstimate(
            file_path=file_path,
            context_text=context_text,
            estimated_tokens=estimated,
            priority=priority,
        )


def estimate_tokens(text: str) -> int:
    """Rough token estimate from text length.

    This is intentionally conservative (overestimates slightly)
    to avoid hitting context limits.
    """
    return int(len(text) / CHARS_PER_TOKEN) + 10


def prioritize_files(estimates: list[FileTokenEstimate]) -> list[FileTokenEstimate]:
    """Sort files by review priority (highest first).

    Priority factors:
    - Security-sensitive files get +10
    - Source files over test files get +5
    - Smaller files get slight boost (easier to review well)
    """
    for est in estimates:
        path_lower = est.file_path.lower()

        # Security files are high priority
        security_keywords = ["auth", "login", "crypto", "jwt", "token", "secret", "sql"]
        if any(kw in path_lower for kw in security_keywords):
            est.priority += 10

        # Source files over tests
        test_markers = ["test_", "_test.", ".test.", "spec.", "/tests/", "/__tests__/"]
        if not any(m in path_lower for m in test_markers):
            est.priority += 5

        # Config/infra files are lower priority
        infra_markers = ["docker", "ci", "deploy", "helm", "terraform", ".yml", ".yaml"]
        if any(m in path_lower for m in infra_markers):
            est.priority -= 3

    return sorted(estimates, key=lambda e: e.priority, reverse=True)


def chunk_for_context_window(
    file_estimates: list[FileTokenEstimate],
    budget: Optional[TokenBudget] = None,
) -> list[list[FileTokenEstimate]]:
    """Split files into chunks that fit within the LLM context window.

    Each chunk is a list of files whose combined token count fits
    within the budget. Files are added in priority order.

    Args:
        file_estimates: List of file token estimates (will be prioritized)
        budget: Token budget (defaults to standard budget)

    Returns:
        List of chunks, where each chunk is a list of FileTokenEstimate
    """
    budget = budget or TokenBudget()
    prioritized = prioritize_files(file_estimates)

    chunks: list[list[FileTokenEstimate]] = []
    current_chunk: list[FileTokenEstimate] = []
    current_tokens = 0
    max_tokens = budget.available_context_tokens

    for estimate in prioritized:
        if current_tokens + estimate.estimated_tokens <= max_tokens:
            current_chunk.append(estimate)
            current_tokens += estimate.estimated_tokens
        else:
            # Start a new chunk
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = [estimate]
            current_tokens = estimate.estimated_tokens

    if current_chunk:
        chunks.append(current_chunk)

    logger.info(
        "Split %d files into %d chunks (budget: %d tokens/chunk)",
        len(prioritized),
        len(chunks),
        max_tokens,
    )

    return chunks


def truncate_context(context_text: str, max_chars: int) -> str:
    """Truncate context text to fit within a character limit.

    Tries to truncate at a natural boundary (end of a function
    or section) rather than cutting mid-line.

    Args:
        context_text: The context text to truncate
        max_chars: Maximum character count

    Returns:
        Truncated text with a notice appended
    """
    if len(context_text) <= max_chars:
        return context_text

    # Find the last newline before the limit
    truncated = context_text[:max_chars]
    last_newline = truncated.rfind("\n")
    if last_newline > max_chars * 0.8:
        truncated = truncated[:last_newline]

    # Find a good cut point (end of a code block or section)
    for marker in ["\n```\n", "\n### ", "\n## ", "\n---\n"]:
        idx = truncated.rfind(marker)
        if idx > max_chars * 0.6:
            truncated = truncated[: idx + len(marker)]
            break

    return truncated + "\n\n[... truncated for context window limit ...]"


def build_optimized_prompt_chunks(
    file_contexts: list[tuple[str, str]],
    budget: Optional[TokenBudget] = None,
) -> list[list[str]]:
    """Build optimized prompt chunks from file contexts.

    Takes raw (file_path, context_text) pairs, estimates tokens,
    prioritizes, and splits into context-window-sized chunks.

    Args:
        file_contexts: List of (file_path, context_text) tuples
        budget: Token budget

    Returns:
        List of chunks, where each chunk is a list of context strings
    """
    budget = budget or TokenBudget()
    estimates = [
        FileTokenEstimate.from_context(path, text)
        for path, text in file_contexts
    ]

    chunks = chunk_for_context_window(estimates, budget)

    result: list[list[str]] = []
    for chunk in chunks:
        context_strings = []
        for est in chunk:
            # Truncate individual files if they're too large
            max_file_chars = budget.available_context_chars // max(len(chunk), 1)
            truncated = truncate_context(est.context_text, max_file_chars)
            context_strings.append(truncated)
        result.append(context_strings)

    return result
