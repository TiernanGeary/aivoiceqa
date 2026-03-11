"""Twilio Media Streams implementation of CallReceiver."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field

from receivers.base import ActiveCall, CallReceiver

logger = logging.getLogger(__name__)

# Mulaw 8kHz mono: 160 bytes = 20ms frame
MULAW_FRAME_SIZE = 160
FRAME_DURATION_S = 0.020  # 20ms


@dataclass
class PendingTest:
    """A test scenario waiting for a call to arrive."""

    scenario_id: str
    phone_number: str
    registered_at: float = field(default_factory=time.time)


class TwilioReceiver(CallReceiver):
    """Receives calls via Twilio Media Streams WebSocket.

    Flow:
    1. Scenario runner registers a pending test (phone_number -> scenario_id)
    2. Reco outbound call triggers Twilio to hit /incoming webhook
    3. TwiML connects call to /media-stream WebSocket
    4. WebSocket handler captures stream_sid and routes audio to queue
    """

    def __init__(self) -> None:
        # Pending tests: phone_number -> PendingTest
        self._pending: dict[str, PendingTest] = {}
        # Active calls: call_sid -> ActiveCall
        self._active_calls: dict[str, ActiveCall] = {}
        # Event fired when a new call arrives
        self._call_arrived: asyncio.Event = asyncio.Event()
        self._latest_call: ActiveCall | None = None

    # --- Pending test registry ---

    def register_pending_test(self, phone_number: str, scenario_id: str) -> None:
        """Register a pending test for a phone number.

        Called by scenario runner before triggering the outbound call.
        """
        normalized = self._normalize_phone(phone_number)
        self._pending[normalized] = PendingTest(
            scenario_id=scenario_id,
            phone_number=normalized,
        )
        logger.info("Registered pending test: %s -> %s", normalized, scenario_id)

    def lookup_pending_test(self, phone_number: str) -> PendingTest | None:
        """Look up a pending test for a phone number without consuming it."""
        normalized = self._normalize_phone(phone_number)
        return self._pending.get(normalized)

    def consume_pending_test(self, phone_number: str) -> PendingTest | None:
        """Look up and remove a pending test for a phone number."""
        normalized = self._normalize_phone(phone_number)
        return self._pending.pop(normalized, None)

    @staticmethod
    def _normalize_phone(number: str) -> str:
        """Normalize phone number by stripping non-digit chars except leading +."""
        if number.startswith("+"):
            return "+" + "".join(c for c in number[1:] if c.isdigit())
        return "".join(c for c in number if c.isdigit())

    # --- Call lifecycle ---

    def register_active_call(self, call: ActiveCall) -> None:
        """Register a new active call (called by WebSocket handler)."""
        self._active_calls[call.call_sid] = call
        self._latest_call = call
        self._call_arrived.set()

    async def wait_for_call(self, timeout: float = 30) -> ActiveCall:
        """Block until a call arrives. Raise TimeoutError if no call within timeout."""
        self._call_arrived.clear()
        try:
            await asyncio.wait_for(self._call_arrived.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"No call arrived within {timeout}s")

        assert self._latest_call is not None
        return self._latest_call

    async def get_audio_chunk(self, call: ActiveCall) -> bytes | None:
        """Get next audio chunk from the call queue.

        Returns None if a sentinel (empty bytes) is received, indicating call ended.
        """
        try:
            chunk = await asyncio.wait_for(call.audio_queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return None

        if chunk == b"":
            return None
        return chunk

    async def send_audio(self, call: ActiveCall, audio: bytes) -> None:
        """Send audio into the call as 20ms mulaw frames over WebSocket.

        Audio is chunked to exactly 160 bytes per message. Chunks are sent
        at ~20ms intervals to avoid audio glitches.
        """
        for i in range(0, len(audio), MULAW_FRAME_SIZE):
            chunk = audio[i : i + MULAW_FRAME_SIZE]
            payload = base64.b64encode(chunk).decode("ascii")
            message = json.dumps(
                {
                    "event": "media",
                    "streamSid": call.stream_sid,
                    "media": {"payload": payload},
                }
            )
            await call.websocket.send_text(message)

            # Pace at ~20ms per frame to avoid overwhelming the audio buffer
            if i + MULAW_FRAME_SIZE < len(audio):
                await asyncio.sleep(FRAME_DURATION_S)

    async def clear_audio(self, call: ActiveCall) -> None:
        """Send clear event to stop any currently playing audio."""
        message = json.dumps(
            {
                "event": "clear",
                "streamSid": call.stream_sid,
            }
        )
        await call.websocket.send_text(message)

    async def hangup(self, call: ActiveCall) -> None:
        """End the call via Twilio REST API."""
        from config.settings import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN

        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            logger.warning("Twilio credentials not set, cannot hangup via REST API")
            # Signal call ended
            await call.audio_queue.put(b"")
            return

        try:
            from twilio.rest import Client  # type: ignore[import-untyped]

            client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            client.calls(call.call_sid).update(status="completed")
        except ImportError:
            logger.warning("twilio package not installed, cannot hangup via REST API")
        except Exception as e:
            logger.error("Failed to hangup call %s: %s", call.call_sid, e)

        # Signal call ended
        await call.audio_queue.put(b"")

    # --- WebSocket media handling ---

    async def handle_media_message(self, call: ActiveCall, data: dict) -> None:
        """Handle a single media event from Twilio WebSocket.

        Decodes base64 mulaw audio and pushes to the call's audio queue.
        """
        event = data.get("event")

        if event == "media":
            payload = data.get("media", {}).get("payload", "")
            if payload:
                audio_bytes = base64.b64decode(payload)
                await call.audio_queue.put(audio_bytes)

        elif event == "stop":
            logger.info("Stream stopped for call %s", call.call_sid)
            await call.audio_queue.put(b"")  # Sentinel: call ended


def chunk_audio(audio: bytes, frame_size: int = MULAW_FRAME_SIZE) -> list[bytes]:
    """Split audio bytes into fixed-size chunks.

    Last chunk may be smaller than frame_size if audio length
    is not evenly divisible.
    """
    return [audio[i : i + frame_size] for i in range(0, len(audio), frame_size)]


def encode_media_payload(audio_chunk: bytes, stream_sid: str) -> str:
    """Encode an audio chunk as a Twilio Media Stream JSON message."""
    payload = base64.b64encode(audio_chunk).decode("ascii")
    return json.dumps(
        {
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": payload},
        }
    )
