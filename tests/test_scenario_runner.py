"""Tests for ScenarioRunner, MockReceiver, and CLI."""

from __future__ import annotations

import asyncio
import argparse
import math
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.audio_gen import AudioGenerator, PreparedAudio
from core.scenario_runner import ScenarioRunner, ScenarioRunnerError
from core.vad import TurnDetector
from models.result import ScenarioResult, StepResult
from models.scenario import TestScenario, TestStep, VadConfig
from receivers.base import ActiveCall, CallReceiver
from receivers.mock_receiver import (
    MockReceiver,
    _generate_mulaw_silence,
    _generate_mulaw_speech,
)
from reco.client import RecoClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scenario(num_steps: int = 2, scenario_id: str = "test_scenario") -> TestScenario:
    """Build a simple TestScenario for testing."""
    steps = []
    for i in range(1, num_steps + 1):
        steps.append(TestStep(
            step=i,
            expected_block=f"block_{i}",
            user_input=f"ステップ{i}の応答" if i > 1 else None,
        ))
    return TestScenario(
        scenario_id=scenario_id,
        mode="scripted",
        flow_path="flow/flow.yaml",
        description="Test scenario",
        steps=steps,
    )


def _mock_vad_model():
    """VAD model that uses RMS energy to distinguish speech from silence.

    Handles both torch tensors (when torch is installed) and plain lists.
    The TurnDetector._run_vad normalizes torch tensors to [-1, 1] before
    passing to the model, so our threshold is in that range.
    """
    def vad_fn(samples, sample_rate):
        try:
            import torch
            if isinstance(samples, torch.Tensor):
                if samples.numel() == 0:
                    return 0.0
                rms = samples.pow(2).mean().sqrt().item()
                # Normalized range: threshold ~0.015 corresponds to ~500/32768
                return 0.9 if rms > 0.015 else 0.1
        except ImportError:
            pass

        if not samples:
            return 0.0
        if isinstance(samples, list):
            rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
        else:
            rms = 0.0
        return 0.9 if rms > 500 else 0.1
    return vad_fn


def _make_turn_detector() -> TurnDetector:
    """Create a TurnDetector with mock VAD model and fast thresholds."""
    return TurnDetector(
        silence_threshold_ms=200,   # Fast for tests
        min_speech_ms=40,           # 2 frames
        vad_model=_mock_vad_model(),
    )


def _make_reco_client() -> RecoClient:
    return RecoClient(base_url="http://localhost:3010", token="test", mock=True)


def _make_audio_generator() -> AudioGenerator:
    return AudioGenerator(tts_provider="mock")


# ---------------------------------------------------------------------------
# MockReceiver tests
# ---------------------------------------------------------------------------

class TestMockReceiver:

    @pytest.mark.asyncio
    async def test_wait_for_call_returns_active_call(self):
        receiver = MockReceiver(num_agent_turns=1, agent_speech_ms=100, agent_silence_ms=100)
        call = await receiver.wait_for_call(timeout=5)
        assert isinstance(call, ActiveCall)
        assert call.call_sid == "mock-call-sid"
        assert call.stream_sid == "mock-stream-sid"
        await receiver.hangup(call)

    @pytest.mark.asyncio
    async def test_get_audio_chunk_returns_data(self):
        receiver = MockReceiver(num_agent_turns=1, agent_speech_ms=100, agent_silence_ms=100)
        call = await receiver.wait_for_call()
        chunk = await receiver.get_audio_chunk(call)
        assert chunk is not None
        assert len(chunk) == 160  # mulaw frame size
        await receiver.hangup(call)

    @pytest.mark.asyncio
    async def test_send_audio_captures_data(self):
        receiver = MockReceiver(num_agent_turns=1, agent_speech_ms=100, agent_silence_ms=100)
        call = await receiver.wait_for_call()
        test_audio = b"\x80" * 320
        await receiver.send_audio(call, test_audio)
        assert len(receiver.sent_audio) == 1
        assert receiver.sent_audio[0] == test_audio
        await receiver.hangup(call)

    @pytest.mark.asyncio
    async def test_hangup_sets_flag(self):
        receiver = MockReceiver(num_agent_turns=1, agent_speech_ms=100, agent_silence_ms=100)
        call = await receiver.wait_for_call()
        assert not receiver.hungup
        await receiver.hangup(call)
        assert receiver.hungup

    @pytest.mark.asyncio
    async def test_generates_speech_and_silence(self):
        speech = _generate_mulaw_speech(duration_ms=100)
        silence = _generate_mulaw_silence(duration_ms=100)
        assert len(speech) > 0
        assert len(silence) > 0
        # Silence should be all 0xff
        assert silence == b"\xff" * len(silence)


# ---------------------------------------------------------------------------
# ScenarioRunner tests
# ---------------------------------------------------------------------------

class TestScenarioRunner:

    @pytest.mark.asyncio
    async def test_full_mock_scenario(self):
        """End-to-end: run a 2-step scenario entirely in mock mode."""
        scenario = _make_scenario(num_steps=2)
        receiver = MockReceiver(
            agent_speech_ms=200,
            agent_silence_ms=500,
            num_agent_turns=5,
        )
        runner = ScenarioRunner(
            reco_client=_make_reco_client(),
            receiver=receiver,
            turn_detector=_make_turn_detector(),
            audio_generator=_make_audio_generator(),
            step_timeout=10.0,
            call_wait_timeout=5.0,
        )

        result = await runner.run_scenario(scenario)

        assert isinstance(result, ScenarioResult)
        assert result.scenario_id == "test_scenario"
        assert result.test_mode == "scripted"
        assert result.call_id is not None
        assert result.conversation_id is not None
        assert result.duration_s > 0
        assert len(result.steps) == 2

        # Each step should have captured some agent audio
        for step_result in result.steps:
            assert isinstance(step_result, StepResult)
            assert step_result.error is None

        # Cleanup
        await runner.reco_client.close()

    @pytest.mark.asyncio
    async def test_call_timeout(self):
        """Scenario should record error when no call arrives."""
        scenario = _make_scenario(num_steps=1)

        # Use a receiver that never connects
        class NeverConnectReceiver(CallReceiver):
            async def wait_for_call(self, timeout: float = 30) -> ActiveCall:
                raise TimeoutError(f"No call within {timeout}s")
            async def get_audio_chunk(self, call): return None
            async def send_audio(self, call, audio): pass
            async def clear_audio(self, call): pass
            async def hangup(self, call): pass

        runner = ScenarioRunner(
            reco_client=_make_reco_client(),
            receiver=NeverConnectReceiver(),
            turn_detector=_make_turn_detector(),
            audio_generator=_make_audio_generator(),
            call_wait_timeout=0.1,
        )

        result = await runner.run_scenario(scenario)
        assert result.error is not None
        assert "timeout" in result.error.lower() or "call" in result.error.lower()
        assert len(result.steps) == 0
        await runner.reco_client.close()

    @pytest.mark.asyncio
    async def test_step_timeout(self):
        """A step that takes too long should record a timeout error."""
        scenario = _make_scenario(num_steps=1)

        # Receiver that never returns audio (simulates agent never responding)
        class SilentReceiver(CallReceiver):
            async def wait_for_call(self, timeout: float = 30) -> ActiveCall:
                return ActiveCall(
                    call_sid="test", stream_sid="test",
                    websocket=None, started_at=time.time(),
                )
            async def get_audio_chunk(self, call):
                await asyncio.sleep(100)  # Block forever
                return None
            async def send_audio(self, call, audio): pass
            async def clear_audio(self, call): pass
            async def hangup(self, call): pass

        runner = ScenarioRunner(
            reco_client=_make_reco_client(),
            receiver=SilentReceiver(),
            turn_detector=_make_turn_detector(),
            audio_generator=_make_audio_generator(),
            step_timeout=0.2,
            call_wait_timeout=5.0,
        )

        result = await runner.run_scenario(scenario)
        assert len(result.steps) == 1
        assert result.steps[0].error is not None
        assert "timeout" in result.steps[0].error.lower()
        await runner.reco_client.close()

    @pytest.mark.asyncio
    async def test_partial_results_on_disconnect(self):
        """If call disconnects mid-scenario, we should get partial results."""
        scenario = _make_scenario(num_steps=3)

        call_obj = ActiveCall(
            call_sid="test", stream_sid="test",
            websocket=None, started_at=time.time(),
        )

        class DisconnectReceiver(CallReceiver):
            """Feeds one turn of audio then disconnects."""
            def __init__(self):
                self._chunks_sent = 0
                self._speech = _generate_mulaw_speech(200)
                self._silence = _generate_mulaw_silence(500)
                self._all_audio = self._speech + self._silence
                self._pos = 0
                self._turn_count = 0

            async def wait_for_call(self, timeout: float = 30) -> ActiveCall:
                return call_obj

            async def get_audio_chunk(self, call):
                if self._turn_count >= 1:
                    # Disconnect after first turn
                    return None
                if self._pos >= len(self._all_audio):
                    self._turn_count += 1
                    self._pos = 0
                    if self._turn_count >= 1:
                        return None
                end = self._pos + 160
                chunk = self._all_audio[self._pos:end]
                if len(chunk) < 160:
                    chunk = chunk + b"\xff" * (160 - len(chunk))
                self._pos = end
                await asyncio.sleep(0.001)
                return chunk

            async def send_audio(self, call, audio): pass
            async def clear_audio(self, call): pass
            async def hangup(self, call): pass

        runner = ScenarioRunner(
            reco_client=_make_reco_client(),
            receiver=DisconnectReceiver(),
            turn_detector=_make_turn_detector(),
            audio_generator=_make_audio_generator(),
            step_timeout=5.0,
        )

        result = await runner.run_scenario(scenario)
        # Should have at least 1 step result (partial results)
        assert len(result.steps) >= 1
        await runner.reco_client.close()

    @pytest.mark.asyncio
    async def test_step_result_fields(self):
        """Verify StepResult contains expected fields."""
        scenario = _make_scenario(num_steps=1)
        receiver = MockReceiver(
            agent_speech_ms=200,
            agent_silence_ms=500,
            num_agent_turns=2,
        )
        runner = ScenarioRunner(
            reco_client=_make_reco_client(),
            receiver=receiver,
            turn_detector=_make_turn_detector(),
            audio_generator=_make_audio_generator(),
            step_timeout=10.0,
        )

        result = await runner.run_scenario(scenario)
        assert len(result.steps) == 1

        step = result.steps[0]
        assert step.step_number == 1
        assert step.expected_block == "block_1"
        # Step 1 has no user_input so user_input_text is None
        assert step.user_input_text is None
        assert step.evaluations == {}
        assert step.passed is None
        await runner.reco_client.close()

    @pytest.mark.asyncio
    async def test_reco_transcript_fetched(self):
        """Verify reco transcript is fetched after the run."""
        scenario = _make_scenario(num_steps=1)
        receiver = MockReceiver(
            agent_speech_ms=200,
            agent_silence_ms=500,
            num_agent_turns=2,
        )
        runner = ScenarioRunner(
            reco_client=_make_reco_client(),
            receiver=receiver,
            turn_detector=_make_turn_detector(),
            audio_generator=_make_audio_generator(),
            step_timeout=10.0,
        )

        result = await runner.run_scenario(scenario)
        # Mock reco client returns a transcript
        assert result.reco_transcript is not None
        assert len(result.reco_transcript) > 0
        await runner.reco_client.close()


# ---------------------------------------------------------------------------
# CLI argument parsing tests
# ---------------------------------------------------------------------------

class TestCLI:

    def test_run_with_scenario(self):
        from cli import main
        # Just test argument parsing, not execution
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        run_parser = subparsers.add_parser("run")
        run_parser.add_argument("--scenario", type=str)
        run_parser.add_argument("--scenario-dir", type=str)
        run_parser.add_argument("--mock", action="store_true")

        args = parser.parse_args(["run", "--scenario", "test.yaml", "--mock"])
        assert args.command == "run"
        assert args.scenario == "test.yaml"
        assert args.mock is True

    def test_run_with_scenario_dir(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        run_parser = subparsers.add_parser("run")
        run_parser.add_argument("--scenario", type=str)
        run_parser.add_argument("--scenario-dir", type=str)
        run_parser.add_argument("--mock", action="store_true")

        args = parser.parse_args(["run", "--scenario-dir", "scenarios/", "--mock"])
        assert args.command == "run"
        assert args.scenario_dir == "scenarios/"
        assert args.mock is True

    def test_run_without_mock(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        run_parser = subparsers.add_parser("run")
        run_parser.add_argument("--scenario", type=str)
        run_parser.add_argument("--scenario-dir", type=str)
        run_parser.add_argument("--mock", action="store_true")

        args = parser.parse_args(["run", "--scenario", "test.yaml"])
        assert args.mock is False

    def test_load_scenarios_from_file(self, tmp_path):
        """Test loading a scenario from a YAML file."""
        from cli import load_scenarios

        yaml_content = """
scenario_id: test_cli
mode: scripted
description: CLI test scenario
flow_path: flow/flow.yaml
steps:
  - step: 1
    expected_block: greeting
"""
        scenario_file = tmp_path / "test.yaml"
        scenario_file.write_text(yaml_content)

        scenarios = load_scenarios(str(scenario_file), None)
        assert len(scenarios) == 1
        assert scenarios[0].scenario_id == "test_cli"

    def test_load_scenarios_from_dir(self, tmp_path):
        """Test loading scenarios from a directory."""
        from cli import load_scenarios

        for name in ["a.yaml", "b.yaml"]:
            (tmp_path / name).write_text(f"""
scenario_id: {name.replace('.yaml', '')}
mode: scripted
steps:
  - step: 1
    expected_block: greeting
""")

        scenarios = load_scenarios(None, str(tmp_path))
        assert len(scenarios) == 2
        ids = {s.scenario_id for s in scenarios}
        assert ids == {"a", "b"}

    def test_build_runner_mock_mode(self):
        """Test that build_runner in mock mode creates proper components."""
        from cli import build_runner
        from receivers.mock_receiver import MockReceiver as MR

        runner = build_runner(mock=True)
        assert isinstance(runner, ScenarioRunner)
        assert runner.reco_client.mock is True
        assert isinstance(runner.receiver, MR)
        assert runner.audio_generator.tts_provider == "mock"


# ---------------------------------------------------------------------------
# Integration: CLI e2e with mock
# ---------------------------------------------------------------------------

class TestCLIIntegration:

    @pytest.mark.asyncio
    async def test_mock_e2e_via_run_command(self, tmp_path):
        """Full e2e: load scenario YAML, run in mock mode, get results."""
        from cli import run_command

        yaml_content = """
scenario_id: cli_e2e_test
mode: scripted
description: E2E CLI test
flow_path: flow/flow.yaml
steps:
  - step: 1
    expected_block: greeting
  - step: 2
    user_input: "はい"
    expected_block: ask_date
"""
        scenario_file = tmp_path / "e2e.yaml"
        scenario_file.write_text(yaml_content)

        args = argparse.Namespace(
            scenario=str(scenario_file),
            scenario_dir=None,
            mock=True,
        )

        exit_code = await run_command(args)
        assert exit_code == 0
