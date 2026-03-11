"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from models.scenario import TestScenario


FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios"


@pytest.fixture
def example_scenario() -> TestScenario:
    """Load the example scripted scenario."""
    return TestScenario.from_yaml(SCENARIOS_DIR / "example_scripted.yaml")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR
