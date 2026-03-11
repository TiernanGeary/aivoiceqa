"""Scenario runner — orchestrates a full test scenario from start to finish.

Wires together: RecoClient, CallReceiver, TurnDetector, AudioGenerator.
Returns a ScenarioResult with timing data for each step.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from config import settings
from core.audio_gen import AudioGenerator, PreparedAudio
from core.vad import TurnDetector, TurnState
from models.result import ScenarioResult, StepResult
from models.scenario import TestScenario, TestStep
from receivers.base import ActiveCall, CallReceiver
from reco.client import RecoClient

logger = logging.getLogger(__name__)


class ScenarioRunnerError(Exception):
    """Raised when a scenario run fails at the orchestration level."""


class ScenarioRunner:
    """Orchestrates a full test scenario: trigger call, play audio, record results.

    All dependencies are injected via constructor for testability.
    """

    def __init__(
        self,
        reco_client: RecoClient,
        receiver: CallReceiver,
        turn_detector: TurnDetector,
        audio_generator: AudioGenerator,
        call_wait_timeout: float | None = None,
        step_timeout: float | None = None,
    ) -> None:
        self.reco_client = reco_client
        self.receiver = receiver
        self.turn_detector = turn_detector
        self.audio_generator = audio_generator
        self.call_wait_timeout = call_wait_timeout or settings.CALL_WAIT_TIMEOUT
        self.step_timeout = step_timeout or settings.SCENARIO_STEP_TIMEOUT

    async def run_scenario(self, scenario: TestScenario) -> ScenarioResult:
        """Execute a full test scenario.

        1. Pre-generate all audio
        2. Register pending test with receiver
        3. Trigger outbound call via reco
        4. Wait for inbound call
        5. Run each step (wait for agent turn, play our audio, record result)
        6. Hang up and fetch post-call data
        7. Return ScenarioResult
        """
        started_at = datetime.now()
        start_time = time.monotonic()
        result = ScenarioResult(
            scenario_id=scenario.scenario_id,
            test_mode=scenario.mode,
            started_at=started_at,
        )

        try:
            # 1. Pre-generate audio for all steps
            logger.info("Pre-generating audio for scenario %s", scenario.scenario_id)
            prepared_audio_list = await self.audio_generator.prepare_scenario_audio(scenario)

            # Build a mapping: step_number -> PreparedAudio
            audio_by_step: dict[int, PreparedAudio] = {
                pa.step: pa for pa in prepared_audio_list
            }

            # 2. Register pending test (if receiver supports it)
            if hasattr(self.receiver, "register_pending_test"):
                self.receiver.register_pending_test(
                    phone_number=settings.TWILIO_PHONE_NUMBER or "+10000000000",
                    scenario_id=scenario.scenario_id,
                )

            # 3. Trigger outbound call via reco
            logger.info("Triggering outbound call for scenario %s", scenario.scenario_id)
            call_start = await self.reco_client.start_call(
                phone=settings.TWILIO_PHONE_NUMBER or "+10000000000",
                customer_id="qa-test",
                flow_path=scenario.flow_path or "flow/flow.yaml",
                metadata={"qa_scenario": scenario.scenario_id},
            )
            result.call_id = call_start.call_id
            result.conversation_id = call_start.conversation_id

            # 4. Wait for inbound call
            logger.info("Waiting for call to arrive (timeout=%ss)", self.call_wait_timeout)
            try:
                call = await self.receiver.wait_for_call(timeout=self.call_wait_timeout)
            except (TimeoutError, asyncio.TimeoutError) as e:
                result.error = f"Call timeout: no call arrived within {self.call_wait_timeout}s"
                result.duration_s = time.monotonic() - start_time
                logger.error(result.error)
                return result

            # 5. Run each step
            logger.info("Call connected, running %d steps", len(scenario.steps))
            for step in scenario.steps:
                prepared = audio_by_step.get(step.step)
                try:
                    step_result = await asyncio.wait_for(
                        self._run_step(step, prepared, call),
                        timeout=self.step_timeout,
                    )
                except asyncio.TimeoutError:
                    step_result = StepResult(
                        step_number=step.step,
                        expected_block=step.expected_block,
                        error=f"Step timeout after {self.step_timeout}s",
                    )
                    logger.warning("Step %d timed out", step.step)
                except Exception as e:
                    step_result = StepResult(
                        step_number=step.step,
                        expected_block=step.expected_block,
                        error=f"Step error: {e}",
                    )
                    logger.error("Step %d error: %s", step.step, e)

                result.steps.append(step_result)

                # If we got a disconnect error, stop running steps
                if step_result.error and "disconnect" in step_result.error.lower():
                    result.error = "Call disconnected during scenario"
                    break

            # 6. Hang up
            logger.info("Hanging up call")
            try:
                await self.receiver.hangup(call)
            except Exception as e:
                logger.warning("Hangup error (non-fatal): %s", e)

            # 7. Poll reco for call completion
            if result.call_id:
                try:
                    await self.reco_client.poll_status(result.call_id, timeout=30)
                except Exception as e:
                    logger.warning("Poll status error (non-fatal): %s", e)

            # 8. Fetch post-call data
            if result.conversation_id:
                try:
                    result.reco_transcript = await self.reco_client.get_transcript(
                        result.conversation_id
                    )
                except Exception as e:
                    logger.warning("Failed to fetch transcript: %s", e)

            # 9. Compute latency stats
            latencies = self.turn_detector.latencies
            if latencies:
                sorted_lat = sorted(latencies)
                n = len(sorted_lat)
                result.latency_p50_ms = sorted_lat[n // 2]
                p95_idx = min(int(n * 0.95), n - 1)
                result.latency_p95_ms = sorted_lat[p95_idx]

        except Exception as e:
            result.error = f"Scenario error: {e}"
            logger.error("Scenario %s failed: %s", scenario.scenario_id, e)

        result.duration_s = time.monotonic() - start_time
        return result

    async def _run_step(
        self, step: TestStep, prepared_audio: PreparedAudio | None, call: ActiveCall
    ) -> StepResult:
        """Execute a single step: wait for agent turn, play our audio, record result."""
        logger.info("Running step %d (expected_block=%s)", step.step, step.expected_block)

        # Wait for agent to finish speaking
        agent_audio, duration_ms = await self._wait_for_agent_turn(call)

        latency = self.turn_detector.get_latency()

        # Build step result
        step_result = StepResult(
            step_number=step.step,
            expected_block=step.expected_block,
            agent_audio=agent_audio if agent_audio else None,
            agent_audio_duration_ms=duration_ms,
            latency_ms=latency,
            user_input_text=step.user_input,
        )

        # Play our prepared audio response (if we have one for this step)
        if prepared_audio is not None:
            try:
                await self._play_audio(call, prepared_audio)
                self.turn_detector.mark_our_audio_sent(time.time())
            except Exception as e:
                logger.warning("Failed to send audio for step %d: %s", step.step, e)

        return step_result

    async def _wait_for_agent_turn(self, call: ActiveCall) -> tuple[bytes, float]:
        """Listen to agent audio via turn_detector until the agent's turn ends.

        Returns (agent_audio_mulaw, duration_ms).
        """
        while True:
            chunk = await self.receiver.get_audio_chunk(call)
            if chunk is None:
                # Call ended or timeout
                audio = self.turn_detector.get_turn_audio()
                return audio, 0.0

            event = self.turn_detector.feed_audio(chunk, time.time())
            if event is not None and event.type == "turn_ended":
                audio = self.turn_detector.get_turn_audio()
                duration = event.duration_ms or 0.0
                self.turn_detector.reset()
                return audio, duration

    async def _play_audio(self, call: ActiveCall, prepared_audio: PreparedAudio) -> None:
        """Send prepared audio chunks to the call.

        Concatenates all chunks and sends via receiver.send_audio.
        """
        full_audio = b"".join(prepared_audio.mulaw_chunks)
        await self.receiver.send_audio(call, full_audio)
