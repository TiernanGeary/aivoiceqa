"""Tests for VAD engine: audio_utils and TurnDetector.

All tests work without torch installed by mocking the silero-vad model.
"""

from __future__ import annotations

import struct
from unittest.mock import MagicMock

import pytest

from core.audio_utils import (
    mulaw_to_pcm16,
    pcm16_to_mulaw,
    resample_8k_to_16k,
)
from core.vad import TurnDetector, TurnEvent, TurnState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mulaw_chunk(value: int = 0xFF, size: int = 160) -> bytes:
    """Create a mulaw chunk of given size. 0xFF = silence in mulaw."""
    return bytes([value]) * size


def make_mock_vad(speech_probs: list[float] | None = None):
    """Create a mock VAD model that returns probabilities from a list.

    If speech_probs is None, always returns 0.0 (silence).
    The mock is callable and pops from the front of the list.
    """
    if speech_probs is None:
        speech_probs = []

    remaining = list(speech_probs)
    model = MagicMock()

    def vad_call(tensor, sample_rate):
        if remaining:
            return remaining.pop(0)
        return 0.0

    model.side_effect = vad_call
    return model


# ---------------------------------------------------------------------------
# audio_utils tests
# ---------------------------------------------------------------------------

class TestMulawToPcm16:
    """Test mulaw -> PCM16 conversion."""

    def test_empty_input(self):
        result = mulaw_to_pcm16(b"")
        assert result == b""

    def test_output_length_doubles(self):
        """Each mulaw byte becomes 2 bytes (int16)."""
        mulaw = make_mulaw_chunk(size=160)
        pcm = mulaw_to_pcm16(mulaw)
        assert len(pcm) == 320  # 160 * 2

    def test_silence_byte_decodes_near_zero(self):
        """mulaw 0xFF should decode to a small value near zero."""
        pcm = mulaw_to_pcm16(bytes([0xFF]))
        sample = struct.unpack("<h", pcm)[0]
        assert abs(sample) < 100  # Near silence

    def test_roundtrip_preserves_shape(self):
        """mulaw -> PCM16 -> mulaw should roughly preserve values."""
        original = bytes(range(256))
        pcm = mulaw_to_pcm16(original)
        back = pcm16_to_mulaw(pcm)
        # mulaw is lossy but roundtrip should be close
        assert len(back) == 256
        # Most bytes should roundtrip exactly or very close
        matches = sum(1 for a, b in zip(original, back) if a == b)
        assert matches > 200  # Allow some lossy deviation


class TestPcm16ToMulaw:
    """Test PCM16 -> mulaw conversion."""

    def test_empty_input(self):
        result = pcm16_to_mulaw(b"")
        assert result == b""

    def test_output_length_halves(self):
        """Each int16 (2 bytes) becomes 1 mulaw byte."""
        pcm = struct.pack("<160h", *([0] * 160))
        mulaw = pcm16_to_mulaw(pcm)
        assert len(mulaw) == 160

    def test_silence_encodes_correctly(self):
        """Zero PCM should encode to mulaw silence."""
        pcm = struct.pack("<1h", 0)
        mulaw = pcm16_to_mulaw(pcm)
        assert len(mulaw) == 1


class TestResample8kTo16k:
    """Test 8kHz -> 16kHz resampling."""

    def test_empty_input(self):
        result = resample_8k_to_16k(b"")
        assert result == b""

    def test_output_doubles_sample_count(self):
        """16kHz output should have 2x the samples of 8kHz input."""
        n_samples = 80
        pcm_8k = struct.pack(f"<{n_samples}h", *([1000] * n_samples))
        pcm_16k = resample_8k_to_16k(pcm_8k)
        out_samples = len(pcm_16k) // 2
        assert out_samples == n_samples * 2

    def test_constant_signal_preserved(self):
        """A constant signal should remain constant after resampling."""
        n = 10
        val = 5000
        pcm_8k = struct.pack(f"<{n}h", *([val] * n))
        pcm_16k = resample_8k_to_16k(pcm_8k)
        out_samples = struct.unpack(f"<{n * 2}h", pcm_16k)
        # All samples should be the same value (interpolation of equal values)
        assert all(s == val for s in out_samples)

    def test_interpolation(self):
        """Check that interpolated samples are between neighbors."""
        samples = [0, 1000, 2000, 3000]
        pcm_8k = struct.pack(f"<{len(samples)}h", *samples)
        pcm_16k = resample_8k_to_16k(pcm_8k)
        out = struct.unpack(f"<{len(samples) * 2}h", pcm_16k)
        # Interpolated sample between 0 and 1000 should be 500
        assert out[0] == 0
        assert out[1] == 500
        assert out[2] == 1000
        assert out[3] == 1500


# ---------------------------------------------------------------------------
# TurnDetector state machine tests
# ---------------------------------------------------------------------------

class TestTurnDetectorInit:
    """Test TurnDetector initialization."""

    def test_default_params(self):
        model = make_mock_vad()
        td = TurnDetector(vad_model=model)
        assert td.silence_threshold_ms == 1500
        assert td.min_speech_ms == 300
        assert td.state == TurnState.IDLE

    def test_custom_params(self):
        model = make_mock_vad()
        td = TurnDetector(silence_threshold_ms=2000, min_speech_ms=500, vad_model=model)
        assert td.silence_threshold_ms == 2000
        assert td.min_speech_ms == 500


class TestTurnDetectorStateMachine:
    """Test state transitions: IDLE -> AGENT_SPEAKING -> SILENCE_DETECTED."""

    def _make_detector(self, speech_probs: list[float], **kwargs) -> TurnDetector:
        """Create a TurnDetector with mocked VAD returning given probabilities."""
        model = make_mock_vad(speech_probs)
        return TurnDetector(vad_model=model, **kwargs)

    def test_idle_on_silence(self):
        """Detector stays IDLE when receiving silence."""
        probs = [0.0] * 20
        td = self._make_detector(probs)
        chunk = make_mulaw_chunk()
        for i in range(20):
            event = td.feed_audio(chunk, timestamp=i * 0.02)
            assert event is None
        assert td.state == TurnState.IDLE

    def test_transition_to_speaking(self):
        """After min_speech_ms of speech, transitions to AGENT_SPEAKING."""
        # 300ms min speech = 15 frames at 20ms each
        n_speech_frames = 15
        probs = [0.9] * n_speech_frames
        td = self._make_detector(probs, min_speech_ms=300)
        chunk = make_mulaw_chunk()

        events = []
        for i in range(n_speech_frames):
            event = td.feed_audio(chunk, timestamp=i * 0.02)
            if event is not None:
                events.append(event)

        assert td.state == TurnState.AGENT_SPEAKING
        assert len(events) == 1
        assert events[0].type == "speech_started"

    def test_short_noise_filtered(self):
        """Noise shorter than min_speech_ms doesn't trigger speech_started."""
        # 5 frames of speech (100ms) then silence — below 300ms minimum
        probs = [0.9] * 5 + [0.0] * 10
        td = self._make_detector(probs, min_speech_ms=300)
        chunk = make_mulaw_chunk()

        events = []
        for i in range(15):
            event = td.feed_audio(chunk, timestamp=i * 0.02)
            if event is not None:
                events.append(event)

        assert td.state == TurnState.IDLE
        assert len(events) == 0

    def test_turn_ended_after_silence_threshold(self):
        """After silence_threshold_ms of silence, turn_ended fires."""
        # 15 frames speech (300ms) + 75 frames silence (1500ms)
        n_speech = 15
        n_silence = 75  # 75 * 20ms = 1500ms
        probs = [0.9] * n_speech + [0.0] * n_silence
        td = self._make_detector(probs, min_speech_ms=300, silence_threshold_ms=1500)
        chunk = make_mulaw_chunk()

        events = []
        for i in range(n_speech + n_silence):
            event = td.feed_audio(chunk, timestamp=i * 0.02)
            if event is not None:
                events.append(event)

        assert len(events) == 2
        assert events[0].type == "speech_started"
        assert events[1].type == "turn_ended"
        assert events[1].duration_ms is not None
        assert td.state == TurnState.SILENCE_DETECTED

    def test_silence_reset_on_resumed_speech(self):
        """If speech resumes before threshold, silence counter resets."""
        # 15 speech + 30 silence (600ms, below 1500ms threshold) + 10 speech
        probs = [0.9] * 15 + [0.0] * 30 + [0.9] * 10
        td = self._make_detector(probs, min_speech_ms=300, silence_threshold_ms=1500)
        chunk = make_mulaw_chunk()

        events = []
        for i in range(55):
            event = td.feed_audio(chunk, timestamp=i * 0.02)
            if event is not None:
                events.append(event)

        # Should only have speech_started, no turn_ended
        assert len(events) == 1
        assert events[0].type == "speech_started"
        assert td.state == TurnState.AGENT_SPEAKING

    def test_turn_audio_accumulated(self):
        """Turn audio buffer contains data from the speaking period."""
        n_speech = 15
        n_silence = 75
        probs = [0.9] * n_speech + [0.0] * n_silence
        td = self._make_detector(probs, min_speech_ms=300, silence_threshold_ms=1500)
        chunk = make_mulaw_chunk(value=0x80, size=160)

        for i in range(n_speech + n_silence):
            td.feed_audio(chunk, timestamp=i * 0.02)

        audio = td.get_turn_audio()
        # Should have accumulated audio from speech + silence period
        assert len(audio) > 0
        # IDLE accumulates audio during speech detection (frames 0-14),
        # then AGENT_SPEAKING accumulates all subsequent frames (75 silence).
        # Total = 15 (IDLE speech window) + 75 (SPEAKING) = 90 frames
        assert len(audio) == (n_speech + n_silence) * 160

    def test_reset_clears_state(self):
        """Reset returns to IDLE with cleared buffers."""
        probs = [0.9] * 15
        td = self._make_detector(probs, min_speech_ms=300)
        chunk = make_mulaw_chunk()

        for i in range(15):
            td.feed_audio(chunk, timestamp=i * 0.02)

        assert td.state == TurnState.AGENT_SPEAKING
        td.reset()
        assert td.state == TurnState.IDLE
        assert td.get_turn_audio() == b""


class TestLatencyTracking:
    """Test latency measurement between our audio and agent response."""

    def test_latency_calculation(self):
        """Latency = speech_start - our_audio_sent."""
        n_speech = 15
        probs = [0.9] * n_speech
        model = make_mock_vad(probs)
        td = TurnDetector(vad_model=model, min_speech_ms=300)
        chunk = make_mulaw_chunk()

        # We sent audio at t=1.0
        td.mark_our_audio_sent(1.0)

        # Agent starts speaking at t=1.5 (frame 0 at t=1.22, speech detected at frame 14)
        base_ts = 1.22
        events = []
        for i in range(n_speech):
            event = td.feed_audio(chunk, timestamp=base_ts + i * 0.02)
            if event is not None:
                events.append(event)

        assert len(events) == 1
        assert events[0].type == "speech_started"

        latency = td.get_latency()
        assert latency is not None
        # speech_start_ts should account for the accumulated frames
        # Latency should be positive (agent spoke after us)
        assert latency > 0

    def test_no_latency_without_mark(self):
        """No latency returned if mark_our_audio_sent wasn't called."""
        probs = [0.9] * 15
        model = make_mock_vad(probs)
        td = TurnDetector(vad_model=model, min_speech_ms=300)
        chunk = make_mulaw_chunk()

        for i in range(15):
            td.feed_audio(chunk, timestamp=i * 0.02)

        assert td.get_latency() is None

    def test_latencies_accumulate(self):
        """Multiple exchanges accumulate latency records."""
        # First exchange: 15 speech + 75 silence
        # Second exchange: 15 speech
        probs = [0.9] * 15 + [0.0] * 75 + [0.9] * 15
        model = make_mock_vad(probs)
        td = TurnDetector(vad_model=model, min_speech_ms=300, silence_threshold_ms=1500)
        chunk = make_mulaw_chunk()

        # First exchange
        td.mark_our_audio_sent(0.0)
        for i in range(90):
            td.feed_audio(chunk, timestamp=0.1 + i * 0.02)

        # After turn_ended, state is SILENCE_DETECTED
        # Feed more speech — auto-resets to IDLE then processes
        td.mark_our_audio_sent(2.0)
        for i in range(15):
            td.feed_audio(chunk, timestamp=2.1 + i * 0.02)

        assert len(td.latencies) == 2


class TestTurnDetectorEdgeCases:
    """Edge cases and boundary conditions."""

    def test_custom_silence_threshold(self):
        """Custom silence threshold from scenario config."""
        model = make_mock_vad([0.9] * 15 + [0.0] * 50)
        td = TurnDetector(
            vad_model=model,
            silence_threshold_ms=1000,  # 50 frames
            min_speech_ms=300,
        )
        chunk = make_mulaw_chunk()

        events = []
        for i in range(65):
            event = td.feed_audio(chunk, timestamp=i * 0.02)
            if event is not None:
                events.append(event)

        assert len(events) == 2
        assert events[1].type == "turn_ended"

    def test_silence_just_below_threshold_no_event(self):
        """Silence just below threshold does not trigger turn_ended."""
        # 49 frames of silence = 980ms, threshold is 1000ms
        probs = [0.9] * 15 + [0.0] * 49
        model = make_mock_vad(probs)
        td = TurnDetector(
            vad_model=model,
            silence_threshold_ms=1000,
            min_speech_ms=300,
        )
        chunk = make_mulaw_chunk()

        events = []
        for i in range(64):
            event = td.feed_audio(chunk, timestamp=i * 0.02)
            if event is not None:
                events.append(event)

        assert len(events) == 1  # Only speech_started
        assert td.state == TurnState.AGENT_SPEAKING
