"""Tests for the config loader."""
from __future__ import annotations

import pytest
import tempfile
from pathlib import Path

from reviewpilot.config import (
    ReviewPilotConfig,
    LLMConfig,
    SandboxConfig,
    ReviewConfig,
    load_config,
    load_config_from_file,
    load_config_from_dict,
)
from reviewpilot.review.models import Severity


class TestDefaultConfig:
    def test_default_llm_provider(self):
        config = ReviewPilotConfig()
        assert config.llm.provider == "openai"

    def test_default_model(self):
        config = ReviewPilotConfig()
        assert config.llm.model == "gpt-4o-mini"

    def test_default_sandbox_enabled(self):
        config = ReviewPilotConfig()
        assert config.sandbox.enabled is True

    def test_default_max_comments(self):
        config = ReviewPilotConfig()
        assert config.review.max_comments == 15

    def test_default_severity_threshold(self):
        config = ReviewPilotConfig()
        assert config.review.severity_threshold == Severity.MINOR


class TestLoadConfigFromDict:
    def test_override_provider(self):
        config = load_config_from_dict({"llm": {"provider": "anthropic", "model": "claude-3-haiku"}})
        assert config.llm.provider == "anthropic"
        assert config.llm.model == "claude-3-haiku"

    def test_override_sandbox(self):
        config = load_config_from_dict({"sandbox": {"enabled": False}})
        assert config.sandbox.enabled is False

    def test_custom_ignore_patterns(self):
        config = load_config_from_dict({"ignore": ["*.md", "docs/**"]})
        assert "*.md" in config.ignore
        assert "docs/**" in config.ignore

    def test_empty_dict_gives_defaults(self):
        config = load_config_from_dict({})
        assert config.llm.provider == "openai"


class TestLoadConfigFromFile:
    def test_valid_yaml_file(self, tmp_path):
        config_file = tmp_path / ".reviewpilot.yml"
        config_file.write_text(
            "llm:\n  provider: ollama\n  model: llama3\nreview:\n  max_comments: 5\n"
        )
        config = load_config_from_file(config_file)
        assert config.llm.provider == "ollama"
        assert config.llm.model == "llama3"
        assert config.review.max_comments == 5

    def test_empty_yaml_gives_defaults(self, tmp_path):
        config_file = tmp_path / ".reviewpilot.yml"
        config_file.write_text("")
        config = load_config_from_file(config_file)
        assert config.llm.provider == "openai"

    def test_invalid_yaml_raises(self, tmp_path):
        config_file = tmp_path / ".reviewpilot.yml"
        config_file.write_text("{{invalid yaml content")
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_config_from_file(config_file)

    def test_non_dict_yaml_raises(self, tmp_path):
        config_file = tmp_path / ".reviewpilot.yml"
        config_file.write_text("- just\n- a\n- list\n")
        with pytest.raises(ValueError, match="YAML mapping"):
            load_config_from_file(config_file)

    def test_missing_file_raises(self):
        with pytest.raises(ValueError, match="Cannot read"):
            load_config_from_file("/nonexistent/path/.reviewpilot.yml")


class TestLoadConfig:
    def test_loads_from_repo_yml(self, tmp_path):
        config_file = tmp_path / ".reviewpilot.yml"
        config_file.write_text("llm:\n  provider: gemini\n")
        config = load_config(tmp_path)
        assert config.llm.provider == "gemini"

    def test_loads_from_repo_yaml(self, tmp_path):
        config_file = tmp_path / ".reviewpilot.yaml"
        config_file.write_text("llm:\n  provider: anthropic\n")
        config = load_config(tmp_path)
        assert config.llm.provider == "anthropic"

    def test_falls_back_to_defaults(self, tmp_path):
        config = load_config(tmp_path)
        assert config.llm.provider == "openai"
