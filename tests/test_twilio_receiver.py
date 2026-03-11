"""Tests for Twilio receiver, server endpoints, and audio utilities."""

from __future__ import annotations

import asyncio
import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from receivers.base import ActiveCall
from receivers.twilio_receiver import (
    MULAW_FRAME_SIZE,
    PendingTest,
    TwilioReceiver,
    chunk_audio,
    encode_media_payload,
)
from server import app, build_twiml


# ---------------------------------------------------------------------------
# TwiML / Server tests
# ---------------------------------------------------------------------------


class TestTwiML:
    """Test TwiML generation and the /incoming endpoint."""

    def test_build_twiml_contains_stream(self) -> None:
        twiml = build_twiml("example.ngrok.io")
        assert "<Response>" in twiml
        assert "<Connect>" in twiml
        assert "<Stream" in twiml
        assert "wss://example.ngrok.io/media-stream" in twiml

    def test_build_twiml_is_valid_xml_ish(self) -> None:
        twiml = build_twiml("test.example.com")
        assert twiml.startswith('<?xml version="1.0"')
        assert twiml.endswith("</Response>")

    def test_incoming_endpoint_returns_xml(self) -> None:
        client = TestClient(app)
        resp = client.post("/incoming")
        assert resp.status_code == 200
        assert "application/xml" in resp.headers["content-type"]
        assert "<Response>" in resp.text
        assert "<Stream" in resp.text


# ---------------------------------------------------------------------------
# Audio chunking tests
# ---------------------------------------------------------------------------


class TestAudioChunking:
    """Test audio frame chunking to 160-byte boundaries."""

    def test_chunk_exact_multiple(self) -> None:
        audio = bytes(range(256)) * 10  # 2560 bytes
        audio = audio[: MULAW_FRAME_SIZE * 5]  # exactly 800 bytes = 5 frames
        chunks = chunk_audio(audio)
        assert len(chunks) == 5
        assert all(len(c) == MULAW_FRAME_SIZE for c in chunks)

    def test_chunk_non_exact_multiple(self) -> None:
        audio = b"\x00" * 250  # 1 full frame + 90 bytes remainder
        chunks = chunk_audio(audio)
        assert len(chunks) == 2
        assert len(chunks[0]) == MULAW_FRAME_SIZE
        assert len(chunks[1]) == 90

    def test_chunk_smaller_than_frame(self) -> None:
        audio = b"\x00" * 50
        chunks = chunk_audio(audio)
        assert len(chunks) == 1
        assert len(chunks[0]) == 50

    def test_chunk_empty(self) -> None:
        chunks = chunk_audio(b"")
        assert chunks == []

    def test_chunk_single_frame(self) -> None:
        audio = b"\xff" * MULAW_FRAME_SIZE
        chunks = chunk_audio(audio)
        assert len(chunks) == 1
        assert chunks[0] == audio


# ---------------------------------------------------------------------------
# Base64 encode/decode roundtrip
# ---------------------------------------------------------------------------


class TestBase64Roundtrip:
    """Test base64 encoding/decoding of audio payloads."""

    def test_roundtrip_preserves_data(self) -> None:
        original = bytes(range(160))
        encoded = base64.b64encode(original).decode("ascii")
        decoded = base64.b64decode(encoded)
        assert decoded == original

    def test_encode_media_payload_format(self) -> None:
        chunk = b"\x80" * MULAW_FRAME_SIZE
        stream_sid = "MZ_test_stream_123"
        msg_str = encode_media_payload(chunk, stream_sid)
        msg = json.loads(msg_str)

        assert msg["event"] == "media"
        assert msg["streamSid"] == stream_sid
        assert "payload" in msg["media"]

        # Verify payload decodes back to original
        decoded = base64.b64decode(msg["media"]["payload"])
        assert decoded == chunk

    def test_encode_media_payload_small_chunk(self) -> None:
        chunk = b"\x00" * 10
        msg = json.loads(encode_media_payload(chunk, "sid"))
        decoded = base64.b64decode(msg["media"]["payload"])
        assert decoded == chunk


# ---------------------------------------------------------------------------
# Pending test registry
# ---------------------------------------------------------------------------


class TestPendingTestRegistry:
    """Test pending test registration, lookup, and consumption."""

    def test_register_and_lookup(self) -> None:
        rx = TwilioReceiver()
        rx.register_pending_test("+81-90-1234-5678", "scenario_001")

        result = rx.lookup_pending_test("+819012345678")
        assert result is not None
        assert result.scenario_id == "scenario_001"

    def test_lookup_missing_returns_none(self) -> None:
        rx = TwilioReceiver()
        assert rx.lookup_pending_test("+15551234567") is None

    def test_consume_removes_entry(self) -> None:
        rx = TwilioReceiver()
        rx.register_pending_test("+819012345678", "scenario_002")

        consumed = rx.consume_pending_test("+819012345678")
        assert consumed is not None
        assert consumed.scenario_id == "scenario_002"

        # Should be gone now
        assert rx.lookup_pending_test("+819012345678") is None

    def test_consume_missing_returns_none(self) -> None:
        rx = TwilioReceiver()
        assert rx.consume_pending_test("+15551234567") is None

    def test_register_overwrites_previous(self) -> None:
        rx = TwilioReceiver()
        rx.register_pending_test("+819012345678", "old")
        rx.register_pending_test("+819012345678", "new")

        result = rx.lookup_pending_test("+819012345678")
        assert result is not None
        assert result.scenario_id == "new"

    def test_phone_normalization(self) -> None:
        rx = TwilioReceiver()
        rx.register_pending_test("+81 (90) 1234-5678", "test")

        # All these should resolve to the same number
        assert rx.lookup_pending_test("+819012345678") is not None
        assert rx.lookup_pending_test("+81-90-1234-5678") is not None


# ---------------------------------------------------------------------------
# TwilioReceiver async behavior
# ---------------------------------------------------------------------------


class TestTwilioReceiverAsync:
    """Test async call lifecycle methods.

    Uses _run helper to execute coroutines since the installed
    pytest-asyncio version is too old for auto mode.
    """

    @staticmethod
    def _run(coro):
        """Run a coroutine in a fresh event loop."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_wait_for_call_success(self) -> None:
        async def _test():
            rx = TwilioReceiver()
            call = ActiveCall(
                call_sid="CA_test",
                stream_sid="MZ_test",
                websocket=None,
                started_at=time.time(),
            )

            async def arrive() -> None:
                await asyncio.sleep(0.05)
                rx.register_active_call(call)

            asyncio.create_task(arrive())
            result = await rx.wait_for_call(timeout=2.0)
            assert result.call_sid == "CA_test"
            assert result.stream_sid == "MZ_test"

        self._run(_test())

    def test_wait_for_call_timeout(self) -> None:
        async def _test():
            rx = TwilioReceiver()
            with pytest.raises(TimeoutError):
                await rx.wait_for_call(timeout=0.1)

        self._run(_test())

    def test_get_audio_chunk_returns_data(self) -> None:
        async def _test():
            rx = TwilioReceiver()
            call = ActiveCall(
                call_sid="CA_test",
                stream_sid="MZ_test",
                websocket=None,
                started_at=time.time(),
            )
            audio = b"\x80" * 160
            await call.audio_queue.put(audio)
            chunk = await rx.get_audio_chunk(call)
            assert chunk == audio

        self._run(_test())

    def test_get_audio_chunk_returns_none_on_sentinel(self) -> None:
        async def _test():
            rx = TwilioReceiver()
            call = ActiveCall(
                call_sid="CA_test",
                stream_sid="MZ_test",
                websocket=None,
                started_at=time.time(),
            )
            await call.audio_queue.put(b"")
            chunk = await rx.get_audio_chunk(call)
            assert chunk is None

        self._run(_test())

    def test_get_audio_chunk_returns_none_on_timeout(self) -> None:
        async def _test():
            rx = TwilioReceiver()
            call = ActiveCall(
                call_sid="CA_test",
                stream_sid="MZ_test",
                websocket=None,
                started_at=time.time(),
            )
            chunk = await rx.get_audio_chunk(call)
            assert chunk is None

        self._run(_test())

    def test_handle_media_message_queues_audio(self) -> None:
        async def _test():
            rx = TwilioReceiver()
            call = ActiveCall(
                call_sid="CA_test",
                stream_sid="MZ_test",
                websocket=None,
                started_at=time.time(),
            )
            audio = b"\x80" * 160
            payload = base64.b64encode(audio).decode("ascii")
            data = {"event": "media", "media": {"payload": payload}}
            await rx.handle_media_message(call, data)
            chunk = call.audio_queue.get_nowait()
            assert chunk == audio

        self._run(_test())

    def test_handle_stop_message_sends_sentinel(self) -> None:
        async def _test():
            rx = TwilioReceiver()
            call = ActiveCall(
                call_sid="CA_test",
                stream_sid="MZ_test",
                websocket=None,
                started_at=time.time(),
            )
            await rx.handle_media_message(call, {"event": "stop"})
            sentinel = call.audio_queue.get_nowait()
            assert sentinel == b""

        self._run(_test())

    def test_send_audio_chunks_correctly(self) -> None:
        """Verify send_audio sends properly chunked base64 messages."""
        async def _test():
            sent_messages: list[str] = []

            class FakeWebSocket:
                async def send_text(self, msg: str) -> None:
                    sent_messages.append(msg)

            rx = TwilioReceiver()
            call = ActiveCall(
                call_sid="CA_test",
                stream_sid="MZ_test",
                websocket=FakeWebSocket(),
                started_at=time.time(),
            )

            audio = b"\x7f" * 320
            await rx.send_audio(call, audio)

            assert len(sent_messages) == 2
            for msg_str in sent_messages:
                msg = json.loads(msg_str)
                assert msg["event"] == "media"
                assert msg["streamSid"] == "MZ_test"
                decoded = base64.b64decode(msg["media"]["payload"])
                assert len(decoded) == MULAW_FRAME_SIZE

        self._run(_test())

    def test_clear_audio_sends_clear_event(self) -> None:
        async def _test():
            sent_messages: list[str] = []

            class FakeWebSocket:
                async def send_text(self, msg: str) -> None:
                    sent_messages.append(msg)

            rx = TwilioReceiver()
            call = ActiveCall(
                call_sid="CA_test",
                stream_sid="MZ_test",
                websocket=FakeWebSocket(),
                started_at=time.time(),
            )

            await rx.clear_audio(call)

            assert len(sent_messages) == 1
            msg = json.loads(sent_messages[0])
            assert msg["event"] == "clear"
            assert msg["streamSid"] == "MZ_test"

        self._run(_test())


# ---------------------------------------------------------------------------
# WebSocket integration test (using TestClient)
# ---------------------------------------------------------------------------


class TestWebSocketHandler:
    """Test the /media-stream WebSocket endpoint."""

    def test_websocket_start_and_media(self) -> None:
        client = TestClient(app)

        with client.websocket_connect("/media-stream") as ws:
            # Send connected event
            ws.send_json({"event": "connected", "protocol": "Call"})

            # Send start event
            ws.send_json(
                {
                    "event": "start",
                    "streamSid": "MZ_ws_test",
                    "start": {"callSid": "CA_ws_test"},
                }
            )

            # Send a media event
            audio = b"\x80" * 160
            payload = base64.b64encode(audio).decode("ascii")
            ws.send_json(
                {
                    "event": "media",
                    "media": {"payload": payload},
                }
            )

            # Send stop event
            ws.send_json({"event": "stop"})

        # Verify call was registered
        from server import receiver as srv_receiver

        call = srv_receiver._active_calls.get("CA_ws_test")
        assert call is not None
        assert call.stream_sid == "MZ_ws_test"
        assert call.call_sid == "CA_ws_test"
