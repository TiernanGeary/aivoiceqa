# Agent C: VAD Engine

You are building the Voice Activity Detection engine for the voiceaiqa QA tool. This component detects when the agent stops speaking so the QA tool can respond at the right moment. It also captures latency measurements.

## Branch
`phase2/vad-engine` — cut from latest `main` after Phase 1 is merged.

## Git Workflow
- Cut branch: `git checkout -b phase2/vad-engine main`
- Commit incrementally with clear messages
- Do NOT merge to main — orchestrator handles merges

## Files You Own
```
core/
├── vad.py              # TurnDetector class with VAD + state machine
└── audio_utils.py      # mulaw↔PCM conversion, resampling utilities
```

## What You Build

### audio_utils.py — Audio format conversion
Utility functions for converting between audio formats:

```python
def mulaw_to_pcm16(mulaw_bytes: bytes) -> bytes:
    """Convert mulaw (8kHz) to PCM16 (8kHz).
    NOTE: audioop is deprecated in Python 3.11+ and removed in 3.13.
    Check Python version and use appropriate library.
    If Python >= 3.13, use soundfile or a pure-python alternative."""

def resample_8k_to_16k(pcm16_8k: bytes) -> bytes:
    """Resample PCM16 from 8kHz to 16kHz (required by silero-vad)."""

def pcm16_to_mulaw(pcm16_bytes: bytes, sample_rate: int = 8000) -> bytes:
    """Convert PCM16 to mulaw at 8kHz. Resample first if needed."""
```

### TurnDetector class (core/vad.py)
State machine that processes audio chunks and detects turn boundaries:

```python
class TurnState(Enum):
    IDLE = "idle"                        # Waiting for agent to speak
    AGENT_SPEAKING = "agent_speaking"    # Agent is talking, accumulating audio
    SILENCE_DETECTED = "silence"         # Silence threshold reached, turn ended

class TurnDetector:
    def __init__(self, silence_threshold_ms: int = 1500, min_speech_ms: int = 300):
        """
        silence_threshold_ms: How long silence must last to consider turn ended.
            Default 1500ms — conservative for Japanese business speech.
            QA tool has no benefit from responding fast. Better to wait and avoid
            cutting off the agent mid-sentence.

        min_speech_ms: Minimum speech duration to count as real speech.
            Filters background noise, clicks, coughs. Default 300ms.
        """

    def feed_audio(self, mulaw_chunk: bytes, timestamp: float) -> TurnEvent | None:
        """Feed a 20ms mulaw audio chunk from Twilio.
        Internally converts to PCM16 16kHz and runs silero-vad.
        Returns TurnEvent if a state transition happened, None otherwise."""

    def get_turn_audio(self) -> bytes:
        """Get accumulated audio for the current/just-ended turn (mulaw format).
        Call this after receiving a TURN_ENDED event."""

    def get_latency(self) -> float | None:
        """Get latency for the last completed exchange:
        time between our audio ending and agent audio starting.
        Returns None if no complete exchange yet."""

    def mark_our_audio_sent(self, timestamp: float) -> None:
        """Record when we finished playing our audio.
        Used to calculate agent response latency."""

    def reset(self) -> None:
        """Reset state for next turn."""
```

### TurnEvent
```python
@dataclass
class TurnEvent:
    type: str  # "speech_started", "turn_ended"
    timestamp: float
    duration_ms: float | None = None  # speech duration for turn_ended events
```

### State Machine Logic

```
IDLE:
  - Receive audio chunk
  - Run silero-vad: speech probability
  - If prob > 0.5 for min_speech_ms consecutive → transition to AGENT_SPEAKING
  - Record speech_start_timestamp (for latency calculation)
  - Return TurnEvent("speech_started")

AGENT_SPEAKING:
  - Accumulate audio chunks into turn buffer
  - Run silero-vad on each chunk
  - If prob < 0.5 → start silence counter
  - If silence counter reaches silence_threshold_ms → transition to SILENCE_DETECTED
  - If prob > 0.5 again before threshold → reset silence counter, stay SPEAKING

SILENCE_DETECTED:
  - Turn is over
  - Return TurnEvent("turn_ended", duration_ms=...)
  - Transition back to IDLE
  - Turn audio buffer is available via get_turn_audio()
```

### Latency Calculation
```
1. QA plays its audio response → calls mark_our_audio_sent(timestamp)
2. Agent starts speaking → speech_started event with timestamp
3. Latency = speech_started.timestamp - our_audio_sent_timestamp
```

Track per-turn latencies in a list for P50/P95 reporting.

## silero-vad Setup
- Install: `pip install silero-vad` or load via `torch.hub`
- Model expects: PCM16, 16kHz, mono
- Input: process in 30ms or 20ms frames (480 or 320 samples at 16kHz)
- Output: float 0.0-1.0 (speech probability)
- Threshold: 0.5 is standard

## Python Version Compatibility
Check if `audioop` is available:
```python
try:
    import audioop
except ImportError:
    # Python 3.13+, use alternative
    # Options: soundfile, pydub, or pure-python mulaw codec
```

## Dependencies
- `torch` (for silero-vad)
- `numpy` (for audio resampling)
- Standard lib: `audioop` (if available), `struct`

## Testing
Write tests in `tests/test_vad.py`:
- Test state transitions: IDLE → SPEAKING → SILENCE → IDLE
- Test silence threshold: verify turn doesn't end before threshold
- Test min_speech_duration: short noise bursts are filtered
- Test latency calculation
- Test audio format conversion (mulaw → PCM16 → 16kHz roundtrip)
- Use synthetic audio: generate silence + tone patterns for deterministic testing

## Acceptance Criteria
- [ ] TurnDetector correctly transitions through states
- [ ] 1500ms silence threshold works (configurable)
- [ ] Short noise (<300ms) is filtered out
- [ ] Turn audio buffer is accumulated and retrievable
- [ ] Latency calculation works correctly
- [ ] Audio conversion handles mulaw ↔ PCM16 ↔ 16kHz
- [ ] Works on current Python version (check audioop availability)
- [ ] All tests pass
