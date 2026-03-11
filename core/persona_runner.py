"""Persona-driven response generation for persona mode QA scenarios.

Each agent turn:
  1. Wait for agent to finish speaking (VAD)
  2. Transcribe agent audio via Whisper (gives context on what was said)
  3. Feed transcript + conversation history to GPT-4o-mini, which responds in-character
  4. Convert response to TTS audio and play it back
"""

from __future__ import annotations

import audioop
import io
import logging
import wave
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_PERSONA_SYSTEM = """\
You are roleplaying as a persona receiving an outbound sales call from an AI phone agent \
called "reco" — a Japanese AI phone tool made by Step AI.

Persona name: {name}
Personality: {personality}
Your goal in this call: {goal}
Language / tone: {language}
{objections_section}
Rules:
- Respond naturally in Japanese as this persona would on a real phone call
- Keep responses brief (1-3 sentences) — this is a phone call, not a chat
- Do NOT break character or mention that you are an AI
- When you want to end the call (hung up, agreed to next steps, or rejected), \
append exactly [END] at the very end of your final message
"""


async def transcribe_mulaw(mulaw_bytes: bytes, api_key: str) -> str | None:
    """Transcribe mulaw 8kHz mono audio to text via OpenAI Whisper.

    Returns the transcript string, or None if transcription fails/is skipped.
    """
    if not mulaw_bytes or not api_key:
        return None
    try:
        # Decode mulaw → linear PCM16
        pcm16 = audioop.ulaw2lin(mulaw_bytes, 2)

        # Build WAV in memory
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(pcm16)
        buf.seek(0)

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.wav", buf, "audio/wav")},
                data={"model": "whisper-1", "language": "ja"},
            )
        if resp.status_code == 200:
            return resp.json().get("text", "").strip() or None
        logger.warning("Whisper returned %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.debug("Transcription failed (non-fatal): %s", exc)
    return None


async def generate_response(
    persona: dict,
    history: list[dict],
    openai_api_key: str,
    model: str = "gpt-4o-mini",
) -> tuple[str, bool]:
    """Generate the persona's next response using GPT-4o-mini.

    Args:
        persona:        The persona config dict from the scenario YAML.
        history:        List of {"role": "agent"|"persona", "content": str}.
        openai_api_key: OpenAI API key.
        model:          OpenAI model to use.

    Returns:
        (response_text, should_end_call)
    """
    objections = persona.get("objections", [])
    obj_section = ""
    if objections:
        obj_section = "Objections you might raise:\n" + "\n".join(
            f"- {o}" for o in objections
        )

    system = _PERSONA_SYSTEM.format(
        name=persona.get("name", "担当者"),
        personality=persona.get("personality", "A typical Japanese business professional"),
        goal=persona.get("goal", "Assess whether this product is worth pursuing"),
        language=persona.get("language", "Japanese, polite business style (teineigo)"),
        objections_section=obj_section,
    )

    # Build OpenAI messages from history
    messages: list[dict] = [{"role": "system", "content": system}]
    for h in history:
        role = "assistant" if h["role"] == "persona" else "user"
        messages.append({"role": role, "content": h["content"]})

    if len(messages) == 1:
        # First turn: phone just connected, agent will speak first
        messages.append({"role": "user", "content": "[電話が繋がりました。相手が話し始めるのを待っています。]"})

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {openai_api_key}"},
            json={
                "model": model,
                "max_tokens": 200,
                "messages": messages,
            },
        )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    should_end = "[END]" in text
    text = text.replace("[END]", "").strip()
    return text, should_end
