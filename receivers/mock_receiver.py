"""Mock call receiver for testing without Twilio.

Simulates the call flow: generates synthetic agent audio (mulaw silence/tone),
captures sent audio, and provides a controllable test double for integration tests.
"""

from __future__ import annotations

import asyncio
import struct
import math
import time
from dataclasses import dataclass, field

from receivers.base import ActiveCall, CallReceiver


# Mulaw 8kHz: 160 bytes = 20ms frame
MULAW_FRAME_SIZE = 160
FRAME_DURATION_S = 0.020


def _generate_mulaw_speech(duration_ms: float = 500, frequency: float = 440.0) -> bytes:
    """Generate synthetic mulaw audio that registers as speech.

    Produces a sine wave tone at 8kHz sample rate, then converts to mulaw.
    This creates audio with energy that a VAD model would detect as speech.
    """
    import audioop

    sample_rate = 8000
    num_samples = int(sample_rate * duration_ms / 1000.0)
    amplitude = 16000  # Loud enough to trigger VAD

    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        value = int(amplitude * math.sin(2 * math.pi * frequency * t))
        samples.append(value)

    pcm16 = struct.pack(f"<{num_samples}h", *samples)
    mulaw = audioop.lin2ulaw(pcm16, 2)
    return mulaw


def _generate_mulaw_silence(duration_ms: float = 200) -> bytes:
    """Generate mulaw silence bytes."""
    num_bytes = int(8000 * duration_ms / 1000.0)
    return b"\xff" * num_bytes


class MockReceiver(CallReceiver):
    """Simulates receiving a call for mock/test mode.

    Feeds synthetic agent audio into the audio queue and captures
    any audio sent back. Allows controlling the simulated agent behavior.
    """

    def __init__(
        self,
        agent_speech_ms: float = 500,
        agent_silence_ms: float = 2000,
        num_agent_turns: int = 10,
        call_connect_delay: float = 0.01,
    ) -> None:
        self.agent_speech_ms = agent_speech_ms
        self.agent_silence_ms = agent_silence_ms
        self.num_agent_turns = num_agent_turns
        self.call_connect_delay = call_connect_delay

        self._active_call: ActiveCall | None = None
        self._call_event = asyncio.Event()
        self._sent_audio: list[bytes] = []
        self._hungup = False
        self._feed_task: asyncio.Task | None = None

    @property
    def sent_audio(self) -> list[bytes]:
        """All audio chunks that were sent into the call."""
        return list(self._sent_audio)

    @property
    def hungup(self) -> bool:
        return self._hungup

    async def start_feeding(self) -> None:
        """Start the background task that feeds agent audio into the queue.

        Call this after registering the active call.
        """
        if self._active_call is None:
            return
        self._feed_task = asyncio.create_task(self._feed_agent_audio())

    async def _feed_agent_audio(self) -> None:
        """Background: feed synthetic agent audio (speech + silence) into the queue."""
        call = self._active_call
        if call is None:
            return

        for turn in range(self.num_agent_turns):
            if self._hungup:
                break

            # Agent speaks
            speech = _generate_mulaw_speech(self.agent_speech_ms)
            for i in range(0, len(speech), MULAW_FRAME_SIZE):
                if self._hungup:
                    return
                chunk = speech[i: i + MULAW_FRAME_SIZE]
                if len(chunk) < MULAW_FRAME_SIZE:
                    chunk = chunk + b"\xff" * (MULAW_FRAME_SIZE - len(chunk))
                await call.audio_queue.put(chunk)
                await asyncio.sleep(0.001)  # Yield control, fast for tests

            # Agent is silent
            silence = _generate_mulaw_silence(self.agent_silence_ms)
            for i in range(0, len(silence), MULAW_FRAME_SIZE):
                if self._hungup:
                    return
                chunk = silence[i: i + MULAW_FRAME_SIZE]
                if len(chunk) < MULAW_FRAME_SIZE:
                    chunk = chunk + b"\xff" * (MULAW_FRAME_SIZE - len(chunk))
                await call.audio_queue.put(chunk)
                await asyncio.sleep(0.001)

        # End of all turns — signal call ended
        if not self._hungup:
            await call.audio_queue.put(b"")

    async def wait_for_call(self, timeout: float = 30) -> ActiveCall:
        """Simulate waiting for a call to arrive."""
        await asyncio.sleep(self.call_connect_delay)

        call = ActiveCall(
            call_sid="mock-call-sid",
            stream_sid="mock-stream-sid",
            websocket=None,
            started_at=time.time(),
        )
        self._active_call = call
        self._call_event.set()
        # Start feeding agent audio in the background
        await self.start_feeding()
        return call

    async def get_audio_chunk(self, call: ActiveCall) -> bytes | None:
        """Get next audio chunk from the mock audio queue."""
        try:
            chunk = await asyncio.wait_for(call.audio_queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return None

        if chunk == b"":
            return None
        return chunk

    async def send_audio(self, call: ActiveCall, audio: bytes) -> None:
        """Capture audio that would be sent to the call."""
        self._sent_audio.append(audio)

    async def clear_audio(self, call: ActiveCall) -> None:
        """No-op for mock."""
        pass

    async def hangup(self, call: ActiveCall) -> None:
        """Signal call ended."""
        self._hungup = True
        if self._feed_task and not self._feed_task.done():
            self._feed_task.cancel()
            try:
                await self._feed_task
            except asyncio.CancelledError:
                pass
        await call.audio_queue.put(b"")
