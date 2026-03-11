"""Reco API client for triggering calls and fetching post-call data."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from reco.mock_data import (
    MOCK_CALL_ID,
    MOCK_CONVERSATION_DATA,
    MOCK_CONVERSATION_ID,
    MOCK_RECORDING_URL,
    MOCK_TRANSCRIPT,
)


@dataclass
class CallStartResult:
    """Result of triggering an outbound call."""
    call_id: str
    conversation_id: int


@dataclass
class ConversationData:
    """Conversation metadata from reco API."""
    id: int
    call_status: str          # success / failure / no_answer / potential
    duration_seconds: int
    phone_number: str
    flow_path: str
    customer_id: str
    created_at: str


class RecoClientError(Exception):
    """Raised when a reco API call fails."""


class RecoClient:
    """Async HTTP client for the reco REST API.

    When mock=True, returns realistic fake data without network access.
    """

    def __init__(self, base_url: str, token: str, mock: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.mock = mock
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # --- Public API ---

    async def start_call(
        self,
        phone: str,
        customer_id: str,
        flow_path: str,
        metadata: dict | None = None,
    ) -> CallStartResult:
        """POST /api/calls/start -- trigger an outbound call.

        Returns call_id and conversation_id. Tags QA calls via metadata.
        """
        if self.mock:
            return CallStartResult(
                call_id=MOCK_CALL_ID,
                conversation_id=MOCK_CONVERSATION_ID,
            )

        client = await self._get_client()
        body: dict = {
            "phone": phone,
            "customer_id": customer_id,
            "flow_path": flow_path,
        }
        if metadata:
            body["metadata"] = metadata

        resp = await client.post("/api/calls/start", json=body)
        if resp.status_code != 200:
            raise RecoClientError(
                f"start_call failed: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        return CallStartResult(
            call_id=data["call_id"],
            conversation_id=data["conversation_id"],
        )

    async def poll_status(
        self,
        call_id: str,
        timeout: float = 60,
        poll_interval: float = 2,
    ) -> str:
        """GET /api/calls/status?call_id=... -- poll until completed/failed.

        Returns final status string. Raises RecoClientError on timeout.
        """
        if self.mock:
            # Simulate a brief wait then return completed
            await asyncio.sleep(0.01)
            return "completed"

        client = await self._get_client()
        elapsed = 0.0
        while elapsed < timeout:
            resp = await client.get("/api/calls/status", params={"call_id": call_id})
            if resp.status_code != 200:
                raise RecoClientError(
                    f"poll_status failed: {resp.status_code} {resp.text}"
                )
            status = resp.json().get("status", "unknown")
            if status in ("completed", "failed", "stopped"):
                return status
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise RecoClientError(
            f"poll_status timed out after {timeout}s for call_id={call_id}"
        )

    async def get_conversation(self, conversation_id: int) -> ConversationData:
        """GET /conversations/{id} -- fetch conversation metadata."""
        if self.mock:
            d = MOCK_CONVERSATION_DATA
            return ConversationData(
                id=d["id"],
                call_status=d["call_status"],
                duration_seconds=d["duration_seconds"],
                phone_number=d["phone_number"],
                flow_path=d["flow_path"],
                customer_id=d["customer_id"],
                created_at=d["created_at"],
            )

        client = await self._get_client()
        resp = await client.get(f"/conversations/{conversation_id}")
        if resp.status_code != 200:
            raise RecoClientError(
                f"get_conversation failed: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        return ConversationData(
            id=data["id"],
            call_status=data["call_status"],
            duration_seconds=data["duration_seconds"],
            phone_number=data["phone_number"],
            flow_path=data["flow_path"],
            customer_id=data["customer_id"],
            created_at=data["created_at"],
        )

    async def get_transcript(self, conversation_id: int) -> str:
        """GET /conversations/{id}/transcript -- full transcript text."""
        if self.mock:
            return MOCK_TRANSCRIPT

        client = await self._get_client()
        resp = await client.get(f"/conversations/{conversation_id}/transcript")
        if resp.status_code != 200:
            raise RecoClientError(
                f"get_transcript failed: {resp.status_code} {resp.text}"
            )
        return resp.text

    async def get_recording_url(self, conversation_id: int) -> str:
        """GET /recordings/{id}/audio -- presigned S3 URL for recording."""
        if self.mock:
            return MOCK_RECORDING_URL

        client = await self._get_client()
        resp = await client.get(f"/recordings/{conversation_id}/audio")
        if resp.status_code != 200:
            raise RecoClientError(
                f"get_recording_url failed: {resp.status_code} {resp.text}"
            )
        return resp.json().get("url", "")

    async def get_final_block_id(self, conversation_id: int) -> str | None:
        """Get the final block ID from the conversation.

        TODO: reco does not expose final_block_id yet. This method is a
        placeholder for when the field is added to the conversation API.
        """
        return None


def parse_transcript_turns(transcript: str) -> list[dict[str, str]]:
    """Parse a raw transcript string into structured turns.

    Each line is expected in the format ``ROLE: text``.
    Returns a list of dicts with 'role' and 'text' keys.
    """
    turns: list[dict[str, str]] = []
    for line in transcript.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        colon_idx = line.find(": ")
        if colon_idx == -1:
            # Continuation of previous turn
            if turns:
                turns[-1]["text"] += " " + line
            continue
        role = line[:colon_idx].strip().upper()
        text = line[colon_idx + 2:].strip()
        turns.append({"role": role, "text": text})
    return turns
