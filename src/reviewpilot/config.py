"""Configuration loader for ReviewPilot.

Loads and validates the .reviewpilot.yml configuration file from the
target repository. Falls back to sensible defaults when no config exists.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

from reviewpilot.review.models import Severity


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = Field(default="openai", description="LLM provider: openai, anthropic, ollama, gemini")
    model: str = Field(default="gpt-4o-mini", description="Model name")
    api_key_env: str = Field(default="OPENAI_API_KEY", description="Environment variable name for API key")
    temperature: float = Field(default=0.1, description="Sampling temperature (lower = more deterministic)")
    max_tokens: int = Field(default=4096, description="Max tokens in LLM response")


class SandboxConfig(BaseModel):
    """Docker sandbox configuration."""

    enabled: bool = Field(default=True, description="Enable sandbox verification")
    docker_image: str = Field(default="python:3.11-slim", description="Base Docker image")
    test_command: str = Field(default="echo 'No test command configured'", description="Command to run tests")
    timeout_seconds: int = Field(default=120, description="Max time for sandbox execution")
    memory_limit: str = Field(default="1g", description="Docker memory limit")
    max_repair_attempts: int = Field(default=1, description="Max LLM self-repair attempts on failure")


class ReviewConfig(BaseModel):
    """Review behavior configuration."""

    max_comments: int = Field(default=15, description="Max inline comments per review")
    severity_threshold: Severity = Field(default=Severity.MINOR, description="Minimum severity to post")
    post_summary: bool = Field(default=True, description="Post a PR summary comment")
    deterministic_only: bool = Field(default=False, description="Skip LLM, only run deterministic checks")


class ReviewPilotConfig(BaseModel):
    """Complete ReviewPilot configuration."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    ignore: list[str] = Field(default_factory=list, description="Additional glob patterns to ignore")
    languages: list[str] = Field(
        default_factory=lambda: ["python", "javascript", "typescript"],
        description="Languages to review",
    )


def load_config(repo_path: str | Path) -> ReviewPilotConfig:
    """Load ReviewPilot configuration from a repository.

    Looks for .reviewpilot.yml or .reviewpilot.yaml in the repo root.
    Falls back to default configuration if no config file exists.

    Args:
        repo_path: Path to the repository root

    Returns:
        Validated ReviewPilotConfig
    """
    repo_path = Path(repo_path)

    # Try both .yml and .yaml extensions
    for config_name in (".reviewpilot.yml", ".reviewpilot.yaml"):
        config_file = repo_path / config_name
        if config_file.exists():
            return load_config_from_file(config_file)

    # No config file — return defaults
    return ReviewPilotConfig()


def load_config_from_file(config_path: str | Path) -> ReviewPilotConfig:
    """Load and validate configuration from a specific file.

    Args:
        config_path: Path to the YAML config file

    Returns:
        Validated ReviewPilotConfig

    Raises:
        ValueError: If the config file is invalid
    """
    config_path = Path(config_path)

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Cannot read config file {config_path}: {e}") from e

    try:
        raw_data = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {config_path}: {e}") from e

    if raw_data is None:
        return ReviewPilotConfig()

    if not isinstance(raw_data, dict):
        raise ValueError(f"Config file must contain a YAML mapping, got {type(raw_data).__name__}")

    return ReviewPilotConfig(**raw_data)


def load_config_from_dict(data: dict) -> ReviewPilotConfig:
    """Load configuration from a dictionary (useful for testing).

    Args:
        data: Configuration dictionary

    Returns:
        Validated ReviewPilotConfig
    """
    return ReviewPilotConfig(**data)
