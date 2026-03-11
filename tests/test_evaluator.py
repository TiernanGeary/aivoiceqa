"""Tests for the evaluation pipeline — Tier 1, Tier 2, Tier 3, and Evaluator."""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.evaluator import Evaluator
from core.tier1_metrics import (
    check_call_completed,
    check_dead_air,
    check_repetition,
    check_response_latency,
    check_turn_count,
    run_all_tier1,
)
from core.tier2_metrics import Tier2Evaluator
from core.tier3_metrics import Tier3Evaluator, _character_error_rate
from models.result import EvalResult, ScenarioResult, StepResult
from models.scenario import ScenarioChecks, TestScenario, TestStep, TurnRange


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scenario(
    steps: int = 3,
    expected_min: int = 2,
    expected_max: int = 5,
) -> TestScenario:
    return TestScenario(
        scenario_id="test-scenario",
        steps=[
            TestStep(
                step=i + 1,
                expected_block=f"block_{i + 1}",
                user_input=f"user input {i + 1}",
                checks=ScenarioChecks(
                    factual="Confirm the appointment",
                    keigo_level="teineigo",
                    must_contain_meaning=["予約", "確認"],
                ),
            )
            for i in range(steps)
        ],
        expected_turns=TurnRange(min=expected_min, max=expected_max),
    )


def _make_result(
    steps: int = 3,
    latency_ms: float = 500.0,
    error: str | None = None,
    agent_texts: list[str] | None = None,
) -> ScenarioResult:
    if agent_texts is None:
        # Use distinct texts so repetition detection doesn't fire
        defaults = [
            "はい、予約を確認いたします。",
            "火曜日の午後2時ですね、承知しました。",
            "他にご質問はございますか？",
            "ありがとうございます。失礼いたします。",
            "少々お待ちください。",
        ]
        agent_texts = [defaults[i % len(defaults)] for i in range(steps)]
    return ScenarioResult(
        scenario_id="test-scenario",
        test_mode="scripted",
        steps=[
            StepResult(
                step_number=i + 1,
                user_input_text=f"user input {i + 1}",
                agent_response_text=agent_texts[i] if i < len(agent_texts) else "",
                latency_ms=latency_ms,
            )
            for i in range(steps)
        ],
        error=error,
    )


def _mock_claude_response(data: dict) -> MagicMock:
    """Build a mock Anthropic messages.create response."""
    content_block = MagicMock()
    content_block.text = json.dumps(data)
    response = MagicMock()
    response.content = [content_block]
    return response


# ---------------------------------------------------------------------------
# Tier 1: Algorithmic metrics
# ---------------------------------------------------------------------------

class TestTier1CallCompleted:
    def test_pass_no_error(self):
        result = _make_result()
        ev = check_call_completed(result)
        assert ev.passed is True
        assert ev.metric == "call_completed"

    def test_fail_with_error(self):
        result = _make_result(error="Connection timeout")
        ev = check_call_completed(result)
        assert ev.passed is False
        assert "Connection timeout" in ev.reasoning


class TestTier1ResponseLatency:
    def test_pass_low_latency(self):
        result = _make_result(latency_ms=500)
        ev = check_response_latency(result, p95_threshold_ms=3000)
        assert ev.passed is True
        assert ev.details["p95_ms"] <= 3000

    def test_fail_high_latency(self):
        result = _make_result(latency_ms=5000)
        ev = check_response_latency(result, p95_threshold_ms=3000)
        assert ev.passed is False

    def test_no_latency_data(self):
        result = ScenarioResult(
            scenario_id="test",
            test_mode="scripted",
            steps=[StepResult(step_number=1, latency_ms=None)],
        )
        ev = check_response_latency(result)
        assert ev.passed is True
        assert "No latency data" in ev.reasoning


class TestTier1Repetition:
    def test_no_repetition(self):
        result = _make_result(agent_texts=["A", "B", "C"])
        ev = check_repetition(result.steps)
        assert ev.passed is True
        assert ev.details["type"] is None

    def test_stuck_loop(self):
        result = _make_result(
            steps=4,
            agent_texts=["same response", "same response", "same response", "same response"],
        )
        ev = check_repetition(result.steps)
        assert ev.passed is False
        assert ev.details["type"] == "stuck_loop"

    def test_stt_reask(self):
        result = _make_result(
            steps=3,
            agent_texts=[
                "すみません、もう一度お願いします",
                "Different response here",
                "すみません、聞き取れません",
            ],
        )
        ev = check_repetition(result.steps)
        assert ev.passed is False
        assert ev.details["type"] == "stt_reask"

    def test_single_step(self):
        result = _make_result(steps=1, agent_texts=["only one"])
        ev = check_repetition(result.steps)
        assert ev.passed is True

    def test_clarification(self):
        # Responses that are similar (overlapping words) but not identical
        result = _make_result(
            steps=4,
            agent_texts=[
                "日時はいつがよろしいですか",
                "日時はいつがよろしいですか",
                "Different entirely",
                "日時はいつがよろしいですか",
            ],
        )
        ev = check_repetition(result.steps)
        # 2 consecutive identical is not stuck_loop (needs 3+)
        # But there are multiple similar pairs -> clarification
        assert ev.details["type"] == "clarification"


class TestTier1DeadAir:
    def test_pass_no_dead_air(self):
        result = _make_result(latency_ms=1000)
        ev = check_dead_air(result.steps, threshold_ms=5000)
        assert ev.passed is True

    def test_fail_dead_air(self):
        result = _make_result(latency_ms=7000)
        ev = check_dead_air(result.steps, threshold_ms=5000)
        assert ev.passed is False
        assert len(ev.details["flagged_steps"]) > 0


class TestTier1TurnCount:
    def test_within_range(self):
        scenario = _make_scenario(steps=3, expected_min=2, expected_max=5)
        result = _make_result(steps=3)
        ev = check_turn_count(result, scenario)
        assert ev.passed is True

    def test_too_few_turns(self):
        scenario = _make_scenario(steps=3, expected_min=5, expected_max=10)
        result = _make_result(steps=3)
        ev = check_turn_count(result, scenario)
        assert ev.passed is False

    def test_too_many_turns(self):
        scenario = _make_scenario(steps=3, expected_min=1, expected_max=2)
        result = _make_result(steps=3)
        ev = check_turn_count(result, scenario)
        assert ev.passed is False


class TestTier1RunAll:
    def test_run_all_returns_five_metrics(self):
        scenario = _make_scenario()
        result = _make_result()
        evals = run_all_tier1(result, scenario)
        assert len(evals) == 5
        expected_metrics = {
            "call_completed",
            "response_latency",
            "repetition_detected",
            "silence_or_dead_air",
            "turn_count_deviation",
        }
        assert set(evals.keys()) == expected_metrics


# ---------------------------------------------------------------------------
# Tier 2: LLM-based metrics
# ---------------------------------------------------------------------------

class TestTier2Evaluator:
    @pytest.fixture()
    def mock_client(self):
        client = MagicMock()
        client.messages = MagicMock()
        client.messages.create = AsyncMock()
        return client

    @pytest.fixture()
    def evaluator(self, mock_client):
        return Tier2Evaluator(mock_client)

    @pytest.mark.asyncio
    async def test_evaluate_step_all_pass(self, evaluator, mock_client):
        mock_data = {
            "block_transition_correct": {"pass": True, "inferred_block": "block_1", "reasoning": "Correct"},
            "factual_accuracy": {"pass": True, "reasoning": "Accurate"},
            "keigo_level_correct": {"pass": True, "detected_level": "teineigo", "reasoning": "Correct"},
            "conversation_natural": {"pass": True, "score": 4, "reasoning": "Natural"},
            "hallucination_detected": {"pass": True, "hallucinated_content": None, "reasoning": "None"},
            "must_contain_meaning": {"pass": True, "missing_meanings": [], "reasoning": "All present"},
        }
        mock_client.messages.create.return_value = _mock_claude_response(mock_data)

        step = TestStep(
            step=1,
            expected_block="block_1",
            user_input="test input",
            checks=ScenarioChecks(factual="Confirm", keigo_level="teineigo", must_contain_meaning=["予約"]),
        )
        step_result = StepResult(step_number=1, agent_response_text="はい、予約を確認いたします。")

        results = await evaluator.evaluate_step(step, step_result, "flow yaml", "transcript")

        assert len(results) == 6
        assert all(r.passed for r in results.values())
        mock_client.messages.create.assert_called_once()  # Single API call

    @pytest.mark.asyncio
    async def test_evaluate_step_some_fail(self, evaluator, mock_client):
        mock_data = {
            "block_transition_correct": {"pass": False, "inferred_block": "block_2", "reasoning": "Wrong block"},
            "factual_accuracy": {"pass": True, "reasoning": "OK"},
            "keigo_level_correct": {"pass": False, "detected_level": "casual", "reasoning": "Too casual"},
            "conversation_natural": {"pass": True, "score": 3, "reasoning": "OK"},
            "hallucination_detected": {"pass": True, "hallucinated_content": None, "reasoning": "None"},
            "must_contain_meaning": {"pass": True, "missing_meanings": [], "reasoning": "OK"},
        }
        mock_client.messages.create.return_value = _mock_claude_response(mock_data)

        step = TestStep(step=1, expected_block="block_1")
        step_result = StepResult(step_number=1, agent_response_text="Agent said something")

        results = await evaluator.evaluate_step(step, step_result, "", "")

        assert results["block_transition_correct"].passed is False
        assert results["keigo_level_correct"].passed is False
        assert results["factual_accuracy"].passed is True

    @pytest.mark.asyncio
    async def test_evaluate_step_no_response(self, evaluator, mock_client):
        step = TestStep(step=1, expected_block="block_1")
        step_result = StepResult(step_number=1, agent_response_text="")

        results = await evaluator.evaluate_step(step, step_result, "", "")

        assert len(results) == 6
        assert all(not r.passed for r in results.values())
        mock_client.messages.create.assert_not_called()

    def test_prompt_includes_context(self, evaluator):
        step = TestStep(
            step=1,
            expected_block="greeting_block",
            checks=ScenarioChecks(
                factual="Greet the customer",
                keigo_level="sonkeigo",
                must_contain_meaning=["ご予約", "確認"],
            ),
        )
        step_result = StepResult(step_number=1, agent_response_text="こんにちは")

        prompt = evaluator._build_evaluation_prompt(step, step_result, "flow yaml here", "transcript here")

        assert "greeting_block" in prompt
        assert "Greet the customer" in prompt
        assert "sonkeigo" in prompt
        assert "ご予約" in prompt
        assert "flow yaml here" in prompt
        assert "transcript here" in prompt
        assert "こんにちは" in prompt

    @pytest.mark.asyncio
    async def test_infer_block(self, evaluator, mock_client):
        content_block = MagicMock()
        content_block.text = "block_greeting"
        response = MagicMock()
        response.content = [content_block]
        mock_client.messages.create.return_value = response

        inferred, matches = await evaluator.infer_block_from_response(
            "こんにちは", "flow yaml", "block_greeting"
        )
        assert inferred == "block_greeting"
        assert matches is True

    @pytest.mark.asyncio
    async def test_parse_response_with_markdown_fences(self, evaluator, mock_client):
        """Claude sometimes wraps JSON in markdown code fences."""
        wrapped = '```json\n{"block_transition_correct": {"pass": true, "inferred_block": "b1", "reasoning": "ok"}}\n```'
        parsed = evaluator._parse_response(wrapped)
        assert parsed["block_transition_correct"]["pass"] is True


# ---------------------------------------------------------------------------
# Tier 3: Audio metrics
# ---------------------------------------------------------------------------

class TestCER:
    def test_identical(self):
        assert _character_error_rate("こんにちは", "こんにちは") == 0.0

    def test_completely_different(self):
        cer = _character_error_rate("abc", "xyz")
        assert cer == 1.0

    def test_empty_reference(self):
        assert _character_error_rate("", "") == 0.0
        assert _character_error_rate("", "abc") == 1.0

    def test_partial_match(self):
        cer = _character_error_rate("こんにちは", "こんにちわ")
        assert 0.0 < cer < 1.0


class TestTier3Evaluator:
    @pytest.fixture()
    def mock_openai(self):
        client = MagicMock()
        client.audio = MagicMock()
        client.audio.transcriptions = MagicMock()
        client.audio.transcriptions.create = AsyncMock()
        return client

    @pytest.fixture()
    def evaluator(self, mock_openai):
        return Tier3Evaluator(mock_openai)

    @pytest.mark.asyncio
    async def test_transcribe_audio(self, evaluator, mock_openai):
        transcript = MagicMock()
        transcript.text = "こんにちは"
        mock_openai.audio.transcriptions.create.return_value = transcript

        text = await evaluator.transcribe_audio(b"fake audio bytes")
        assert text == "こんにちは"

    @pytest.mark.asyncio
    async def test_tts_pronunciation_pass(self, evaluator, mock_openai):
        transcript = MagicMock()
        transcript.text = "こんにちは"
        mock_openai.audio.transcriptions.create.return_value = transcript

        ev = await evaluator.evaluate_tts_pronunciation("こんにちは", b"audio")
        assert ev.passed is True
        assert ev.metric == "tts_pronunciation"
        assert ev.details["cer"] == 0.0

    @pytest.mark.asyncio
    async def test_tts_pronunciation_fail(self, evaluator, mock_openai):
        transcript = MagicMock()
        transcript.text = "xxxxxxx"  # completely wrong
        mock_openai.audio.transcriptions.create.return_value = transcript

        ev = await evaluator.evaluate_tts_pronunciation("こんにちは", b"audio")
        assert ev.passed is False

    @pytest.mark.asyncio
    async def test_stt_accuracy(self, evaluator):
        ev = await evaluator.evaluate_stt_accuracy(
            "火曜日の2時にお願いします", "火曜日の2時ですね、承知しました"
        )
        assert ev.passed is True
        assert ev.metric == "stt_accuracy"


# ---------------------------------------------------------------------------
# Main Evaluator
# ---------------------------------------------------------------------------

class TestEvaluator:
    @pytest.fixture()
    def mock_anthropic(self):
        client = MagicMock()
        client.messages = MagicMock()
        client.messages.create = AsyncMock()
        mock_data = {
            "block_transition_correct": {"pass": True, "inferred_block": "block_1", "reasoning": "OK"},
            "factual_accuracy": {"pass": True, "reasoning": "OK"},
            "keigo_level_correct": {"pass": True, "detected_level": "teineigo", "reasoning": "OK"},
            "conversation_natural": {"pass": True, "score": 4, "reasoning": "OK"},
            "hallucination_detected": {"pass": True, "hallucinated_content": None, "reasoning": "OK"},
            "must_contain_meaning": {"pass": True, "missing_meanings": [], "reasoning": "OK"},
        }
        client.messages.create.return_value = _mock_claude_response(mock_data)
        return client

    @pytest.mark.asyncio
    async def test_tier1_only(self):
        evaluator = Evaluator(tier1_enabled=True, tier2_enabled=False, tier3_enabled=False)
        scenario = _make_scenario()
        result = _make_result()

        evaluated = await evaluator.evaluate_scenario(scenario, result)

        assert evaluated.overall_passed is True
        # Tier 1 metrics on first step
        assert "call_completed" in evaluated.steps[0].evaluations

    @pytest.mark.asyncio
    async def test_tier1_and_tier2(self, mock_anthropic):
        evaluator = Evaluator(
            tier1_enabled=True,
            tier2_enabled=True,
            tier3_enabled=False,
            anthropic_client=mock_anthropic,
        )
        scenario = _make_scenario()
        result = _make_result()

        evaluated = await evaluator.evaluate_scenario(scenario, result, flow_yaml="flow yaml")

        # Should have tier1 + tier2 metrics
        assert "call_completed" in evaluated.steps[0].evaluations
        assert "factual_accuracy" in evaluated.steps[0].evaluations
        assert evaluated.overall_passed is True

    @pytest.mark.asyncio
    async def test_overall_fail_propagates(self):
        evaluator = Evaluator(tier1_enabled=True, tier2_enabled=False, tier3_enabled=False)
        scenario = _make_scenario(steps=3, expected_min=10, expected_max=20)
        result = _make_result(steps=3)

        evaluated = await evaluator.evaluate_scenario(scenario, result)

        # Turn count deviation should fail
        assert evaluated.steps[0].evaluations["turn_count_deviation"].passed is False
        assert evaluated.overall_passed is False

    @pytest.mark.asyncio
    async def test_with_error_scenario(self):
        evaluator = Evaluator(tier1_enabled=True, tier2_enabled=False, tier3_enabled=False)
        scenario = _make_scenario()
        result = _make_result(error="Call dropped")

        evaluated = await evaluator.evaluate_scenario(scenario, result)

        assert evaluated.steps[0].evaluations["call_completed"].passed is False
        assert evaluated.overall_passed is False

    @pytest.mark.asyncio
    async def test_no_tiers_enabled(self):
        evaluator = Evaluator(tier1_enabled=False, tier2_enabled=False, tier3_enabled=False)
        scenario = _make_scenario()
        result = _make_result()

        evaluated = await evaluator.evaluate_scenario(scenario, result)

        # Nothing to evaluate — all steps pass by default
        assert evaluated.overall_passed is True

    @pytest.mark.asyncio
    async def test_tier3_with_audio(self):
        mock_openai = MagicMock()
        mock_openai.audio = MagicMock()
        mock_openai.audio.transcriptions = MagicMock()
        transcript = MagicMock()
        transcript.text = "テスト"
        mock_openai.audio.transcriptions.create = AsyncMock(return_value=transcript)

        evaluator = Evaluator(
            tier1_enabled=False,
            tier2_enabled=False,
            tier3_enabled=True,
            openai_client=mock_openai,
        )
        scenario = _make_scenario(steps=1)
        result = ScenarioResult(
            scenario_id="test",
            test_mode="scripted",
            steps=[
                StepResult(
                    step_number=1,
                    user_input_text="テスト入力",
                    agent_response_text="テスト",
                    agent_audio=b"fake audio",
                ),
            ],
        )

        evaluated = await evaluator.evaluate_scenario(scenario, result)

        assert "tts_pronunciation" in evaluated.steps[0].evaluations
        assert "stt_accuracy" in evaluated.steps[0].evaluations


# ---------------------------------------------------------------------------
# EvalResult construction
# ---------------------------------------------------------------------------

class TestEvalResult:
    def test_basic_construction(self):
        ev = EvalResult(metric="test", passed=True)
        assert ev.metric == "test"
        assert ev.passed is True
        assert ev.score is None
        assert ev.reasoning is None
        assert ev.details is None

    def test_full_construction(self):
        ev = EvalResult(
            metric="test",
            passed=False,
            score=0.5,
            reasoning="Half right",
            details={"key": "value"},
        )
        assert ev.score == 0.5
        assert ev.details["key"] == "value"
