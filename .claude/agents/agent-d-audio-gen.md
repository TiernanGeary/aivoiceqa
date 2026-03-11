# Agent D: Audio Generation

You are building the audio generation component for the voiceaiqa QA tool. This component creates the "user" side audio — either generating TTS from text or loading pre-recorded audio files — and converts it to the mulaw format Twilio requires.

## Branch
`phase2/audio-gen` — cut from latest `main` after Phase 1 is merged.

## Git Workflow
- Cut branch: `git checkout -b phase2/audio-gen main`
- Commit incrementally with clear messages
- Do NOT merge to main — orchestrator handles merges

## Files You Own
```
core/
├── audio_gen.py        # AudioGenerator class — TTS + file loading + conversion
└── audio_cache.py      # Cache generated audio to avoid re-generating
```

## What You Build

### AudioGenerator class

```python
class AudioGenerator:
    def __init__(self, tts_provider: str = "cartesia", cache_dir: str = "cache/audio"):
        """
        tts_provider: "cartesia" (default, same as reco) or "mock" for testing
        cache_dir: directory to cache generated audio files
        """

    async def prepare_scenario_audio(self, scenario: TestScenario) -> list[PreparedAudio]:
        """Pre-generate all audio for a scenario's steps before the call starts.
        For each step:
          - If step.user_audio exists → load from file
          - Elif step.user_input exists → generate TTS
          - Else → no audio (agent speaks first)
        Returns list of PreparedAudio ready to send to Twilio."""

    async def generate_tts(self, text: str, language: str = "ja") -> bytes:
        """Generate Japanese TTS audio via Cartesia API.
        Returns raw PCM audio."""

    def load_audio_file(self, path: str) -> bytes:
        """Load a WAV/MP3 audio file from disk.
        Returns raw PCM audio."""

    def convert_to_twilio(self, pcm_audio: bytes, source_sample_rate: int) -> bytes:
        """Convert PCM audio to Twilio's mulaw 8kHz format.
        Steps:
          1. Resample to 8kHz if needed
          2. Convert PCM16 → mulaw
          3. Chunk into 160-byte frames (20ms at 8kHz)
        Returns mulaw bytes ready for Twilio Media Streams."""

    def chunk_for_twilio(self, mulaw_audio: bytes) -> list[bytes]:
        """Split mulaw audio into exactly 160-byte chunks (20ms frames).
        Pad last chunk with silence if needed."""
```

### PreparedAudio
```python
@dataclass
class PreparedAudio:
    step: int
    text_reference: str          # The text (for evaluation reference)
    mulaw_chunks: list[bytes]    # 160-byte mulaw chunks ready for Twilio
    duration_ms: float           # Total audio duration
    source: str                  # "tts" or "file"
```

### Audio Cache (audio_cache.py)
Avoid re-generating TTS for the same text:

```python
class AudioCache:
    def __init__(self, cache_dir: str = "cache/audio"):
        ...

    def get(self, text: str, language: str) -> bytes | None:
        """Check if audio for this text is cached. Return PCM bytes or None."""

    def put(self, text: str, language: str, audio: bytes) -> None:
        """Cache generated audio. Key is hash of text+language."""
```

Cache key: SHA256 of `text + language + provider`. Store as `.wav` files.

### Cartesia TTS Integration
- Use Cartesia's REST API for Japanese TTS
- Select an appropriate Japanese voice ID
- Output format: request PCM16 if available, otherwise WAV and extract PCM
- Handle API errors with retry (max 3 attempts)

### Mock mode
When `tts_provider="mock"`, generate a simple sine wave tone of appropriate duration instead of calling Cartesia. This allows full pipeline testing without API keys.

## Audio Format Pipeline
```
Cartesia TTS → PCM16 (likely 24kHz) → resample to 8kHz → mulaw → 160-byte chunks
     OR
WAV file → load PCM → resample to 8kHz → mulaw → 160-byte chunks
```

Use `core/audio_utils.py` from Agent C for mulaw conversion if available. If not, implement conversion locally — it can be shared later.

## Configuration
Read from `config/settings.py`:
- `CARTESIA_API_KEY`
- `CARTESIA_VOICE_ID` (Japanese voice)
- `AUDIO_CACHE_DIR` (default "cache/audio")

## Dependencies
- `httpx` for Cartesia API calls
- `numpy` for audio resampling
- `soundfile` or `wave` for loading audio files
- Standard lib: `audioop` (if available), `hashlib` for cache keys

## Testing
Write tests in `tests/test_audio_gen.py`:
- Test mock TTS generates valid audio
- Test audio file loading (include a small test WAV file in `tests/fixtures/`)
- Test PCM → mulaw conversion
- Test chunking produces exactly 160-byte chunks
- Test cache hit/miss behavior
- Test prepare_scenario_audio handles all three step types (no input, text input, audio file)

## Acceptance Criteria
- [ ] TTS generation works via Cartesia API (or mock mode)
- [ ] Audio files load correctly (WAV format)
- [ ] Audio converts to mulaw 8kHz correctly
- [ ] Chunking produces exactly 160-byte frames (20ms)
- [ ] Cache prevents redundant TTS generation
- [ ] prepare_scenario_audio handles all step types
- [ ] Mock mode works without API keys
- [ ] All tests pass
