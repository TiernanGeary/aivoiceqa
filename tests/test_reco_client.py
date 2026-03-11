"""Tests for reco API client (mock mode).

Uses asyncio.get_event_loop().run_until_complete() for async tests to avoid
dependency on a specific pytest-asyncio version.
"""

import asyncio

import pytest

from reco.client import (
    CallStartResult,
    ConversationData,
    RecoClient,
    RecoClientError,
    parse_transcript_turns,
)
from reco.mock_data import (
    MOCK_CALL_ID,
    MOCK_CONVERSATION_ID,
    MOCK_RECORDING_URL,
    MOCK_TRANSCRIPT,
)


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def mock_client() -> RecoClient:
    """A RecoClient in mock mode -- no network access needed."""
    return RecoClient(base_url="http://localhost:3010", token="fake", mock=True)


class TestMockStartCall:
    def test_returns_call_start_result(self, mock_client: RecoClient):
        result = _run(mock_client.start_call(
            phone="+819012345678",
            customer_id="cust_001",
            flow_path="booking/happy_path",
            metadata={"qa_test": True, "scenario_id": "test_1"},
        ))
        assert isinstance(result, CallStartResult)
        assert result.call_id == MOCK_CALL_ID
        assert result.conversation_id == MOCK_CONVERSATION_ID

    def test_metadata_is_optional(self, mock_client: RecoClient):
        result = _run(mock_client.start_call(
            phone="+819012345678",
            customer_id="cust_001",
            flow_path="booking/happy_path",
        ))
        assert result.call_id == MOCK_CALL_ID


class TestMockPollStatus:
    def test_returns_completed(self, mock_client: RecoClient):
        status = _run(mock_client.poll_status(MOCK_CALL_ID))
        assert status == "completed"

    def test_poll_with_custom_timeout(self, mock_client: RecoClient):
        status = _run(mock_client.poll_status(
            MOCK_CALL_ID, timeout=5, poll_interval=0.1
        ))
        assert status == "completed"


class TestMockGetConversation:
    def test_returns_conversation_data(self, mock_client: RecoClient):
        conv = _run(mock_client.get_conversation(MOCK_CONVERSATION_ID))
        assert isinstance(conv, ConversationData)
        assert conv.id == MOCK_CONVERSATION_ID
        assert conv.call_status == "success"
        assert conv.duration_seconds == 65
        assert conv.flow_path == "booking/happy_path"

    def test_conversation_has_required_fields(self, mock_client: RecoClient):
        conv = _run(mock_client.get_conversation(MOCK_CONVERSATION_ID))
        assert conv.phone_number
        assert conv.customer_id
        assert conv.created_at


class TestMockGetTranscript:
    def test_returns_transcript_string(self, mock_client: RecoClient):
        transcript = _run(mock_client.get_transcript(MOCK_CONVERSATION_ID))
        assert isinstance(transcript, str)
        assert len(transcript) > 0
        assert "ASSISTANT:" in transcript
        assert "USER:" in transcript

    def test_transcript_is_japanese(self, mock_client: RecoClient):
        transcript = _run(mock_client.get_transcript(MOCK_CONVERSATION_ID))
        # Check for Japanese characters (hiragana/katakana/kanji ranges)
        assert any("\u3040" <= ch <= "\u9fff" for ch in transcript)


class TestMockGetRecordingUrl:
    def test_returns_url_string(self, mock_client: RecoClient):
        url = _run(mock_client.get_recording_url(MOCK_CONVERSATION_ID))
        assert isinstance(url, str)
        assert url == MOCK_RECORDING_URL
        assert url.startswith("https://")


class TestGetFinalBlockId:
    def test_returns_none_placeholder(self, mock_client: RecoClient):
        result = _run(mock_client.get_final_block_id(MOCK_CONVERSATION_ID))
        assert result is None


class TestParseTranscriptTurns:
    def test_parse_mock_transcript(self):
        turns = parse_transcript_turns(MOCK_TRANSCRIPT)
        assert len(turns) == 7
        assert turns[0]["role"] == "ASSISTANT"
        assert turns[1]["role"] == "USER"

    def test_all_turns_have_text(self):
        turns = parse_transcript_turns(MOCK_TRANSCRIPT)
        for turn in turns:
            assert "role" in turn
            assert "text" in turn
            assert len(turn["text"]) > 0

    def test_alternating_roles(self):
        turns = parse_transcript_turns(MOCK_TRANSCRIPT)
        for i in range(len(turns) - 1):
            assert turns[i]["role"] != turns[i + 1]["role"], (
                f"Turn {i} and {i+1} have the same role: {turns[i]['role']}"
            )

    def test_empty_string(self):
        turns = parse_transcript_turns("")
        assert turns == []

    def test_single_line(self):
        turns = parse_transcript_turns("ASSISTANT: こんにちは")
        assert len(turns) == 1
        assert turns[0]["role"] == "ASSISTANT"
        assert turns[0]["text"] == "こんにちは"


class TestMockClientClose:
    def test_close_is_safe(self, mock_client: RecoClient):
        """Closing a mock client (no real HTTP client) should not error."""
        _run(mock_client.close())


class TestPollStatusTimeout:
    def test_real_client_would_timeout(self):
        """Verify that poll_status raises on timeout.

        Uses a mock httpx transport that always returns 'running',
        ensuring the poll loop expires and raises RecoClientError.
        """
        import httpx

        async def _always_running(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "running"})

        transport = httpx.MockTransport(_always_running)

        async def _do_test():
            client = RecoClient(base_url="http://fake", token="t", mock=False)
            client._client = httpx.AsyncClient(
                transport=transport, base_url="http://fake"
            )
            try:
                with pytest.raises(RecoClientError, match="timed out"):
                    await client.poll_status(
                        "call_123", timeout=0.5, poll_interval=0.1
                    )
            finally:
                await client.close()

        _run(_do_test())
