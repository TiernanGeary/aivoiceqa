"""Voice Activity Detection engine with state machine for turn detection.

Processes mulaw audio chunks from Twilio, detects when the agent stops
speaking (silence threshold), and tracks response latency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from core.audio_utils import mulaw_to_pcm16, resample_8k_to_16k


class TurnState(Enum):
    """State machine states for turn detection."""
    IDLE = "idle"                        # Waiting for agent to speak
    AGENT_SPEAKING = "agent_speaking"    # Agent is talking
    SILENCE_DETECTED = "silence"         # Silence threshold reached


@dataclass
class TurnEvent:
    """Emitted when a state transition occurs."""
    type: str  # "speech_started" or "turn_ended"
    timestamp: float
    duration_ms: float | None = None  # speech duration for turn_ended events


class TurnDetector:
    """Detects agent turn boundaries using silero-vad.

    Processes 20ms mulaw chunks (160 bytes at 8kHz) and transitions through:
    IDLE -> AGENT_SPEAKING -> SILENCE_DETECTED -> IDLE

    Args:
        silence_threshold_ms: How long silence must last to end a turn (default 1500ms).
        min_speech_ms: Minimum speech duration to count as real speech (default 300ms).
        vad_model: Optional pre-loaded silero-vad model. If None, loads via torch.hub.
    """

    CHUNK_DURATION_MS = 20  # Each Twilio chunk is 20ms

    def __init__(
        self,
        silence_threshold_ms: int = 1500,
        min_speech_ms: int = 300,
        vad_model: object | None = None,
    ) -> None:
        self.silence_threshold_ms = silence_threshold_ms
        self.min_speech_ms = min_speech_ms
        self.speech_threshold = 0.5

        # VAD model (silero-vad)
        self._vad_model = vad_model
        if self._vad_model is None:
            self._vad_model = self._load_vad_model()

        # State
        self._state = TurnState.IDLE
        self._speech_start_ts: float | None = None
        self._speech_frames: int = 0       # consecutive speech frames (in IDLE)
        self._silence_ms: float = 0.0      # accumulated silence in AGENT_SPEAKING
        self._turn_audio: bytearray = bytearray()  # mulaw audio buffer

        # Latency tracking
        self._our_audio_sent_ts: float | None = None
        self._latencies: list[float] = []

    @staticmethod
    def _load_vad_model() -> object:
        """Load silero-vad model via torch.hub, falling back to energy-based VAD."""
        try:
            import torch  # type: ignore[import-not-found]

            model, _utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                onnx=False,
            )
            return model
        except (ImportError, Exception):
            import logging
            logging.getLogger(__name__).warning(
                "torch/silero-vad not available — using energy-based VAD fallback"
            )
            return TurnDetector._energy_vad_model()

    @staticmethod
    def _energy_vad_model():
        """Simple RMS-energy VAD model — no torch required."""
        def vad_fn(samples, sample_rate):
            try:
                import torch
                if isinstance(samples, torch.Tensor):
                    if samples.numel() == 0:
                        return 0.0
                    return 0.9 if samples.pow(2).mean().sqrt().item() > 0.015 else 0.1
            except ImportError:
                pass
            if not samples:
                return 0.0
            if isinstance(samples, (list, tuple)):
                rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
            else:
                rms = 0.0
            return 0.9 if rms > 500 else 0.1
        return vad_fn

    @property
    def state(self) -> TurnState:
        """Current state of the detector."""
        return self._state

    @property
    def latencies(self) -> list[float]:
        """All recorded latencies in ms."""
        return list(self._latencies)

    def feed_audio(self, mulaw_chunk: bytes, timestamp: float) -> TurnEvent | None:
        """Feed a 20ms mulaw audio chunk (160 bytes at 8kHz).

        Converts to PCM16 16kHz, runs VAD, and manages state transitions.
        Returns a TurnEvent if a state transition occurred, None otherwise.
        """
        # Convert mulaw -> PCM16 8kHz -> PCM16 16kHz
        pcm16_8k = mulaw_to_pcm16(mulaw_chunk)
        pcm16_16k = resample_8k_to_16k(pcm16_8k)

        # Run VAD
        prob = self._run_vad(pcm16_16k)

        is_speech = prob > self.speech_threshold

        if self._state == TurnState.IDLE:
            return self._handle_idle(mulaw_chunk, timestamp, is_speech)
        elif self._state == TurnState.AGENT_SPEAKING:
            return self._handle_speaking(mulaw_chunk, timestamp, is_speech)
        elif self._state == TurnState.SILENCE_DETECTED:
            # Auto-reset to IDLE after emitting turn_ended
            self.reset()
            return self._handle_idle(mulaw_chunk, timestamp, is_speech)
        return None

    def _run_vad(self, pcm16_16k: bytes) -> float:
        """Run silero-vad on a 16kHz PCM16 chunk. Returns speech probability."""
        import struct as _struct

        n_samples = len(pcm16_16k) // 2
        samples = _struct.unpack(f"<{n_samples}h", pcm16_16k)

        try:
            import torch  # type: ignore[import-not-found]

            # Normalize to float32 [-1, 1]
            tensor = torch.FloatTensor(samples) / 32768.0
            prob = self._vad_model(tensor, 16000)
            if isinstance(prob, torch.Tensor):
                return prob.item()
            return float(prob)
        except ImportError:
            # No torch available — model must be a callable mock
            # Pass raw samples as a list (for testing)
            prob = self._vad_model(list(samples), 16000)
            return float(prob)

    def _handle_idle(
        self, mulaw_chunk: bytes, timestamp: float, is_speech: bool
    ) -> TurnEvent | None:
        """Handle audio in IDLE state."""
        if is_speech:
            self._speech_frames += 1
            frames_for_min = self.min_speech_ms / self.CHUNK_DURATION_MS
            if self._speech_frames >= frames_for_min:
                # Enough consecutive speech — transition to AGENT_SPEAKING
                self._state = TurnState.AGENT_SPEAKING
                self._speech_start_ts = timestamp - (self._speech_frames - 1) * self.CHUNK_DURATION_MS / 1000.0
                self._silence_ms = 0.0
                # Store the audio accumulated during the speech detection window
                self._turn_audio.extend(mulaw_chunk)

                # Calculate latency if we have a sent timestamp
                latency_ms: float | None = None
                if self._our_audio_sent_ts is not None:
                    latency_ms = (self._speech_start_ts - self._our_audio_sent_ts) * 1000.0
                    self._latencies.append(latency_ms)
                    self._our_audio_sent_ts = None  # consumed

                return TurnEvent(
                    type="speech_started",
                    timestamp=self._speech_start_ts,
                )
            else:
                # Accumulating speech frames but not enough yet
                self._turn_audio.extend(mulaw_chunk)
        else:
            # Reset speech frame counter on non-speech
            self._speech_frames = 0
            self._turn_audio.clear()
        return None

    def _handle_speaking(
        self, mulaw_chunk: bytes, timestamp: float, is_speech: bool
    ) -> TurnEvent | None:
        """Handle audio in AGENT_SPEAKING state."""
        self._turn_audio.extend(mulaw_chunk)

        if not is_speech:
            self._silence_ms += self.CHUNK_DURATION_MS
            if self._silence_ms >= self.silence_threshold_ms:
                # Silence threshold reached — turn ended
                self._state = TurnState.SILENCE_DETECTED
                duration_ms = None
                if self._speech_start_ts is not None:
                    duration_ms = (timestamp - self._speech_start_ts) * 1000.0
                return TurnEvent(
                    type="turn_ended",
                    timestamp=timestamp,
                    duration_ms=duration_ms,
                )
        else:
            # Speech resumed — reset silence counter
            self._silence_ms = 0.0

        return None

    def get_turn_audio(self) -> bytes:
        """Get accumulated mulaw audio for the current/just-ended turn.

        Call after receiving a turn_ended event.
        """
        return bytes(self._turn_audio)

    def get_latency(self) -> float | None:
        """Get latency (ms) for the last completed exchange.

        Returns time between our audio ending and agent speech starting.
        Returns None if no complete exchange recorded yet.
        """
        if not self._latencies:
            return None
        return self._latencies[-1]

    def mark_our_audio_sent(self, timestamp: float) -> None:
        """Record when we finished playing our audio.

        Used to calculate agent response latency.
        """
        self._our_audio_sent_ts = timestamp

    def reset(self) -> None:
        """Reset state for next turn. Preserves latency history."""
        self._state = TurnState.IDLE
        self._speech_start_ts = None
        self._speech_frames = 0
        self._silence_ms = 0.0
        self._turn_audio.clear()
