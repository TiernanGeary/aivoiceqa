"""FastAPI server — Twilio webhook, WebSocket, and QA UI API."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.settings import QA_PUBLIC_URL, QA_SERVER_PORT
from receivers.base import ActiveCall
from receivers.twilio_receiver import TwilioReceiver

logger = logging.getLogger(__name__)

app = FastAPI(title="voiceaiqa", version="0.1.0")

# Shared receiver — used by both Twilio WebSocket handler and scenario runner
receiver = TwilioReceiver()

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
STATIC_DIR = Path(__file__).parent / "static"
RUNS_DIR = Path(__file__).parent / "reports" / "runs"
RECORDINGS_DIR = Path(__file__).parent / "reports" / "recordings"


# ---------------------------------------------------------------------------
# Run state (in-memory)
# ---------------------------------------------------------------------------

@dataclass
class RunState:
    run_id: str
    scenario_file: str
    scenario_id: str
    status: str = "pending"      # pending | running | done | error
    logs: list[str] = field(default_factory=list)
    result: dict | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "scenario_file": self.scenario_file,
            "scenario_id": self.scenario_id,
            "status": self.status,
            "logs": self.logs,
            "result": self.result,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.finished_at - self.started_at, 1)
                if self.finished_at else None,
        }


_runs: dict[str, RunState] = {}


def _save_recording(run_id: str, inbound: bytes, outbound_segments: list) -> str | None:
    """Mix inbound (agent) and outbound (QA) mulaw audio and save as WAV.

    outbound_segments is a list of (inbound_byte_offset, mulaw_bytes) tuples.
    Both channels are decoded to PCM16, placed at their correct time offsets,
    and summed together.
    """
    if not inbound and not outbound_segments:
        return None
    try:
        import audioop
        import struct
        import wave

        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

        # Decode inbound to PCM16 samples
        inbound_pcm = audioop.ulaw2lin(bytes(inbound), 2) if inbound else b""
        n_in = len(inbound_pcm) // 2

        # Figure out total length needed
        n_total = n_in
        for offset_bytes, seg in outbound_segments:
            offset_samples = offset_bytes  # mulaw: 1 byte = 1 sample
            out_pcm = audioop.ulaw2lin(seg, 2)
            n_seg = len(out_pcm) // 2
            n_total = max(n_total, offset_samples + n_seg)

        # Build mixed PCM16 array
        mixed = [0] * n_total

        # Add inbound
        for i in range(n_in):
            mixed[i] += struct.unpack_from("<h", inbound_pcm, i * 2)[0]

        # Add each outbound segment at its time offset
        for offset_bytes, seg in outbound_segments:
            out_pcm = audioop.ulaw2lin(seg, 2)
            n_seg = len(out_pcm) // 2
            for i in range(n_seg):
                pos = offset_bytes + i
                if pos < n_total:
                    mixed[pos] += struct.unpack_from("<h", out_pcm, i * 2)[0]

        # Clip to int16 range
        mixed = [max(-32768, min(32767, s)) for s in mixed]

        # Pack and write WAV
        pcm_mixed = struct.pack(f"<{n_total}h", *mixed)
        wav_path = RECORDINGS_DIR / f"{run_id}.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(pcm_mixed)

        logger.info("Recording saved: %s (%d samples, %.1fs)", wav_path, n_total, n_total / 8000)
        return f"/recordings/{run_id}.wav"
    except Exception as exc:
        logger.warning("Failed to save recording for run %s: %s", run_id, exc)
        return None


def _save_run(run: RunState) -> None:
    """Persist a RunState to a JSON file in RUNS_DIR."""
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        path = RUNS_DIR / f"{run.run_id}.json"
        path.write_text(json.dumps(run.to_dict(), default=str), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to save run %s: %s", run.run_id, exc)


def _load_persisted_runs() -> None:
    """Load all previously persisted run JSON files into _runs on startup."""
    if not RUNS_DIR.exists():
        return
    for path in sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            run = RunState(
                run_id=data["run_id"],
                scenario_file=data["scenario_file"],
                scenario_id=data["scenario_id"],
                status=data.get("status", "done"),
                logs=data.get("logs", []),
                result=data.get("result"),
                started_at=data.get("started_at", 0.0),
                finished_at=data.get("finished_at"),
            )
            _runs[run.run_id] = run
        except Exception as exc:
            logger.warning("Failed to load run file %s: %s", path.name, exc)


@app.on_event("startup")
async def startup_event() -> None:
    _load_persisted_runs()
    logger.info("Loaded %d persisted runs", len(_runs))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scenario_files() -> list[dict]:
    """List all scenario YAML files with their metadata."""
    out = []
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            out.append({
                "file": path.name,
                "scenario_id": data.get("scenario_id", path.stem),
                "description": data.get("description", ""),
                "mode": data.get("mode", "scripted"),
                "steps": len(data.get("steps", [])),
                "flow_path": data.get("flow_path", ""),
            })
        except Exception:
            pass
    return out


def _result_to_dict(result, recording_url: str | None = None) -> dict:
    """Convert ScenarioResult dataclass to a JSON-serialisable dict."""
    steps = []
    for s in result.steps:
        steps.append({
            "step_number": s.step_number,
            "expected_block": s.expected_block,
            "actual_block": s.actual_block,
            "user_input": s.user_input_text,
            "agent_response": s.agent_response_text,
            "agent_audio_duration_ms": round(s.agent_audio_duration_ms or 0),
            "latency_ms": round(s.latency_ms) if s.latency_ms is not None else None,
            "passed": s.passed,
            "error": s.error,
        })
    return {
        "scenario_id": result.scenario_id,
        "overall_passed": result.overall_passed,
        "duration_s": round(result.duration_s, 1),
        "call_id": result.call_id,
        "conversation_id": result.conversation_id,
        "latency_p50_ms": round(result.latency_p50_ms) if result.latency_p50_ms is not None else None,
        "latency_p95_ms": round(result.latency_p95_ms) if result.latency_p95_ms is not None else None,
        "error": result.error,
        "steps": steps,
        "transcript": result.reco_transcript,
        "recording_url": recording_url,
    }


class _RunLogHandler(logging.Handler):
    """Captures log records into a RunState.logs list."""
    def __init__(self, run: RunState):
        super().__init__()
        self._run = run

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        self._run.logs.append(msg)


async def _execute_run(run: RunState, scenario_path: Path) -> None:
    """Run a scenario in the background, updating RunState as we go."""
    from config import settings
    from core.audio_gen import AudioGenerator
    from core.scenario_runner import ScenarioRunner
    from core.vad import TurnDetector
    from models.scenario import TestScenario
    from reco.client import RecoClient

    # Attach a log handler so scenario logs appear in the UI
    handler = _RunLogHandler(run)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                           datefmt="%H:%M:%S"))
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    try:
        run.status = "running"
        run.logs.append(f"Starting scenario: {run.scenario_id}")

        scenario = TestScenario.from_yaml(scenario_path)

        reco_client = RecoClient(
            base_url=settings.RECO_API_URL,
            token=settings.RECO_API_TOKEN,
            username=settings.RECO_API_USERNAME,
            password=settings.RECO_API_PASSWORD,
        )

        turn_detector = TurnDetector(
            silence_threshold_ms=scenario.vad.silence_threshold_ms,
            min_speech_ms=300,
        )

        audio_gen = AudioGenerator(tts_provider="cartesia")

        runner = ScenarioRunner(
            reco_client=reco_client,
            receiver=receiver,          # shared Twilio receiver
            turn_detector=turn_detector,
            audio_generator=audio_gen,
        )

        result = await runner.run_scenario(scenario)

        # Save local recording mixing both agent (inbound) and QA (outbound) audio
        recording_url: str | None = None
        active_call = receiver._latest_call
        if active_call:
            recording_url = _save_recording(
                run.run_id,
                inbound=bytes(active_call.inbound_buffer),
                outbound_segments=active_call.outbound_segments,
            )

        run.result = _result_to_dict(result, recording_url=recording_url)
        run.status = "error" if result.error else "done"

        await reco_client.close()

    except Exception as exc:
        logger.exception("Run %s failed", run.run_id)
        run.status = "error"
        run.logs.append(f"ERROR: {exc}")
    finally:
        run.finished_at = time.time()
        root_logger.removeHandler(handler)
        _save_run(run)


# ---------------------------------------------------------------------------
# UI API routes
# ---------------------------------------------------------------------------

@app.get("/api/scenarios")
async def list_scenarios():
    return _scenario_files()


def _safe_scenario_path(filename: str) -> Path:
    """Resolve a scenario filename, rejecting path traversal."""
    from fastapi import HTTPException
    if "/" in filename or "\\" in filename or not filename.endswith(".yaml"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return SCENARIOS_DIR / filename


def _scenario_to_yaml(data: dict) -> str:
    """Serialise a scenario dict back to YAML, preserving step structure."""
    # Ensure steps have their checks nested correctly
    steps = []
    for s in data.get("steps", []):
        step: dict = {"step": s["step"]}
        if s.get("user_input"):
            step["user_input"] = s["user_input"]
        if s.get("expected_block"):
            step["expected_block"] = s["expected_block"]
        checks: dict = {}
        c = s.get("checks", {})
        if c.get("factual"):
            checks["factual"] = c["factual"]
        if c.get("keigo_level"):
            checks["keigo_level"] = c["keigo_level"]
        meanings = [m for m in c.get("must_contain_meaning", []) if m]
        if meanings:
            checks["must_contain_meaning"] = meanings
        if checks:
            step["checks"] = checks
        steps.append(step)

    out: dict = {
        "scenario_id": data["scenario_id"],
        "mode": data.get("mode", "scripted"),
        "description": data.get("description", ""),
        "flow_path": data.get("flow_path", "reco-rta/flow/flow.yaml"),
    }
    if data.get("expected_turns"):
        out["expected_turns"] = data["expected_turns"]
    if data.get("expected_duration"):
        out["expected_duration"] = data["expected_duration"]
    if data.get("vad"):
        out["vad"] = data["vad"]

    # Persona mode fields
    if data.get("mode") == "persona":
        persona = data.get("persona", {})
        if persona:
            out["persona"] = {k: v for k, v in persona.items() if v}
        evaluation = data.get("evaluation", {})
        if evaluation:
            out["evaluation"] = evaluation
    else:
        out["steps"] = steps

    return yaml.dump(out, allow_unicode=True, sort_keys=False, default_flow_style=False)


@app.get("/api/scenarios/{filename}")
async def get_scenario(filename: str):
    path = _safe_scenario_path(filename)
    from fastapi import HTTPException
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    with open(path) as f:
        return yaml.safe_load(f)


@app.post("/api/scenarios")
async def create_scenario(body: dict):
    from fastapi import HTTPException
    sid = body.get("scenario_id", "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="scenario_id is required")
    filename = f"{sid}.yaml"
    path = _safe_scenario_path(filename)
    if path.exists():
        raise HTTPException(status_code=409, detail="Scenario already exists — use PUT to update")
    SCENARIOS_DIR.mkdir(exist_ok=True)
    path.write_text(_scenario_to_yaml(body), encoding="utf-8")
    return {"file": filename, "scenario_id": sid}


@app.put("/api/scenarios/{filename}")
async def update_scenario(filename: str, body: dict):
    path = _safe_scenario_path(filename)
    from fastapi import HTTPException
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    path.write_text(_scenario_to_yaml(body), encoding="utf-8")
    return {"file": filename, "scenario_id": body.get("scenario_id")}


@app.delete("/api/scenarios/{filename}")
async def delete_scenario(filename: str):
    path = _safe_scenario_path(filename)
    from fastapi import HTTPException
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    path.unlink()
    return {"deleted": filename}


class StartRunRequest(BaseModel):
    scenario_file: str


@app.post("/api/runs")
async def start_run(body: StartRunRequest):
    path = SCENARIOS_DIR / body.scenario_file
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Scenario file not found")

    with open(path) as f:
        data = yaml.safe_load(f)

    run = RunState(
        run_id=uuid.uuid4().hex[:8],
        scenario_file=body.scenario_file,
        scenario_id=data.get("scenario_id", path.stem),
    )
    _runs[run.run_id] = run
    asyncio.create_task(_execute_run(run, path))
    return run.to_dict()


@app.get("/api/runs")
async def list_runs():
    return [r.to_dict() for r in sorted(_runs.values(),
                                         key=lambda r: r.started_at, reverse=True)]


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    from fastapi import HTTPException
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.to_dict()


@app.get("/api/status")
async def server_status():
    return {
        "qa_public_url": QA_PUBLIC_URL,
        "twilio_webhook": f"https://{QA_PUBLIC_URL}/incoming" if QA_PUBLIC_URL else None,
        "active_runs": sum(1 for r in _runs.values() if r.status == "running"),
    }


# ---------------------------------------------------------------------------
# Recordings
# ---------------------------------------------------------------------------

@app.get("/recordings/{filename}")
async def get_recording(filename: str):
    from fastapi import HTTPException
    if "/" in filename or "\\" in filename or not filename.endswith(".wav"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = RECORDINGS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(str(path), media_type="audio/wav")


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

@app.get("/")
async def ui_root():
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Twilio webhook + WebSocket (unchanged)
# ---------------------------------------------------------------------------

def get_receiver() -> TwilioReceiver:
    return receiver


@app.post("/incoming")
async def incoming_call() -> Response:
    if not QA_PUBLIC_URL:
        logger.error("QA_PUBLIC_URL is not set — Twilio cannot connect to media stream")
    twiml = build_twiml(QA_PUBLIC_URL or "missing-qa-public-url")
    return Response(content=twiml, media_type="application/xml")


def build_twiml(webhook_url: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="wss://{webhook_url}/media-stream" />'
        "</Connect></Response>"
    )


@app.websocket("/media-stream")
async def media_stream(ws: WebSocket) -> None:
    await ws.accept()
    call: ActiveCall | None = None
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            event = data.get("event")

            if event == "connected":
                logger.info("WebSocket connected")

            elif event == "start":
                stream_sid = data.get("streamSid", "")
                call_sid = data.get("start", {}).get("callSid", "")
                call = ActiveCall(
                    call_sid=call_sid,
                    stream_sid=stream_sid,
                    websocket=ws,
                    started_at=time.time(),
                )
                receiver.register_active_call(call)
                logger.info("Stream started: stream_sid=%s call_sid=%s", stream_sid, call_sid)

            elif event in ("media", "stop"):
                if call is not None:
                    await receiver.handle_media_message(call, data)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
        if call is not None:
            await call.audio_queue.put(b"")
    except Exception:
        logger.exception("Error in media stream handler")
        if call is not None:
            await call.audio_queue.put(b"")


def start_server() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=QA_SERVER_PORT)


if __name__ == "__main__":
    start_server()
