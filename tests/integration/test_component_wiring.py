"""Integration: Verify all component interfaces are compatible.

Checks that output types of one component match the input expectations of the next.
"""

from __future__ import annotations

import asyncio
import inspect
import struct

import pytest

from cli import _mock_vad_model
from core.audio_gen import AudioGenerator, PreparedAudio, TWILIO_CHUNK_SIZE
from core.audio_utils import mulaw_to_pcm16, pcm16_to_mulaw, resample_8k_to_16k
from core.evaluator import Evaluator
from core.reporter import Reporter
from core.scenario_runner import ScenarioRunner
from core.tier1_metrics import run_all_tier1
from core.vad import TurnDetector, TurnEvent
from models.result import EvalResult, ScenarioResult, StepResult
from models.scenario import TestScenario, TestStep, ScenarioChecks
from receivers.base import CallReceiver, ActiveCall
from receivers.mock_receiver import MockReceiver
from reco.client import RecoClient, CallStartResult


# ---------------------------------------------------------------------------
# Audio format compatibility
# ---------------------------------------------------------------------------


def test_audio_generator_output_produces_160_byte_chunks() -> None:
    """AudioGenerator.chunk_for_twilio produces exactly 160-byte mulaw chunks."""
    gen = AudioGenerator(tts_provider="mock")
    pcm = gen._generate_mock_audio("テスト")
    mulaw = gen.convert_to_twilio(pcm, 8000)
    chunks = gen.chunk_for_twilio(mulaw)

    assert len(chunks) > 0
    for chunk in chunks:
        assert len(chunk) == TWILIO_CHUNK_SIZE, (
            f"Expected {TWILIO_CHUNK_SIZE} bytes, got {len(chunk)}"
        )


@pytest.mark.asyncio
async def test_audio_generator_prepare_returns_prepared_audio() -> None:
    """prepare_scenario_audio returns list[PreparedAudio] with mulaw_chunks."""
    scenario = TestScenario(
        scenario_id="wire_test",
        mode="scripted",
        steps=[
            TestStep(step=1, user_input="こんにちは", expected_block="greeting"),
        ],
    )
    gen = AudioGenerator(tts_provider="mock")
    prepared = await gen.prepare_scenario_audio(scenario)

    assert len(prepared) == 1
    assert isinstance(prepared[0], PreparedAudio)
    assert prepared[0].step == 1
    assert len(prepared[0].mulaw_chunks) > 0
    assert all(len(c) == TWILIO_CHUNK_SIZE for c in prepared[0].mulaw_chunks)


def test_turn_detector_accepts_mulaw_bytes() -> None:
    """TurnDetector.feed_audio accepts mulaw bytes (160 bytes) without error."""
    vad = TurnDetector(
        silence_threshold_ms=1500,
        min_speech_ms=300,
        vad_model=_mock_vad_model(),
    )
    # 160 bytes of silence
    chunk = b"\xff" * 160
    result = vad.feed_audio(chunk, 0.0)
    # Should return None (silence, no event) or a TurnEvent
    assert result is None or isinstance(result, TurnEvent)


def test_mulaw_to_pcm16_roundtrip_preserves_audio() -> None:
    """PCM16 -> mulaw -> PCM16 roundtrip preserves audio quality.

    mulaw is lossy, so we check similarity not exact equality.
    """
    num_samples = 160
    # Generate a simple sine wave
    import math
    samples = [int(8000 * math.sin(2 * math.pi * 440 * i / 8000)) for i in range(num_samples)]
    pcm_original = struct.pack(f"<{num_samples}h", *samples)

    mulaw = pcm16_to_mulaw(pcm_original)
    pcm_roundtrip = mulaw_to_pcm16(mulaw)

    # Decode both
    orig_samples = struct.unpack(f"<{num_samples}h", pcm_original)
    rt_samples = struct.unpack(f"<{num_samples}h", pcm_roundtrip)

    # mulaw is lossy but should be close -- check RMS error is small
    rms_error = (sum((a - b) ** 2 for a, b in zip(orig_samples, rt_samples)) / num_samples) ** 0.5
    assert rms_error < 500, f"RMS error too high: {rms_error}"


def test_resample_8k_to_16k_doubles_samples() -> None:
    """Resampling 8kHz to 16kHz should roughly double sample count."""
    num_samples = 160  # 20ms at 8kHz
    pcm_8k = struct.pack(f"<{num_samples}h", *([0] * num_samples))
    pcm_16k = resample_8k_to_16k(pcm_8k)

    out_samples = len(pcm_16k) // 2
    assert out_samples == num_samples * 2, (
        f"Expected {num_samples * 2} samples, got {out_samples}"
    )


# ---------------------------------------------------------------------------
# Runner <-> Evaluator interface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_runner_result_matches_evaluator_input() -> None:
    """ScenarioRunner returns ScenarioResult. Evaluator.evaluate_scenario accepts it."""
    scenario = TestScenario(
        scenario_id="wire_test",
        mode="scripted",
        steps=[
            TestStep(step=1, expected_block="greeting"),
            TestStep(step=2, user_input="テスト", expected_block="block_a"),
        ],
    )

    reco = RecoClient(base_url="http://localhost:3010", token="fake", mock=True)
    audio_gen = AudioGenerator(tts_provider="mock")
    vad = TurnDetector(
        silence_threshold_ms=1500,
        min_speech_ms=300,
        vad_model=_mock_vad_model(),
    )
    receiver = MockReceiver(agent_speech_ms=500, agent_silence_ms=2000)

    runner = ScenarioRunner(
        reco_client=reco,
        receiver=receiver,
        turn_detector=vad,
        audio_generator=audio_gen,
    )

    result = await runner.run_scenario(scenario)
    await reco.close()

    # Verify result is a ScenarioResult
    assert isinstance(result, ScenarioResult)
    assert len(result.steps) > 0
    for step in result.steps:
        assert isinstance(step, StepResult)

    # Evaluator should accept this result
    evaluator = Evaluator(tier1_enabled=True, tier2_enabled=False, tier3_enabled=False)
    evaluated = await evaluator.evaluate_scenario(scenario, result)

    assert isinstance(evaluated, ScenarioResult)
    assert evaluated.overall_passed is not None


def test_evaluator_result_matches_reporter_input() -> None:
    """Reporter accepts ScenarioResult with populated evaluations."""
    result = ScenarioResult(
        scenario_id="report_test",
        test_mode="scripted",
        duration_s=5.0,
        overall_passed=True,
    )
    step = StepResult(
        step_number=1,
        user_input_text="test",
        agent_response_text="response",
        expected_block="block_a",
        passed=True,
    )
    step.evaluations["call_completed"] = EvalResult(
        metric="call_completed", passed=True, score=1.0, reasoning="OK"
    )
    result.steps.append(step)

    reporter = Reporter()
    report = reporter.generate_json_report(result)

    assert report["scenario_id"] == "report_test"
    assert report["overall_passed"] is True
    assert len(report["steps"]) == 1
    assert "call_completed" in report["steps"][0]["evaluations"]


def test_reco_client_start_call_returns_correct_type() -> None:
    """RecoClient.start_call returns CallStartResult with call_id and conversation_id."""
    reco = RecoClient(base_url="http://localhost:3010", token="fake", mock=True)

    result = asyncio.get_event_loop().run_until_complete(
        reco.start_call(
            phone="+819012345678",
            customer_id="test",
            flow_path="test/flow.yaml",
        )
    )
    asyncio.get_event_loop().run_until_complete(reco.close())

    assert isinstance(result, CallStartResult)
    assert isinstance(result.call_id, str)
    assert isinstance(result.conversation_id, int)


# ---------------------------------------------------------------------------
# Mock receiver <-> runner compatibility
# ---------------------------------------------------------------------------


def test_mock_receiver_implements_call_receiver() -> None:
    """MockReceiver properly implements the CallReceiver interface."""
    assert issubclass(MockReceiver, CallReceiver)

    # Check all abstract methods are implemented
    for method_name in ("wait_for_call", "get_audio_chunk", "send_audio", "clear_audio", "hangup"):
        assert hasattr(MockReceiver, method_name), f"Missing method: {method_name}"
        method = getattr(MockReceiver, method_name)
        assert callable(method), f"{method_name} is not callable"


def test_tier1_metrics_accept_scenario_result() -> None:
    """run_all_tier1 accepts (ScenarioResult, TestScenario) and returns dict[str, EvalResult]."""
    scenario = TestScenario(
        scenario_id="t1_test",
        mode="scripted",
        steps=[TestStep(step=1, expected_block="greeting")],
    )
    result = ScenarioResult(
        scenario_id="t1_test",
        test_mode="scripted",
    )
    result.steps.append(StepResult(step_number=1, expected_block="greeting"))

    evals = run_all_tier1(result, scenario)

    assert isinstance(evals, dict)
    for key, val in evals.items():
        assert isinstance(key, str)
        assert isinstance(val, EvalResult)

    # Expected metrics
    expected_metrics = {"call_completed", "response_latency", "repetition_detected",
                        "silence_or_dead_air", "turn_count_deviation"}
    assert set(evals.keys()) == expected_metrics


# ---------------------------------------------------------------------------
# No circular imports
# ---------------------------------------------------------------------------


def test_no_circular_imports() -> None:
    """Importing all main modules should not cause circular import errors."""
    # These are all loaded at import time; if we reach here, no circular imports
    import config.settings
    import core.audio_cache
    import core.audio_gen
    import core.audio_utils
    import core.evaluator
    import core.reporter
    import core.scenario_runner
    import core.tier1_metrics
    import core.vad
    import models.result
    import models.scenario
    import receivers.base
    import receivers.mock_receiver
    import receivers.twilio_receiver
    import reco.client
    import reco.mock_data
    import cli
