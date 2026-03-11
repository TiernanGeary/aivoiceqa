"""Integration: Validate scenario YAML parsing edge cases."""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

import pytest
import yaml

from models.scenario import TestScenario, TestStep, ScenarioChecks


# ---------------------------------------------------------------------------
# Loading the real example scenario
# ---------------------------------------------------------------------------

SCENARIOS_DIR = Path(__file__).parent.parent.parent / "scenarios"


def test_example_scenario_yaml_loads_correctly() -> None:
    """Load the actual example_scripted.yaml and verify all fields parse."""
    scenario = TestScenario.from_yaml(SCENARIOS_DIR / "example_scripted.yaml")

    assert scenario.scenario_id == "booking_happy_path"
    assert scenario.mode == "scripted"
    assert scenario.flow_path == "flow/flow.yaml"
    assert scenario.description != ""
    assert len(scenario.steps) == 4

    # Step 1: no user_input (agent speaks first)
    assert scenario.steps[0].step == 1
    assert scenario.steps[0].user_input is None
    assert scenario.steps[0].expected_block == "greeting"
    assert scenario.steps[0].checks.keigo_level == "teineigo"

    # Step 2: has user_input and checks
    assert scenario.steps[1].step == 2
    assert scenario.steps[1].user_input == "はい、予約をお願いします"
    assert scenario.steps[1].checks.must_contain_meaning == ["日時"]

    # expected_turns
    assert scenario.expected_turns.min == 3
    assert scenario.expected_turns.max == 8

    # expected_duration
    assert scenario.expected_duration.min_seconds == 30
    assert scenario.expected_duration.max_seconds == 180

    # vad config
    assert scenario.vad.silence_threshold_ms == 1500


def test_scenario_with_audio_file_reference() -> None:
    """Scenario step with user_audio field loads correctly."""
    yaml_content = textwrap.dedent("""\
        scenario_id: audio_ref_test
        mode: scripted
        steps:
          - step: 1
            user_audio: "audio/greeting.wav"
            expected_block: greeting
    """)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        scenario = TestScenario.from_yaml(f.name)

    assert len(scenario.steps) == 1
    assert scenario.steps[0].user_audio == "audio/greeting.wav"
    assert scenario.steps[0].user_input is None


def test_scenario_with_text_input() -> None:
    """Scenario step with user_input field loads correctly."""
    yaml_content = textwrap.dedent("""\
        scenario_id: text_input_test
        mode: scripted
        steps:
          - step: 1
            user_input: "こんにちは"
            expected_block: greeting
    """)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        scenario = TestScenario.from_yaml(f.name)

    assert len(scenario.steps) == 1
    assert scenario.steps[0].user_input == "こんにちは"
    assert scenario.steps[0].user_audio is None


def test_scenario_with_no_user_input() -> None:
    """Step where agent speaks first (no user_input or user_audio)."""
    yaml_content = textwrap.dedent("""\
        scenario_id: no_input_test
        mode: scripted
        steps:
          - step: 1
            expected_block: greeting
    """)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        scenario = TestScenario.from_yaml(f.name)

    assert scenario.steps[0].user_input is None
    assert scenario.steps[0].user_audio is None


def test_scenario_missing_scenario_id_gives_error() -> None:
    """Missing scenario_id should give a clear KeyError."""
    yaml_content = textwrap.dedent("""\
        mode: scripted
        steps:
          - step: 1
    """)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        with pytest.raises(KeyError, match="scenario_id"):
            TestScenario.from_yaml(f.name)


def test_scenario_with_empty_steps() -> None:
    """Scenario with no steps should still load (empty list)."""
    yaml_content = textwrap.dedent("""\
        scenario_id: empty_steps
        mode: scripted
        steps: []
    """)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        scenario = TestScenario.from_yaml(f.name)

    assert scenario.scenario_id == "empty_steps"
    assert len(scenario.steps) == 0


def test_scenario_with_no_steps_key() -> None:
    """Scenario YAML with no 'steps' key defaults to empty list."""
    yaml_content = textwrap.dedent("""\
        scenario_id: no_steps_key
    """)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        scenario = TestScenario.from_yaml(f.name)

    assert len(scenario.steps) == 0


def test_scenario_with_all_checks() -> None:
    """Step with all check fields populated parses correctly."""
    yaml_content = textwrap.dedent("""\
        scenario_id: full_checks
        mode: scripted
        steps:
          - step: 1
            user_input: "テスト"
            expected_block: test_block
            checks:
              factual: "Agent should respond about testing"
              keigo_level: sonkeigo
              must_contain_meaning:
                - "テスト"
                - "確認"
    """)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        scenario = TestScenario.from_yaml(f.name)

    checks = scenario.steps[0].checks
    assert checks.factual == "Agent should respond about testing"
    assert checks.keigo_level == "sonkeigo"
    assert checks.must_contain_meaning == ["テスト", "確認"]


def test_scenario_defaults_for_optional_fields() -> None:
    """Optional fields get reasonable defaults."""
    yaml_content = textwrap.dedent("""\
        scenario_id: defaults_test
    """)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        scenario = TestScenario.from_yaml(f.name)

    assert scenario.mode == "scripted"
    assert scenario.flow_path is None
    assert scenario.description == ""
    assert scenario.expected_turns.min == 1
    assert scenario.expected_turns.max == 50
    assert scenario.expected_duration.min_seconds == 0
    assert scenario.expected_duration.max_seconds == 600
    assert scenario.vad.silence_threshold_ms == 1500
    assert scenario.persona is None
    assert scenario.evaluation is None


def test_scenario_persona_mode_fields() -> None:
    """Persona mode fields parse when present."""
    yaml_content = textwrap.dedent("""\
        scenario_id: persona_test
        mode: persona
        persona:
          name: "田中太郎"
          age: 35
          personality: "polite and patient"
        evaluation:
          criteria: "booking_complete"
    """)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        scenario = TestScenario.from_yaml(f.name)

    assert scenario.mode == "persona"
    assert scenario.persona is not None
    assert scenario.persona["name"] == "田中太郎"
    assert scenario.evaluation is not None


def test_scenario_directory_loading() -> None:
    """Load multiple scenarios from a directory."""
    from cli import load_scenarios

    # The scenarios/ directory should have at least one YAML file
    scenarios = load_scenarios(None, str(SCENARIOS_DIR))
    assert len(scenarios) >= 1
    assert all(isinstance(s, TestScenario) for s in scenarios)


def test_cli_load_single_scenario() -> None:
    """load_scenarios with a single file path works."""
    from cli import load_scenarios

    scenarios = load_scenarios(str(SCENARIOS_DIR / "example_scripted.yaml"), None)
    assert len(scenarios) == 1
    assert scenarios[0].scenario_id == "booking_happy_path"
