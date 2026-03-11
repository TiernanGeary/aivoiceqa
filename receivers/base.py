"""Abstract base class for call receivers."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ActiveCall:
    """Tracks state of an active call."""

    call_sid: str
    stream_sid: str
    websocket: object  # WebSocket instance (typed loosely to avoid import dep)
    started_at: float
    audio_queue: asyncio.Queue = field(default_factory=asyncio.Queue)


class CallReceiver(ABC):
    """Abstract interface for receiving calls.

    Pluggable for Twilio, SIP, WebRTC, etc.
    """

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
