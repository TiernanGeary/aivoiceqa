"""Audio cache — avoid re-generating TTS for the same text."""

from __future__ import annotations

import hashlib
from pathlib import Path


class AudioCache:
    """SHA256-based cache for generated audio.

    Cache key is SHA256 of (text + language + provider).
    Cached files are stored as raw PCM bytes with a .pcm extension.
    """

    def __init__(self, cache_dir: str = "cache/audio") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, text: str, language: str, provider: str = "cartesia") -> str:
        """Generate SHA256 cache key from text + language + provider."""
        payload = f"{text}|{language}|{provider}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, text: str, language: str, provider: str = "cartesia") -> Path:
        """Get the file path for a cached audio entry."""
        key = self._cache_key(text, language, provider)
        return self.cache_dir / f"{key}.pcm"

    def get(self, text: str, language: str, provider: str = "cartesia") -> bytes | None:
        """Check if audio for this text is cached. Return PCM bytes or None."""
        path = self._cache_path(text, language, provider)
        if path.exists():
            return path.read_bytes()
        return None

    def put(self, text: str, language: str, audio: bytes, provider: str = "cartesia") -> None:
        """Cache generated audio. Key is hash of text + language + provider."""
        path = self._cache_path(text, language, provider)
        path.write_bytes(audio)

    def has(self, text: str, language: str, provider: str = "cartesia") -> bool:
        """Check if a cache entry exists without reading it."""
        return self._cache_path(text, language, provider).exists()

    def clear(self) -> None:
        """Remove all cached audio files."""
        for f in self.cache_dir.glob("*.pcm"):
            f.unlink()
