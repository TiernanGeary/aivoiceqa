"""Load configuration from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")


def _bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


def _int(val: str | None, default: int) -> int:
    if val is None:
        return default
    return int(val)


def _float(val: str | None, default: float) -> float:
    if val is None:
        return default
    return float(val)


# --- Mock mode ---
RECO_MOCK_MODE: bool = _bool(os.getenv("RECO_MOCK_MODE"), default=False)

# --- Reco connection ---
RECO_API_URL: str = os.getenv("RECO_API_URL", "http://localhost:3010")
RECO_API_TOKEN: str = os.getenv("RECO_API_TOKEN", "")
# Username/password login (preferred over static token — auto-obtains JWT)
RECO_API_USERNAME: str = os.getenv("RECO_API_USERNAME", "")
RECO_API_PASSWORD: str = os.getenv("RECO_API_PASSWORD", "")

# --- Twilio ---
TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER: str = os.getenv("TWILIO_PHONE_NUMBER", "")
QA_SERVER_PORT: int = _int(os.getenv("QA_SERVER_PORT"), default=8050)

# --- Cartesia TTS ---
CARTESIA_API_KEY: str = os.getenv("CARTESIA_API_KEY", "")
CARTESIA_VOICE_ID: str = os.getenv("CARTESIA_VOICE_ID", "")
AUDIO_CACHE_DIR: str = os.getenv("AUDIO_CACHE_DIR", "cache/audio")

# --- Evaluation ---
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
EVAL_MODEL: str = os.getenv("EVAL_MODEL", "claude-sonnet-4-20250514")
EVAL_TEMPERATURE: float = _float(os.getenv("EVAL_TEMPERATURE"), default=0.0)
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# --- Thresholds ---
LATENCY_P95_THRESHOLD_MS: float = _float(os.getenv("LATENCY_P95_THRESHOLD_MS"), default=3000.0)
DEAD_AIR_THRESHOLD_MS: float = _float(os.getenv("DEAD_AIR_THRESHOLD_MS"), default=5000.0)
SCENARIO_STEP_TIMEOUT: float = _float(os.getenv("SCENARIO_STEP_TIMEOUT"), default=60.0)
CALL_WAIT_TIMEOUT: float = _float(os.getenv("CALL_WAIT_TIMEOUT"), default=30.0)

# --- Report output ---
REPORT_OUTPUT_DIR: str = os.getenv("REPORT_OUTPUT_DIR", "reports")

# --- Public URL for Twilio webhook ---
# The public HTTPS hostname of this QA server (e.g. from ngrok).
# Used to build TwiML that connects Twilio to our /media-stream WebSocket.
# Example: "abc123.ngrok-free.app" or "qa.example.com"
QA_PUBLIC_URL: str = os.getenv("QA_PUBLIC_URL", "")
