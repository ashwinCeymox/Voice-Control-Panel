"""Read-only probe for LocalAI native streaming (/v1/realtime).

Connects with 0 audio frames, reads first event, sends minimal session.update,
and determines if LocalAI natively supports streaming transcription for Parakeet.
"""

import asyncio
import inspect
import json
import logging
import os
import sys

logger = logging.getLogger("probe_native")


async def probe_native_realtime(base_url: str = "http://127.0.0.1:8080", model: str = "parakeet-cpp-realtime-scene", api_key: str = "") -> dict:
    ws_base = base_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
    probe_url = f"{ws_base}/v1/realtime?model={model}"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    result = {"url": probe_url, "model": model, "native_streaming": "unknown", "detail": ""}

    try:
        import websockets

        connect_kwargs = {"close_timeout": 3.0}
        try:
            sig = inspect.signature(websockets.connect)
            if "additional_headers" in sig.parameters:
                connect_kwargs["additional_headers"] = headers
            elif "extra_headers" in sig.parameters:
                connect_kwargs["extra_headers"] = headers
        except Exception:
            connect_kwargs["extra_headers"] = headers

        async with websockets.connect(probe_url, **connect_kwargs) as ws:
            first_event = await asyncio.wait_for(ws.recv(), timeout=3.0)
            try:
                evt_json = json.loads(first_event)
                evt_type = evt_json.get("type", "unknown")
            except Exception:
                evt_type = "raw_text"

            # Minimal session update (0 audio frames)
            session_config = {
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "instructions": "Transcribe speech",
                },
            }
            await ws.send(json.dumps(session_config))
            resp_evt = await asyncio.wait_for(ws.recv(), timeout=3.0)
            await ws.close()

            if "not a pipeline model" in str(resp_evt).lower() or "not a pipeline model" in str(first_event).lower():
                result["native_streaming"] = "no"
                result["detail"] = "LocalAI /v1/realtime requires full pipeline model (LLM+TTS), rejects standalone STT"
            elif evt_type == "session.created":
                result["native_streaming"] = "yes"
                result["detail"] = f"Session accepted: {evt_type}"
            else:
                result["native_streaming"] = "unknown"
                result["detail"] = f"Received event: {evt_type}"

    except Exception as e:
        err_msg = str(e)
        if "not a pipeline model" in err_msg.lower() or "400" in err_msg:
            result["native_streaming"] = "no"
            result["detail"] = "Endpoint rejected model: not a pipeline model"
        else:
            result["native_streaming"] = "unknown"
            result["detail"] = f"Connection failed: {e}"

    return result


def main():
    base_url = os.environ.get("LOCALAI_BASE_URL", "http://127.0.0.1:8080")
    model = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LOCALAI_STT_MODEL", "parakeet-cpp-realtime-scene")
    api_key = os.environ.get("LOCALAI_API_KEY", "")
    res = asyncio.run(probe_native_realtime(base_url, model, api_key))
    print(f"Native Streaming Probe Result: {res['native_streaming']} ({res['detail']})")


if __name__ == "__main__":
    main()
