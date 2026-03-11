# Agent B: Twilio Receiver

You are building the Twilio call receiver for the voiceaiqa QA tool. This component receives inbound phone calls from the agent being tested and manages bidirectional audio streaming via Twilio Media Streams.

## Branch
`phase2/twilio-receiver` — cut from latest `main` after Phase 1 is merged.

## Git Workflow
- Cut branch: `git checkout -b phase2/twilio-receiver main`
- Commit incrementally with clear messages
- Do NOT merge to main — orchestrator handles merges

## Files You Own
```
receivers/
├── __init__.py
├── base.py                 # CallReceiver abstract base class
└── twilio_receiver.py      # TwilioReceiver implementation
server.py                   # FastAPI app with webhook + WebSocket endpoints
```

## What You Build

### server.py — FastAPI application
Two endpoints:

1. **POST /incoming** — Twilio webhook when a call arrives
   - Returns TwiML that connects the call to a Media Stream:
     ```xml
     <Response>
       <Connect>
         <Stream url="wss://{webhook_url}/media-stream" />
       </Connect>
     </Response>
     ```
   - Must look up the pending test scenario for this call

2. **WebSocket /media-stream** — Twilio Media Streams bidirectional audio
   - Receives JSON messages with events: `connected`, `start`, `media`, `stop`
   - `media` events contain base64-encoded mulaw audio (8kHz, mono, 20ms frames)
   - Must capture `streamSid` from `start` event (needed for sending audio and `clear` events)
   - Delegates to `TwilioReceiver` for audio handling

### CallReceiver base class (receivers/base.py)
Abstract interface for receiving calls (pluggable for future SIP/WebRTC):

```python
from abc import ABC, abstractmethod

class CallReceiver(ABC):
    @abstractmethod
    async def wait_for_call(self, timeout: float = 30) -> ActiveCall:
        """Block until a call arrives. Raise TimeoutError if no call within timeout."""

    @abstractmethod
    async def get_audio_chunk(self, call: ActiveCall) -> bytes | None:
        """Get next audio chunk from the call. Returns None if call ended."""

    @abstractmethod
    async def send_audio(self, call: ActiveCall, audio: bytes) -> None:
        """Send audio into the call (mulaw 8kHz, 20ms frames)."""

    @abstractmethod
    async def clear_audio(self, call: ActiveCall) -> None:
        """Stop any currently playing audio."""

    @abstractmethod
    async def hangup(self, call: ActiveCall) -> None:
        """End the call."""
```

### TwilioReceiver (receivers/twilio_receiver.py)
Implements `CallReceiver` using Twilio Media Streams:

- **Audio receive**: Decode base64 mulaw from `media` events, push to an asyncio Queue
- **Audio send**: Encode audio as base64 mulaw, send as JSON over WebSocket. MUST chunk to exactly 20ms frames (160 bytes of mulaw at 8kHz). Send chunks at ~20ms intervals to avoid audio glitches.
- **Clear**: Send `{"event": "clear", "streamSid": "..."}` to stop playback
- **Hangup**: Use Twilio REST API to end the call via `call_sid`
- **Pending test registry**: In-memory dict mapping phone numbers to pending scenarios. The scenario runner registers a pending test before triggering the call.

### ActiveCall data class
```python
@dataclass
class ActiveCall:
    call_sid: str
    stream_sid: str
    websocket: WebSocket
    started_at: float
    audio_queue: asyncio.Queue  # inbound audio chunks
```

## Audio Format Details
- Twilio sends/receives: **mulaw, 8kHz, mono**
- Each `media` event payload is base64-encoded
- Decode: `base64.b64decode(payload)` → raw mulaw bytes
- When sending, chunk audio to exactly **160 bytes** per message (20ms at 8kHz mulaw)
- Send format: `{"event": "media", "streamSid": "...", "media": {"payload": "<base64>"}}`

## Important: asyncio architecture
The WebSocket receive loop MUST NOT block. Use this pattern:
- Tight `await ws.receive_json()` loop that pushes to `audio_queue`
- Separate asyncio tasks for VAD processing and audio playback
- The WebSocket handler should only do: receive → queue → continue

## Configuration
Read from `config/settings.py`:
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_PHONE_NUMBER`
- `QA_SERVER_PORT` (default 8050)

## Dependencies
- `fastapi` + `uvicorn` for the web server
- `twilio` SDK for REST API calls (hangup)
- `starlette.websockets` for WebSocket handling

## Testing
Write tests in `tests/test_twilio_receiver.py`:
- Test TwiML response format
- Test audio chunking (verify 160-byte chunks)
- Test base64 encode/decode roundtrip
- Test pending test registry (register, lookup, consume)

## Acceptance Criteria
- [ ] FastAPI server starts on configured port
- [ ] /incoming webhook returns valid TwiML with Stream
- [ ] WebSocket handler receives media events and queues audio
- [ ] Audio can be sent back in correct format (20ms mulaw chunks)
- [ ] `clear` event stops playback
- [ ] Pending test registry works (register before call, lookup on arrival)
- [ ] `streamSid` is captured and stored on ActiveCall
- [ ] All tests pass
