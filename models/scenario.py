"""Test scenario and step definitions. Loaded from YAML files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ScenarioChecks:
    """Per-step evaluation checks."""
    factual: str | None = None
    keigo_level: str | None = None  # "teineigo", "sonkeigo", "kenjougo"
    must_contain_meaning: list[str] = field(default_factory=list)


@dataclass
class TurnRange:
    min: int = 1
    max: int = 50


@dataclass
class DurationRange:
    min_seconds: int = 0
    max_seconds: int = 600


@dataclass
class VadConfig:
    silence_threshold_ms: int = 1500


@dataclass
class TestStep:
    """A single step in a test scenario."""
    step: int
    expected_block: str | None = None
    user_input: str | None = None       # Text → auto-generate TTS
    user_audio: str | None = None       # Path to audio file (takes priority over user_input)
    checks: ScenarioChecks = field(default_factory=ScenarioChecks)


@dataclass
class TestScenario:
    """A complete test scenario loaded from YAML."""
    scenario_id: str
    mode: str = "scripted"  # "scripted" or "persona"
    flow_path: str | None = None
    description: str = ""
    steps: list[TestStep] = field(default_factory=list)
    expected_turns: TurnRange = field(default_factory=TurnRange)
    expected_duration: DurationRange = field(default_factory=DurationRange)
    vad: VadConfig = field(default_factory=VadConfig)

    # Persona mode fields (future)
    persona: dict | None = None
    evaluation: dict | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> TestScenario:
        """Load a scenario from a YAML file."""
        path = Path(path)
        with open(path) as f:
            data = yaml.safe_load(f)

        steps = []
        for step_data in data.get("steps", []):
            checks_data = step_data.pop("checks", {})
            checks = ScenarioChecks(
                factual=checks_data.get("factual"),
                keigo_level=checks_data.get("keigo_level"),
                must_contain_meaning=checks_data.get("must_contain_meaning", []),
            )
            steps.append(TestStep(checks=checks, **step_data))

        turns_data = data.get("expected_turns", {})
        duration_data = data.get("expected_duration", {})
        vad_data = data.get("vad", {})

        return cls(
            scenario_id=data["scenario_id"],
            mode=data.get("mode", "scripted"),
            flow_path=data.get("flow_path"),
            description=data.get("description", ""),
            steps=steps,
            expected_turns=TurnRange(**turns_data) if turns_data else TurnRange(),
            expected_duration=DurationRange(**duration_data) if duration_data else DurationRange(),
            vad=VadConfig(**vad_data) if vad_data else VadConfig(),
            persona=data.get("persona"),
            evaluation=data.get("evaluation"),
        )
