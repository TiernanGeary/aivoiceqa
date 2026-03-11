"""Integration: Full mock pipeline from scenario through evaluator to reporter.

Verifies the complete flow works end-to-end in mock mode with zero env vars.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

from cli import _mock_vad_model, build_runner
from core.audio_gen import AudioGenerator, PreparedAudio
from core.evaluator import Evaluator
from core.reporter import Reporter
from core.scenario_runner import ScenarioRunner
from core.vad import TurnDetector
from models.result import ScenarioResult, StepResult
from models.scenario import TestScenario
from receivers.mock_receiver import MockReceiver
from reco.client import RecoClient


def _build_mock_runner(
    agent_speech_ms: float = 500,
    agent_silence_ms: float = 2000,
    num_agent_turns: int = 10,
) -> ScenarioRunner:
    """Build a ScenarioRunner fully wired in mock mode."""
    reco = RecoClient(base_url="http://localhost:3010", token="fake", mock=True)
    audio_gen = AudioGenerator(tts_provider="mock")
    vad = TurnDetector(
        silence_threshold_ms=1500,
        min_speech_ms=300,
        vad_model=_mock_vad_model(),
    )
    receiver = MockReceiver(
        agent_speech_ms=agent_speech_ms,
        agent_silence_ms=agent_silence_ms,
        num_agent_turns=num_agent_turns,
    )
    return ScenarioRunner(
        reco_client=reco,
        receiver=receiver,
        turn_detector=vad,
        audio_generator=audio_gen,
    )


@pytest.fixture
def mock_runner() -> ScenarioRunner:
    return _build_mock_runner()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_mock(example_scenario: TestScenario) -> None:
    """Run complete scenario through all components in mock mode.

    1. ScenarioRunner runs with all mocks
    2. Evaluator grades results (Tier 1 only)
    3. Reporter generates JSON
    4. No crashes, all fields populated, report is valid JSON
    """
    runner = _build_mock_runner()

    # Run scenario
    result = await runner.run_scenario(example_scenario)
    await runner.reco_client.close()

    # Basic checks
    assert result.scenario_id == "booking_happy_path"
    assert result.test_mode == "scripted"
    assert len(result.steps) == len(example_scenario.steps)
    assert result.error is None

    # Evaluate (Tier 1 only -- no API keys)
    evaluator = Evaluator(tier1_enabled=True, tier2_enabled=False, tier3_enabled=False)
    evaluated = await evaluator.evaluate_scenario(example_scenario, result)

    # Overall pass/fail should be set
    assert evaluated.overall_passed is not None

    # Each step should have evaluations
    # Tier 1 metrics get attached to step 0
    step0_evals = evaluated.steps[0].evaluations
    assert "call_completed" in step0_evals
    assert "turn_count_deviation" in step0_evals

    # Generate report
    reporter = Reporter(output_dir=tempfile.mkdtemp())
    report_data = reporter.generate_json_report(evaluated)

    # Validate JSON structure
    assert report_data["scenario_id"] == "booking_happy_path"
    assert "steps" in report_data
    assert "summary" in report_data
    assert "block_issues" in report_data
    assert report_data["overall_passed"] is not None

    # Ensure it serializes cleanly
    json_str = json.dumps(report_data, default=str)
    parsed_back = json.loads(json_str)
    assert parsed_back["scenario_id"] == "booking_happy_path"


@pytest.mark.asyncio
async def test_full_pipeline_produces_valid_json_report(
    example_scenario: TestScenario,
) -> None:
    """Run pipeline and verify JSON report file can be parsed and contains
    required fields: scenario_id, steps, evaluations, block_issues."""
    runner = _build_mock_runner()
    result = await runner.run_scenario(example_scenario)
    await runner.reco_client.close()

    evaluator = Evaluator(tier1_enabled=True, tier2_enabled=False, tier3_enabled=False)
    result = await evaluator.evaluate_scenario(example_scenario, result)

    with tempfile.TemporaryDirectory() as tmpdir:
        reporter = Reporter(output_dir=tmpdir)
        filepath = reporter.save_report(result, result.scenario_id)

        # Read and parse the saved file
        with open(filepath) as f:
            saved = json.load(f)

        assert saved["scenario_id"] == "booking_happy_path"
        assert isinstance(saved["steps"], list)
        assert len(saved["steps"]) == 4
        assert "evaluations" in saved["steps"][0]
        assert "block_issues" in saved
        assert "summary" in saved
        assert saved["summary"]["total_steps"] == 4


@pytest.mark.asyncio
async def test_mock_mode_requires_no_env_vars() -> None:
    """Verify mock mode works with no .env file at all.

    Temporarily clear relevant env vars and confirm pipeline runs.
    """
    env_keys = [
        "RECO_API_URL", "RECO_API_TOKEN", "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "CARTESIA_API_KEY",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    ]
    saved = {k: os.environ.pop(k, None) for k in env_keys}

    try:
        from models.scenario import TestScenario, TestStep, ScenarioChecks

        scenario = TestScenario(
            scenario_id="env_test",
            mode="scripted",
            steps=[
                TestStep(step=1, expected_block="greeting"),
                TestStep(step=2, user_input="test", expected_block="block_a"),
            ],
            description="No env vars test",
        )

        runner = _build_mock_runner()
        result = await runner.run_scenario(scenario)
        await runner.reco_client.close()

        assert result.error is None
        assert len(result.steps) == 2
    finally:
        # Restore env vars
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


@pytest.mark.asyncio
async def test_evaluator_sets_overall_pass_fail(
    example_scenario: TestScenario,
) -> None:
    """Evaluator correctly sets overall_passed and per-step passed."""
    runner = _build_mock_runner()
    result = await runner.run_scenario(example_scenario)
    await runner.reco_client.close()

    evaluator = Evaluator(tier1_enabled=True, tier2_enabled=False, tier3_enabled=False)
    result = await evaluator.evaluate_scenario(example_scenario, result)

    for step in result.steps:
        assert step.passed is not None, f"Step {step.step_number} passed is None"

    assert result.overall_passed is not None


@pytest.mark.asyncio
async def test_reporter_console_output_no_crash(
    example_scenario: TestScenario,
) -> None:
    """Reporter.print_summary and print_detailed don't crash."""
    runner = _build_mock_runner()
    result = await runner.run_scenario(example_scenario)
    await runner.reco_client.close()

    evaluator = Evaluator(tier1_enabled=True, tier2_enabled=False, tier3_enabled=False)
    result = await evaluator.evaluate_scenario(example_scenario, result)

    reporter = Reporter()
    # These should not raise
    reporter.print_summary(result)
    reporter.print_detailed(result)


@pytest.mark.asyncio
async def test_scenario_with_call_timeout() -> None:
    """If receiver never returns a call, result has error but no crash."""
    from models.scenario import TestScenario, TestStep

    scenario = TestScenario(
        scenario_id="timeout_test",
        mode="scripted",
        steps=[TestStep(step=1, expected_block="greeting")],
    )

    reco = RecoClient(base_url="http://localhost:3010", token="fake", mock=True)
    audio_gen = AudioGenerator(tts_provider="mock")
    vad = TurnDetector(
        silence_threshold_ms=1500,
        min_speech_ms=300,
        vad_model=_mock_vad_model(),
    )

    # Use a receiver that times out immediately
    class TimeoutReceiver(MockReceiver):
        async def wait_for_call(self, timeout: float = 30) -> None:
            raise TimeoutError("No call arrived")

    receiver = TimeoutReceiver()
    runner = ScenarioRunner(
        reco_client=reco,
        receiver=receiver,
        turn_detector=vad,
        audio_generator=audio_gen,
        call_wait_timeout=0.1,
    )

    result = await runner.run_scenario(scenario)
    await reco.close()

    assert result.error is not None
    assert "timeout" in result.error.lower() or "Call timeout" in result.error
    # No crash -- we got a result back
    assert result.scenario_id == "timeout_test"
