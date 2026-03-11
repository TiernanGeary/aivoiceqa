"""Tests for audio generation: mock TTS, file loading, caching, chunking."""

from __future__ import annotations

import struct
import tempfile
import wave
from pathlib import Path

import pytest

from core.audio_cache import AudioCache
from core.audio_gen import (
    AudioGenerator,
    PreparedAudio,
    TWILIO_CHUNK_SIZE,
    TWILIO_SAMPLE_RATE,
)
from models.scenario import TestScenario, TestStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav_file(path: str | Path, sample_rate: int = 8000, duration_s: float = 0.5) -> None:
    """Create a synthetic mono 16-bit WAV file."""
    num_samples = int(sample_rate * duration_s)
    # Simple ascending ramp so each sample is distinct
    samples = [int(16000 * (i / num_samples)) for i in range(num_samples)]
    pcm_data = struct.pack(f"<{num_samples}h", *samples)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)


def _make_scenario(steps: list[TestStep]) -> TestScenario:
    """Create a minimal TestScenario with given steps."""
    return TestScenario(
        scenario_id="test_scenario",
        mode="scripted",
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Mock TTS generation
# ---------------------------------------------------------------------------

class TestMockTTS:
    @pytest.fixture
    def gen(self, tmp_path: Path) -> AudioGenerator:
        return AudioGenerator(tts_provider="mock", cache_dir=str(tmp_path / "cache"))

    @pytest.mark.asyncio
    async def test_generate_tts_returns_bytes(self, gen: AudioGenerator):
        audio = await gen.generate_tts("テスト", language="ja")
        assert isinstance(audio, bytes)
        assert len(audio) > 0

    @pytest.mark.asyncio
    async def test_mock_audio_duration_scales_with_text(self, gen: AudioGenerator):
        short = await gen.generate_tts("あ", language="ja")
        long = await gen.generate_tts("あいうえおかきくけこ", language="ja")
        # Longer text should produce more audio
        assert len(long) > len(short)

    @pytest.mark.asyncio
    async def test_mock_audio_minimum_duration(self, gen: AudioGenerator):
        audio = await gen.generate_tts("a", language="ja")
        # Minimum 500ms at 8kHz PCM16 = 8000 samples = 16000 bytes
        min_bytes = int(TWILIO_SAMPLE_RATE * 0.5) * 2  # 2 bytes per sample
        assert len(audio) >= min_bytes

    @pytest.mark.asyncio
    async def test_mock_audio_is_valid_pcm16(self, gen: AudioGenerator):
        audio = await gen.generate_tts("テスト", language="ja")
        # PCM16 must have even number of bytes
        assert len(audio) % 2 == 0
        # Should be decodable as int16 samples
        num_samples = len(audio) // 2
        samples = struct.unpack(f"<{num_samples}h", audio)
        assert all(-32768 <= s <= 32767 for s in samples)


# ---------------------------------------------------------------------------
# Mulaw conversion and chunking
# ---------------------------------------------------------------------------

class TestConversion:
    @pytest.fixture
    def gen(self, tmp_path: Path) -> AudioGenerator:
        return AudioGenerator(tts_provider="mock", cache_dir=str(tmp_path / "cache"))

    def test_convert_to_twilio_returns_bytes(self, gen: AudioGenerator):
        # 100ms of silence as PCM16 at 8kHz
        pcm = b"\x00\x00" * 800
        mulaw = gen.convert_to_twilio(pcm, source_sample_rate=8000)
        assert isinstance(mulaw, bytes)
        assert len(mulaw) == 800  # 1 mulaw byte per sample

    def test_convert_resamples_from_24khz(self, gen: AudioGenerator):
        # 100ms of PCM16 at 24kHz = 2400 samples = 4800 bytes
        pcm_24k = b"\x00\x00" * 2400
        mulaw = gen.convert_to_twilio(pcm_24k, source_sample_rate=24000)
        # After resampling to 8kHz: ~800 samples -> 800 mulaw bytes
        assert 790 <= len(mulaw) <= 810  # Allow small rounding

    def test_chunk_exact_size(self, gen: AudioGenerator):
        # 320 mulaw bytes = exactly 2 chunks
        mulaw = b"\xff" * 320
        chunks = gen.chunk_for_twilio(mulaw)
        assert len(chunks) == 2
        assert all(len(c) == TWILIO_CHUNK_SIZE for c in chunks)

    def test_chunk_pads_last(self, gen: AudioGenerator):
        # 200 bytes -> 1 full chunk + 1 padded chunk
        mulaw = b"\x80" * 200
        chunks = gen.chunk_for_twilio(mulaw)
        assert len(chunks) == 2
        assert len(chunks[0]) == TWILIO_CHUNK_SIZE
        assert len(chunks[1]) == TWILIO_CHUNK_SIZE
        # First 40 bytes of last chunk should be original data
        assert chunks[1][:40] == b"\x80" * 40
        # Remaining should be silence padding
        assert chunks[1][40:] == b"\xff" * 120

    def test_chunk_empty_audio(self, gen: AudioGenerator):
        chunks = gen.chunk_for_twilio(b"")
        assert chunks == []

    @pytest.mark.asyncio
    async def test_full_pipeline_mock(self, gen: AudioGenerator):
        """End-to-end: mock TTS -> convert -> chunk."""
        pcm = await gen.generate_tts("テスト", language="ja")
        mulaw = gen.convert_to_twilio(pcm, source_sample_rate=TWILIO_SAMPLE_RATE)
        chunks = gen.chunk_for_twilio(mulaw)

        assert len(chunks) > 0
        assert all(len(c) == TWILIO_CHUNK_SIZE for c in chunks)


# ---------------------------------------------------------------------------
# WAV file loading
# ---------------------------------------------------------------------------

class TestFileLoading:
    @pytest.fixture
    def gen(self, tmp_path: Path) -> AudioGenerator:
        return AudioGenerator(tts_provider="mock", cache_dir=str(tmp_path / "cache"))

    def test_load_wav_file(self, gen: AudioGenerator, tmp_path: Path):
        wav_path = tmp_path / "test.wav"
        _make_wav_file(wav_path, sample_rate=8000, duration_s=0.5)

        pcm = gen.load_audio_file(str(wav_path))
        assert isinstance(pcm, bytes)
        # 0.5s at 8kHz PCM16 = 4000 samples * 2 bytes = 8000 bytes
        assert len(pcm) == 8000

    def test_load_missing_file_raises(self, gen: AudioGenerator):
        with pytest.raises(FileNotFoundError):
            gen.load_audio_file("/nonexistent/audio.wav")

    def test_load_and_convert_wav(self, gen: AudioGenerator, tmp_path: Path):
        """Load WAV at 16kHz, convert to mulaw 8kHz."""
        wav_path = tmp_path / "test_16k.wav"
        _make_wav_file(wav_path, sample_rate=16000, duration_s=0.5)

        pcm = gen.load_audio_file(str(wav_path))
        mulaw = gen.convert_to_twilio(pcm, source_sample_rate=16000)
        chunks = gen.chunk_for_twilio(mulaw)

        assert len(chunks) > 0
        assert all(len(c) == TWILIO_CHUNK_SIZE for c in chunks)

    def test_load_wav_24khz_and_convert(self, gen: AudioGenerator, tmp_path: Path):
        """Load WAV at 24kHz (Cartesia rate), convert to Twilio format."""
        wav_path = tmp_path / "test_24k.wav"
        _make_wav_file(wav_path, sample_rate=24000, duration_s=1.0)

        pcm = gen.load_audio_file(str(wav_path))
        mulaw = gen.convert_to_twilio(pcm, source_sample_rate=24000)
        chunks = gen.chunk_for_twilio(mulaw)

        # 1s at 8kHz = 8000 samples -> 8000 mulaw bytes -> 50 chunks
        assert 49 <= len(chunks) <= 51
        assert all(len(c) == TWILIO_CHUNK_SIZE for c in chunks)


# ---------------------------------------------------------------------------
# Audio cache
# ---------------------------------------------------------------------------

class TestAudioCache:
    @pytest.fixture
    def cache(self, tmp_path: Path) -> AudioCache:
        return AudioCache(cache_dir=str(tmp_path / "audio_cache"))

    def test_cache_miss(self, cache: AudioCache):
        result = cache.get("hello", "ja")
        assert result is None

    def test_cache_put_and_get(self, cache: AudioCache):
        audio = b"\x00\x01\x02\x03" * 100
        cache.put("hello", "ja", audio)
        result = cache.get("hello", "ja")
        assert result == audio

    def test_cache_different_text(self, cache: AudioCache):
        cache.put("hello", "ja", b"\x01")
        assert cache.get("goodbye", "ja") is None

    def test_cache_different_language(self, cache: AudioCache):
        cache.put("hello", "ja", b"\x01")
        assert cache.get("hello", "en") is None

    def test_cache_different_provider(self, cache: AudioCache):
        cache.put("hello", "ja", b"\x01", provider="cartesia")
        assert cache.get("hello", "ja", provider="mock") is None

    def test_cache_has(self, cache: AudioCache):
        assert not cache.has("hello", "ja")
        cache.put("hello", "ja", b"\x01")
        assert cache.has("hello", "ja")

    def test_cache_clear(self, cache: AudioCache):
        cache.put("a", "ja", b"\x01")
        cache.put("b", "ja", b"\x02")
        cache.clear()
        assert cache.get("a", "ja") is None
        assert cache.get("b", "ja") is None

    def test_cache_creates_directory(self, tmp_path: Path):
        nested = tmp_path / "deep" / "nested" / "cache"
        cache = AudioCache(cache_dir=str(nested))
        assert nested.exists()


# ---------------------------------------------------------------------------
# prepare_scenario_audio
# ---------------------------------------------------------------------------

class TestPrepareScenarioAudio:
    @pytest.fixture
    def gen(self, tmp_path: Path) -> AudioGenerator:
        return AudioGenerator(tts_provider="mock", cache_dir=str(tmp_path / "cache"))

    @pytest.mark.asyncio
    async def test_step_with_no_input_skipped(self, gen: AudioGenerator):
        """Step 1 has no user_input or user_audio -> should be skipped."""
        scenario = _make_scenario([
            TestStep(step=1, expected_block="greeting"),
        ])
        result = await gen.prepare_scenario_audio(scenario)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_step_with_text_input(self, gen: AudioGenerator):
        """Step with user_input -> should generate TTS."""
        scenario = _make_scenario([
            TestStep(step=2, user_input="はい、予約をお願いします"),
        ])
        result = await gen.prepare_scenario_audio(scenario)
        assert len(result) == 1

        audio = result[0]
        assert isinstance(audio, PreparedAudio)
        assert audio.step == 2
        assert audio.source == "tts"
        assert audio.text_reference == "はい、予約をお願いします"
        assert len(audio.mulaw_chunks) > 0
        assert all(len(c) == TWILIO_CHUNK_SIZE for c in audio.mulaw_chunks)
        assert audio.duration_ms > 0

    @pytest.mark.asyncio
    async def test_step_with_audio_file(self, gen: AudioGenerator, tmp_path: Path):
        """Step with user_audio -> should load from file."""
        wav_path = tmp_path / "response.wav"
        _make_wav_file(wav_path, sample_rate=8000, duration_s=1.0)

        scenario = _make_scenario([
            TestStep(step=3, user_audio=str(wav_path), user_input="text ref"),
        ])
        result = await gen.prepare_scenario_audio(scenario)
        assert len(result) == 1

        audio = result[0]
        assert audio.source == "file"
        assert audio.step == 3
        # user_audio takes priority, but text_reference uses user_input
        assert audio.text_reference == "text ref"
        assert len(audio.mulaw_chunks) > 0

    @pytest.mark.asyncio
    async def test_mixed_steps(self, gen: AudioGenerator, tmp_path: Path):
        """Full scenario: skip, TTS, file, TTS."""
        wav_path = tmp_path / "audio.wav"
        _make_wav_file(wav_path, sample_rate=8000, duration_s=0.5)

        scenario = _make_scenario([
            TestStep(step=1, expected_block="greeting"),  # skip
            TestStep(step=2, user_input="はい"),           # TTS
            TestStep(step=3, user_audio=str(wav_path)),    # file
            TestStep(step=4, user_input="ありがとう"),      # TTS
        ])
        result = await gen.prepare_scenario_audio(scenario)
        assert len(result) == 3  # step 1 skipped

        assert result[0].step == 2
        assert result[0].source == "tts"
        assert result[1].step == 3
        assert result[1].source == "file"
        assert result[2].step == 4
        assert result[2].source == "tts"

    @pytest.mark.asyncio
    async def test_example_scenario(self, gen: AudioGenerator):
        """Test with the actual example scenario from the project."""
        scenario_path = Path(__file__).parent.parent / "scenarios" / "example_scripted.yaml"
        if not scenario_path.exists():
            pytest.skip("Example scenario not found")

        scenario = TestScenario.from_yaml(scenario_path)
        result = await gen.prepare_scenario_audio(scenario)

        # Step 1 has no user_input -> skipped, steps 2-4 have user_input -> 3 prepared
        assert len(result) == 3
        assert all(isinstance(r, PreparedAudio) for r in result)
        assert all(len(c) == TWILIO_CHUNK_SIZE for r in result for c in r.mulaw_chunks)


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------

class TestResampling:
    @pytest.fixture
    def gen(self, tmp_path: Path) -> AudioGenerator:
        return AudioGenerator(tts_provider="mock", cache_dir=str(tmp_path / "cache"))

    def test_resample_24k_to_8k(self, gen: AudioGenerator):
        """24kHz PCM16 should resample to ~1/3 the samples."""
        num_samples_24k = 24000  # 1 second
        pcm_24k = struct.pack(f"<{num_samples_24k}h", *([0] * num_samples_24k))

        result = gen._resample_pcm16(pcm_24k, 24000, 8000)
        num_samples_8k = len(result) // 2
        assert 7990 <= num_samples_8k <= 8010

    def test_resample_preserves_format(self, gen: AudioGenerator):
        """Resampled output should be valid PCM16."""
        import math as m
        # Generate a sine wave at 24kHz
        num_samples = 2400  # 100ms
        samples = [int(16000 * m.sin(2 * m.pi * 440 * i / 24000)) for i in range(num_samples)]
        pcm = struct.pack(f"<{num_samples}h", *samples)

        result = gen._resample_pcm16(pcm, 24000, 8000)
        assert len(result) % 2 == 0
        decoded = struct.unpack(f"<{len(result) // 2}h", result)
        assert all(-32768 <= s <= 32767 for s in decoded)
