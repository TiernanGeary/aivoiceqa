"""Phase 1 tests: models, config, scenario loading."""

from pathlib import Path

from models.scenario import TestScenario, TestStep, ScenarioChecks, VadConfig
from models.result import StepResult, ScenarioResult, EvalResult


class TestScenarioLoading:
    def test_load_example_scenario(self, example_scenario):
        assert example_scenario.scenario_id == "booking_happy_path"
        assert example_scenario.mode == "scripted"
        assert len(example_scenario.steps) == 4

    def test_scenario_steps_parsed(self, example_scenario):
        step1 = example_scenario.steps[0]
        assert step1.step == 1
        assert step1.user_input is None  # Agent speaks first
        assert step1.expected_block == "greeting"

        step2 = example_scenario.steps[1]
        assert step2.user_input == "はい、予約をお願いします"
        assert step2.expected_block == "ask_date"

    def test_scenario_checks_parsed(self, example_scenario):
        step2 = example_scenario.steps[1]
        assert step2.checks.factual is not None
        assert step2.checks.keigo_level == "teineigo"
        assert "日時" in step2.checks.must_contain_meaning

    def test_scenario_vad_config(self, example_scenario):
        assert example_scenario.vad.silence_threshold_ms == 1500

    def test_scenario_expected_turns(self, example_scenario):
        assert example_scenario.expected_turns.min == 3
        assert example_scenario.expected_turns.max == 8

    def test_scenario_expected_duration(self, example_scenario):
        assert example_scenario.expected_duration.min_seconds == 30
        assert example_scenario.expected_duration.max_seconds == 180

    def test_step_with_audio_file(self):
        step = TestStep(
            step=1,
            user_audio="audio/test.wav",
            user_input="text reference",
            expected_block="greeting",
        )
        assert step.user_audio == "audio/test.wav"
        assert step.user_input == "text reference"

    def test_step_no_input(self):
        step = TestStep(step=1, expected_block="greeting")
        assert step.user_input is None
        assert step.user_audio is None


class TestResultModels:
    def test_eval_result(self):
        result = EvalResult(
            metric="factual_accuracy",
            passed=True,
            score=0.95,
            reasoning="Response matches expected content",
        )
        assert result.passed
        assert result.score == 0.95

    def test_step_result(self):
        result = StepResult(
            step_number=1,
            agent_response_text="お電話ありがとうございます",
            expected_block="greeting",
            latency_ms=1200.0,
        )
        assert result.step_number == 1
        assert result.passed is None  # Not evaluated yet

    def test_scenario_result(self):
        result = ScenarioResult(
            scenario_id="test",
            test_mode="scripted",
        )
        assert result.overall_passed is None
        assert result.steps == []
        assert result.error is None


class TestConfig:
    def test_config_imports(self):
        from config import settings
        assert hasattr(settings, "RECO_API_URL")
        assert hasattr(settings, "QA_SERVER_PORT")
        assert hasattr(settings, "RECO_MOCK_MODE")

    def test_config_defaults(self):
        from config import settings
        assert settings.QA_SERVER_PORT == 8050
        assert settings.LATENCY_P95_THRESHOLD_MS == 3000.0
