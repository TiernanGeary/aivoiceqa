"""FastAPI server with Twilio webhook and WebSocket endpoints."""

from __future__ import annotations

import json
import logging
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from config.settings import QA_SERVER_PORT
from receivers.base import ActiveCall
from receivers.twilio_receiver import TwilioReceiver

logger = logging.getLogger(__name__)

app = FastAPI(title="voiceaiqa", version="0.1.0")

# Shared receiver instance — scenario runner and server both reference this
receiver = TwilioReceiver()


def get_receiver() -> TwilioReceiver:
    """Get the shared TwilioReceiver instance."""
    return receiver


@app.post("/incoming")
async def incoming_call() -> Response:
    """Twilio webhook: called when an inbound call arrives.

    Returns TwiML that connects the call to our WebSocket Media Stream.
    """
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f'<Stream url="wss://{{{{webhook_url}}}}/media-stream" />'
        "</Connect>"
        "</Response>"
    )
    # In production, webhook_url comes from config. For now, return template.
    return Response(content=twiml, media_type="application/xml")


def build_twiml(webhook_url: str) -> str:
    """Build TwiML XML for connecting a call to a Media Stream.

    Args:
        webhook_url: The public hostname for the WebSocket endpoint.

    Returns:
        TwiML XML string.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f'<Stream url="wss://{webhook_url}/media-stream" />'
        "</Connect>"
        "</Response>"
    )


@app.websocket("/media-stream")
async def media_stream(ws: WebSocket) -> None:
    """Twilio Media Streams WebSocket handler.

    Receives JSON messages with events: connected, start, media, stop.
    The receive loop stays tight — just receive, queue, continue.
    """
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
                start_data = data.get("start", {})
                call_sid = start_data.get("callSid", "")

                call = ActiveCall(
                    call_sid=call_sid,
                    stream_sid=stream_sid,
                    websocket=ws,
                    started_at=time.time(),
                )
                receiver.register_active_call(call)
                logger.info(
                    "Stream started: stream_sid=%s call_sid=%s",
                    stream_sid,
                    call_sid,
                )

            elif event in ("media", "stop"):
                if call is not None:
                    await receiver.handle_media_message(call, data)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
        if call is not None:
            await call.audio_queue.put(b"")  # Signal call ended
    except Exception:
        logger.exception("Error in media stream handler")
        if call is not None:
            await call.audio_queue.put(b"")


def start_server() -> None:
    """Start the FastAPI server (blocking). Used for standalone mode."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=QA_SERVER_PORT)


if __name__ == "__main__":
    start_server()
