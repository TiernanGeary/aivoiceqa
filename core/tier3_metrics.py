"""Tier 3: Audio-specific evaluation metrics.

Uses OpenAI Whisper for transcription and character error rate (CER) for comparison.
Only available when recorded audio is present.
"""

from __future__ import annotations

import io
from typing import Any

from models.result import EvalResult


def _character_error_rate(reference: str, hypothesis: str) -> float:
    """Compute Character Error Rate (CER) between reference and hypothesis.

    CER = edit_distance(ref, hyp) / len(ref)
    Uses a standard Levenshtein distance at the character level,
    appropriate for Japanese where word boundaries are ambiguous.
    """
    if not reference:
        return 0.0 if not hypothesis else 1.0

    ref = list(reference)
    hyp = list(hypothesis)
    n = len(ref)
    m = len(hyp)

    # DP table for Levenshtein distance
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[j] = min(
                prev[j] + 1,      # deletion
                dp[j - 1] + 1,    # insertion
                prev[j - 1] + cost,  # substitution
            )

    return dp[m] / n


class Tier3Evaluator:
    """Evaluates audio-specific metrics using OpenAI Whisper."""

    # CER threshold: below this is considered good pronunciation
    CER_PASS_THRESHOLD = 0.15

    def __init__(self, openai_client: Any):
        self._client = openai_client

    async def transcribe_audio(
        self, audio_bytes: bytes, language: str = "ja"
    ) -> str:
        """Transcribe audio using Whisper API. Returns text."""
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "audio.wav"

        transcript = await self._client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language=language,
        )
        return transcript.text

    async def evaluate_tts_pronunciation(
        self, intended_text: str, audio_bytes: bytes
    ) -> EvalResult:
        """Compare what reco intended to say vs what Whisper hears.

        Uses Character Error Rate (CER) for Japanese.
        """
        whisper_text = await self.transcribe_audio(audio_bytes)
        cer = _character_error_rate(intended_text, whisper_text)
        passed = cer <= self.CER_PASS_THRESHOLD

        return EvalResult(
            metric="tts_pronunciation",
            passed=passed,
            score=max(0.0, 1.0 - cer),
            reasoning=f"CER={cer:.3f} (threshold={self.CER_PASS_THRESHOLD})",
            details={
                "intended_text": intended_text,
                "whisper_text": whisper_text,
                "cer": cer,
            },
        )

    async def evaluate_stt_accuracy(
        self, our_text: str, agent_response: str
    ) -> EvalResult:
        """Indirect STT check: does the agent's response make sense given what we said?

        If we said 'Tuesday 2pm' and agent confirms 'Wednesday 3pm', STT likely
        misheard us. This is a heuristic — not a perfect measure.

        For MVP, uses simple keyword overlap. A more advanced version could use
        Claude for semantic comparison.
        """
        if not our_text or not agent_response:
            return EvalResult(
                metric="stt_accuracy",
                passed=True,
                score=None,
                reasoning="Insufficient data for STT accuracy check",
            )

        # Simple heuristic: check character overlap between what we said and
        # key content words in the agent's response
        our_chars = set(our_text)
        agent_chars = set(agent_response)
        overlap = len(our_chars & agent_chars)
        total = len(our_chars | agent_chars)
        similarity = overlap / total if total > 0 else 0.0

        # This is a loose heuristic; a low score means the agent may not have
        # understood what we said.
        passed = similarity > 0.1  # Very permissive for MVP

        return EvalResult(
            metric="stt_accuracy",
            passed=passed,
            score=similarity,
            reasoning=f"Character overlap={similarity:.2f}",
            details={
                "our_text": our_text,
                "agent_response": agent_response,
            },
        )
