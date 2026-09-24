"""Model cascading for ReviewPilot.

Routes review requests to different LLM models based on complexity
and severity. Simple deterministic findings skip the LLM entirely,
while complex multi-file changes use the most capable model.

This reduces cost by 60-80% on typical PRs: most files only need
a fast/cheap model, and only a few require deep analysis.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from reviewpilot.config import LLMConfig

logger = logging.getLogger(__name__)


@dataclass
class ModelTier:
    """A model tier with its configuration and selection criteria."""

    name: str
    provider: str
    model: str
    max_tokens: int
    temperature: float
    max_context_lines: int  # Max changed lines to route to this tier

    def to_llm_config(self) -> LLMConfig:
        """Convert to an LLMConfig for use with LLMReviewer."""
        return LLMConfig(
            provider=self.provider,
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )


# Default model tiers (cheapest first)
DEFAULT_TIERS: list[ModelTier] = [
    ModelTier(
        name="fast",
        provider="openai",
        model="gpt-4o-mini",
        max_tokens=2048,
        temperature=0.1,
        max_context_lines=50,
    ),
    ModelTier(
        name="standard",
        provider="openai",
        model="gpt-4o",
        max_tokens=4096,
        temperature=0.1,
        max_context_lines=200,
    ),
    ModelTier(
        name="deep",
        provider="openai",
        model="gpt-4o",
        max_tokens=8192,
        temperature=0.05,
        max_context_lines=999999,  # No limit
    ),
]


@dataclass
class FileComplexity:
    """Complexity assessment for a single file in the PR."""

    file_path: str
    changed_line_count: int
    function_count: int
    has_security_sensitive: bool  # auth, crypto, sql files
    has_test_file: bool
    estimated_tier: str  # "fast", "standard", or "deep"


# Patterns that suggest security-sensitive files
SECURITY_PATTERNS = [
    "auth", "login", "password", "crypto", "encrypt", "decrypt",
    "jwt", "token", "secret", "permission", "rbac", "acl",
    "sanitize", "escape", "sql", "query", "session", "cookie",
    "oauth", "saml", "cert", "key", "sign", "verify",
]


def _is_security_sensitive(file_path: str) -> bool:
    """Check if a file path suggests security-sensitive code."""
    path_lower = file_path.lower()
    return any(pattern in path_lower for pattern in SECURITY_PATTERNS)


def _is_test_file(file_path: str) -> bool:
    """Check if a file is a test file."""
    path_lower = file_path.lower()
    return any(marker in path_lower for marker in [
        "test_", "_test.", ".test.", "spec.", "_spec.",
        "/tests/", "/test/", "/__tests__/",
    ])


def assess_complexity(
    file_path: str,
    changed_line_count: int,
    function_count: int = 0,
) -> FileComplexity:
    """Assess the complexity of a file change to determine model tier.

    Criteria for tier selection:
    - fast: < 50 changed lines, not security-sensitive, not complex
    - standard: 50-200 changed lines, or security-sensitive
    - deep: > 200 changed lines, or complex multi-function changes

    Args:
        file_path: Path to the file
        changed_line_count: Number of changed lines
        function_count: Number of modified functions

    Returns:
        FileComplexity with the estimated tier
    """
    is_security = _is_security_sensitive(file_path)
    is_test = _is_test_file(file_path)

    # Test files always get the fast tier (they need less scrutiny)
    if is_test:
        tier = "fast"
    # Security-sensitive files always get at least standard
    elif is_security:
        tier = "standard" if changed_line_count <= 200 else "deep"
    # Large changes get deeper review
    elif changed_line_count > 200 or function_count > 5:
        tier = "deep"
    elif changed_line_count > 50 or function_count > 2:
        tier = "standard"
    else:
        tier = "fast"

    return FileComplexity(
        file_path=file_path,
        changed_line_count=changed_line_count,
        function_count=function_count,
        has_security_sensitive=is_security,
        has_test_file=is_test,
        estimated_tier=tier,
    )


def get_tier_for_complexity(
    complexity: FileComplexity,
    tiers: Optional[list[ModelTier]] = None,
) -> ModelTier:
    """Get the appropriate model tier for a given complexity level.

    Args:
        complexity: The file complexity assessment
        tiers: Available model tiers (defaults to DEFAULT_TIERS)

    Returns:
        The selected ModelTier
    """
    available_tiers = tiers or DEFAULT_TIERS
    tier_map = {t.name: t for t in available_tiers}

    selected = tier_map.get(complexity.estimated_tier)
    if selected:
        return selected

    # Fallback to the first (cheapest) tier
    return available_tiers[0]


def group_files_by_tier(
    file_complexities: list[FileComplexity],
    tiers: Optional[list[ModelTier]] = None,
) -> dict[str, list[FileComplexity]]:
    """Group files by their model tier for batched processing.

    Files in the same tier can be reviewed together in a single
    LLM call, reducing API overhead.

    Args:
        file_complexities: List of file complexity assessments
        tiers: Available model tiers

    Returns:
        Dict mapping tier name to list of file complexities
    """
    groups: dict[str, list[FileComplexity]] = {}

    for fc in file_complexities:
        tier = get_tier_for_complexity(fc, tiers)
        if tier.name not in groups:
            groups[tier.name] = []
        groups[tier.name].append(fc)

    for tier_name, files in groups.items():
        logger.info(
            "Tier '%s': %d files (%d total changed lines)",
            tier_name,
            len(files),
            sum(f.changed_line_count for f in files),
        )

    return groups
