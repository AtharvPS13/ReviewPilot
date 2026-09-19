"""Shared pytest fixtures for the ReviewPilot test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fixtures_path() -> Path:
    """Return the path to the tests/fixtures/ directory.

    Creates the directory if it does not already exist so that tests
    relying on fixture files can always reference a valid path.
    """
    path = Path(__file__).parent / "fixtures"
    path.mkdir(exist_ok=True)
    return path
