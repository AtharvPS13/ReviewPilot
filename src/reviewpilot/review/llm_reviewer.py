"""LLM reviewer for ReviewPilot.

Integrates with LLMs via LiteLLM to perform semantic code review.
Uses instructor for structured Pydantic output validation with
automatic retries on schema violations.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import instructor
import litellm
from pydantic import ValidationError

from reviewpilot.config import LLMConfig
from reviewpilot.review.models import PRReviewResponse
from reviewpilot.review.prompt_templates import (
    FEW_SHOT_EXAMPLE,
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_review_prompt,
)

logger = logging.getLogger(__name__)


class LLMReviewer:
    """Performs code review using an LLM with structured output.

    Uses LiteLLM for provider-agnostic LLM access and instructor
    for Pydantic-validated structured JSON output.
    """

    def __init__(self, config: LLMConfig) -> None:
        """Initialize the LLM reviewer.

        Args:
            config: LLM configuration (provider, model, API key env var)
        """
        self.config = config
        self._setup_api_key()

    def _setup_api_key(self) -> None:
        """Set up the API key from the configured environment variable."""
        api_key = os.environ.get(self.config.api_key_env, "")
        if not api_key and self.config.provider != "ollama":
            logger.warning(
                "API key env var '%s' is not set. LLM calls will fail.",
                self.config.api_key_env,
            )

    def _get_model_string(self) -> str:
        """Get the LiteLLM model string for the configured provider.

        LiteLLM uses provider prefixes: 'anthropic/claude-3-haiku',
        'ollama/llama3', etc. OpenAI models need no prefix.
        """
        provider = self.config.provider.lower()
        model = self.config.model

        if provider == "openai":
            return model  # e.g., 'gpt-4o-mini'
        elif provider == "anthropic":
            return f"anthropic/{model}"
        elif provider == "ollama":
            return f"ollama/{model}"
        elif provider == "gemini":
            return f"gemini/{model}"
        else:
            # Try direct pass-through for other providers
            return f"{provider}/{model}"

    async def review(
        self,
        file_contexts: list[str],
        pr_title: str = "",
        pr_body: str = "",
    ) -> PRReviewResponse:
        """Perform an LLM code review on the provided file contexts.

        Args:
            file_contexts: List of formatted file context strings
            pr_title: PR title for context
            pr_body: PR description for context

        Returns:
            Structured PRReviewResponse with findings

        Raises:
            LLMError: If the LLM call fails after retries
        """
        user_prompt = build_review_prompt(file_contexts, pr_title, pr_body)
        model = self._get_model_string()

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + FEW_SHOT_EXAMPLE},
            {"role": "user", "content": user_prompt},
        ]

        try:
            # Use instructor for structured output with automatic retry
            client = instructor.from_litellm(litellm.acompletion)
            response = await client.create(
                model=model,
                messages=messages,
                response_model=PRReviewResponse,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                max_retries=2,  # Retry up to 2 times on schema validation failure
            )
            return response

        except ValidationError as e:
            logger.error("LLM output failed Pydantic validation after retries: %s", e)
            # Return empty review rather than crashing
            return PRReviewResponse(
                summary="Review could not be completed due to LLM output validation error.",
                risk_level="low",
                issues=[],
            )
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            raise LLMError(f"LLM review failed: {e}") from e

    async def repair_suggestion(
        self,
        original_code: str,
        suggested_code: str,
        error_output: str,
        file_path: str,
    ) -> Optional[str]:
        """Ask the LLM to fix a suggestion that failed verification.

        Args:
            original_code: The original code
            suggested_code: The suggestion that failed
            error_output: The test/compilation error
            file_path: Path to the file

        Returns:
            Repaired code string, or None if repair failed
        """
        repair_prompt = build_repair_prompt(
            original_code, suggested_code, error_output, file_path
        )
        model = self._get_model_string()

        try:
            response = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a code repair assistant. Fix the code."},
                    {"role": "user", "content": repair_prompt},
                ],
                temperature=0.0,  # Deterministic for repairs
                max_tokens=2048,
            )
            content = response.choices[0].message.content
            if content:
                # Strip markdown code fences if the LLM wraps the output
                content = content.strip()
                if content.startswith("```"):
                    lines = content.splitlines()
                    # Remove first and last lines (``` markers)
                    content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
                return content
            return None

        except Exception as e:
            logger.warning("Repair attempt failed: %s", e)
            return None


class LLMError(Exception):
    """Raised when an LLM call fails."""
    pass
