"""Audio generation — TTS, file loading, and mulaw conversion for Twilio."""

from __future__ import annotations

import audioop
import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np

from config import settings
from core.audio_cache import AudioCache
from models.scenario import TestScenario


# Twilio expects mulaw 8kHz mono, 160-byte chunks (20ms frames)
TWILIO_SAMPLE_RATE = 8000
TWILIO_CHUNK_SIZE = 160  # 20ms at 8kHz, 1 byte per sample (mulaw)
MULAW_SILENCE = b"\xff"  # mulaw silence byte

# Cartesia returns PCM16 at 24kHz by default
CARTESIA_SAMPLE_RATE = 24000
CARTESIA_API_URL = "https://api.cartesia.ai/tts/bytes"


@dataclass
class PreparedAudio:
    """Audio prepared and chunked for Twilio streaming."""
    step: int
    text_reference: str          # The text (for evaluation reference)
    mulaw_chunks: list[bytes]    # 160-byte mulaw chunks ready for Twilio
    duration_ms: float           # Total audio duration
    source: str                  # "tts" or "file"


class AudioGenerator:
    """Generate and prepare audio for scenario playback over Twilio.

    Supports three modes:
    - Cartesia TTS: real API calls for Japanese text-to-speech
    - File loading: load pre-recorded WAV files
    - Mock mode: generate sine wave tones (no API key needed)
    """

    def __init__(
        self,
        tts_provider: str = "cartesia",
        cache_dir: str | None = None,
    ) -> None:
        self.tts_provider = tts_provider
        cache_path = cache_dir or settings.AUDIO_CACHE_DIR
        self.cache = AudioCache(cache_dir=cache_path)

    async def prepare_scenario_audio(
        self, scenario: TestScenario
    ) -> list[PreparedAudio]:
        """Pre-generate all audio for a scenario's steps before the call starts.

        For each step:
          - If step.user_audio exists -> load from file
          - Elif step.user_input exists -> generate TTS
          - Else -> no audio (agent speaks first, skip)

        Returns list of PreparedAudio ready to send to Twilio.
        """
        prepared: list[PreparedAudio] = []

        for step in scenario.steps:
            if step.user_audio:
                # Load from file
                pcm_audio = self.load_audio_file(step.user_audio)
                # Assume WAV files are already at a known rate; detect from file
                source_rate = self._detect_sample_rate(step.user_audio)
                mulaw_audio = self.convert_to_twilio(pcm_audio, source_rate)
                chunks = self.chunk_for_twilio(mulaw_audio)
                duration_ms = len(mulaw_audio) / TWILIO_SAMPLE_RATE * 1000
                prepared.append(PreparedAudio(
                    step=step.step,
                    text_reference=step.user_input or step.user_audio,
                    mulaw_chunks=chunks,
                    duration_ms=duration_ms,
                    source="file",
                ))

            elif step.user_input:
                # Generate TTS
                pcm_audio = await self.generate_tts(step.user_input, language="ja")
                source_rate = (
                    CARTESIA_SAMPLE_RATE
                    if self.tts_provider == "cartesia"
                    else TWILIO_SAMPLE_RATE
                )
                mulaw_audio = self.convert_to_twilio(pcm_audio, source_rate)
                chunks = self.chunk_for_twilio(mulaw_audio)
                duration_ms = len(mulaw_audio) / TWILIO_SAMPLE_RATE * 1000
                prepared.append(PreparedAudio(
                    step=step.step,
                    text_reference=step.user_input,
                    mulaw_chunks=chunks,
                    duration_ms=duration_ms,
                    source="tts",
                ))

            # Else: no user audio for this step (agent speaks first), skip

        return prepared

    async def generate_tts(self, text: str, language: str = "ja") -> bytes:
        """Generate TTS audio. Returns raw PCM16 bytes.

        In mock mode, generates a sine wave tone.
        In cartesia mode, calls Cartesia REST API.
        """
        if self.tts_provider == "mock":
            return self._generate_mock_audio(text)

        # Check cache first
        cached = self.cache.get(text, language, provider=self.tts_provider)
        if cached is not None:
            return cached

        # Call Cartesia API
        pcm_audio = await self._call_cartesia(text, language)
        self.cache.put(text, language, pcm_audio, provider=self.tts_provider)
        return pcm_audio

    def load_audio_file(self, path: str) -> bytes:
        """Load a WAV audio file from disk. Returns raw PCM16 bytes."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        with wave.open(str(file_path), "rb") as wf:
            if wf.getsampwidth() != 2:
                raise ValueError(
                    f"Expected 16-bit WAV, got {wf.getsampwidth() * 8}-bit"
                )
            if wf.getnchannels() != 1:
                raise ValueError(
                    f"Expected mono WAV, got {wf.getnchannels()} channels"
                )
            return wf.readframes(wf.getnframes())

    def convert_to_twilio(self, pcm_audio: bytes, source_sample_rate: int) -> bytes:
        """Convert PCM16 audio to Twilio's mulaw 8kHz format.

        Steps:
          1. Resample to 8kHz if needed
          2. Convert PCM16 -> mulaw
        Returns mulaw bytes.
        """
        # Resample to 8kHz if needed
        if source_sample_rate != TWILIO_SAMPLE_RATE:
            pcm_audio = self._resample_pcm16(
                pcm_audio, source_sample_rate, TWILIO_SAMPLE_RATE
            )

        # Convert PCM16 to mulaw (sample width = 2 bytes for PCM16)
        mulaw_audio = audioop.lin2ulaw(pcm_audio, 2)
        return mulaw_audio

    def chunk_for_twilio(self, mulaw_audio: bytes) -> list[bytes]:
        """Split mulaw audio into exactly 160-byte chunks (20ms frames).

        Pads last chunk with silence if needed.
        """
        chunks: list[bytes] = []
        for i in range(0, len(mulaw_audio), TWILIO_CHUNK_SIZE):
            chunk = mulaw_audio[i : i + TWILIO_CHUNK_SIZE]
            if len(chunk) < TWILIO_CHUNK_SIZE:
                # Pad with mulaw silence
                chunk = chunk + MULAW_SILENCE * (TWILIO_CHUNK_SIZE - len(chunk))
            chunks.append(chunk)
        return chunks

    def _detect_sample_rate(self, path: str) -> int:
        """Detect sample rate from a WAV file."""
        with wave.open(path, "rb") as wf:
            return wf.getframerate()

    def _generate_mock_audio(self, text: str) -> bytes:
        """Generate a sine wave tone for mock mode.

        Duration is based on text length (~100ms per character, min 500ms).
        Output: PCM16 at 8kHz (skips resampling step).
        """
        duration_ms = max(500, len(text) * 100)
        duration_s = duration_ms / 1000.0
        num_samples = int(TWILIO_SAMPLE_RATE * duration_s)

        frequency = 440.0  # A4 tone
        amplitude = 8000  # Moderate volume (PCM16 range is -32768 to 32767)

        samples = []
        for i in range(num_samples):
            t = i / TWILIO_SAMPLE_RATE
            value = int(amplitude * math.sin(2 * math.pi * frequency * t))
            samples.append(value)

        # Pack as PCM16 little-endian
        return struct.pack(f"<{num_samples}h", *samples)

    def _resample_pcm16(
        self, pcm_audio: bytes, from_rate: int, to_rate: int
    ) -> bytes:
        """Resample PCM16 audio using numpy linear interpolation."""
        # Decode PCM16 to numpy array
        samples = np.frombuffer(pcm_audio, dtype=np.int16).astype(np.float64)

        # Calculate new length
        duration = len(samples) / from_rate
        new_length = int(duration * to_rate)

        if new_length == 0:
            return b""

        # Linear interpolation
        old_indices = np.linspace(0, len(samples) - 1, new_length)
        resampled = np.interp(old_indices, np.arange(len(samples)), samples)

        # Convert back to PCM16
        resampled = np.clip(resampled, -32768, 32767).astype(np.int16)
        return resampled.tobytes()

    async def _call_cartesia(self, text: str, language: str) -> bytes:
        """Call Cartesia TTS API. Returns PCM16 bytes at 24kHz."""
        if not settings.CARTESIA_API_KEY:
            raise RuntimeError(
                "CARTESIA_API_KEY not set. Use mock mode for testing."
            )

        headers = {
            "X-API-Key": settings.CARTESIA_API_KEY,
            "Cartesia-Version": "2024-06-10",
            "Content-Type": "application/json",
        }

        payload = {
            "model_id": "sonic-2",
            "transcript": text,
            "voice": {"mode": "id", "id": settings.CARTESIA_VOICE_ID},
            "language": language,
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": CARTESIA_SAMPLE_RATE,
            },
        }

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        CARTESIA_API_URL,
                        json=payload,
                        headers=headers,
                    )
                    response.raise_for_status()
                    return response.content
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                last_error = e
                if attempt < 2:
                    import asyncio
                    await asyncio.sleep(1.0 * (attempt + 1))

        raise RuntimeError(
            f"Cartesia TTS failed after 3 attempts: {last_error}"
        ) from last_error
