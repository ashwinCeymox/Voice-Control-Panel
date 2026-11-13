"""
Standalone Streaming STT Service (Candidate E1: sherpa-onnx Zipformer 20M).
Provides real-time speech recognition over WebSocket with low-latency partial updates.
Bound to private loopback 127.0.0.1:8088 (internal service).
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional
import numpy as np
import sherpa_onnx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("streaming_stt_server")

app = FastAPI(title="Streaming STT Service", version="1.0.0")

# Model configuration
DEFAULT_MODEL_DIR = Path(
    os.environ.get(
        "STREAMING_STT_MODEL_DIR",
        "models/streaming-stt/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17",
    )
)

MODEL_NAME = "sherpa-onnx-streaming-zipformer-en-20M"
LICENSE = "Apache-2.0"
recognizer: Optional[sherpa_onnx.OnlineRecognizer] = None


def init_recognizer():
    global recognizer
    tokens = DEFAULT_MODEL_DIR / "tokens.txt"
    encoder = DEFAULT_MODEL_DIR / "encoder-epoch-99-avg-1.int8.onnx"
    decoder = DEFAULT_MODEL_DIR / "decoder-epoch-99-avg-1.int8.onnx"
    joiner = DEFAULT_MODEL_DIR / "joiner-epoch-99-avg-1.int8.onnx"

    if not (tokens.exists() and encoder.exists() and decoder.exists() and joiner.exists()):
        logger.error("Missing zipformer model files in %s", DEFAULT_MODEL_DIR)
        raise RuntimeError(f"Zipformer model files not found in {DEFAULT_MODEL_DIR}")

    logger.info("Initializing sherpa-onnx OnlineRecognizer from %s...", DEFAULT_MODEL_DIR)
    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(tokens),
        encoder=str(encoder),
        decoder=str(decoder),
        joiner=str(joiner),
        num_threads=2,
        decoding_method="greedy_search",
        enable_endpoint_detection=True,
    )
    logger.info("sherpa-onnx OnlineRecognizer successfully initialized.")


@app.on_event("startup")
async def startup_event():
    init_recognizer()


@app.get("/health")
async def health_check():
    """Health check endpoint for Gateway and Docker."""
    if recognizer is None:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {
        "status": "ready",
        "model": MODEL_NAME,
        "kind": "streaming",
        "provider": "stream-stt",
        "license": LICENSE,
        "sample_rate": 16000,
    }


@app.get("/v1/models")
async def list_models():
    """List available streaming STT models."""
    return {
        "data": [
            {
                "id": MODEL_NAME,
                "name": "Streaming Zipformer 20M (English)",
                "category": "stt",
                "kind": "streaming",
                "provider": "stream-stt",
                "license": LICENSE,
                "size_mb": 42,
            }
        ]
    }


def split_confirmed_tentative(text: str, prev_confirmed: str) -> tuple[str, str]:
    """Split raw recognized text into a monotonically increasing confirmed prefix and a tentative tail.
    
    Rule: Confirmed words never change or retract. Only the trailing 1-2 words are tentative.
    """
    words = text.split()
    if not words:
        return "", ""

    # If 1 or 2 words, the whole text is tentative unless already confirmed
    if len(words) <= 2:
        return prev_confirmed, " ".join(words[len(prev_confirmed.split()) :]) if prev_confirmed else text

    # Confirm all words except the last 2 trailing words
    candidate_confirmed = " ".join(words[:-2])
    # Monotonicity guard: ensure candidate_confirmed preserves previous confirmed prefix
    if candidate_confirmed.startswith(prev_confirmed):
        confirmed = candidate_confirmed
    else:
        confirmed = prev_confirmed

    # Tentative is everything after confirmed
    if confirmed:
        tentative = text[len(confirmed) :].strip()
    else:
        tentative = text

    return confirmed, tentative


@app.websocket("/ws")
async def websocket_stt_stream(websocket: WebSocket):
    """Real-time audio streaming endpoint accepting PCM16 frames and emitting partial transcripts."""
    await websocket.accept()

    if recognizer is None:
        await websocket.send_json({"type": "error", "message": "Streaming recognizer not initialized"})
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    stream = recognizer.create_stream()
    sample_rate = 16000
    seq = 0
    confirmed_text = ""
    last_emitted_text = ""

    try:
        while True:
            message = await websocket.receive()

            # Handle text/JSON control frames
            if "text" in message:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type")

                if msg_type == "stream.start":
                    sample_rate = data.get("sample_rate", 16000)
                    seq = 0
                    confirmed_text = ""
                    last_emitted_text = ""
                    logger.debug("Client started stream with sample_rate=%d", sample_rate)

                elif msg_type == "audio.commit":
                    # Feed tail padding and finalize
                    tail = np.zeros(int(0.4 * sample_rate), dtype=np.float32)
                    stream.accept_waveform(sample_rate, tail)
                    stream.input_finished()

                    while recognizer.is_ready(stream):
                        recognizer.decode_stream(stream)

                    final_text = recognizer.get_result(stream).strip()
                    logger.debug("Stream finalized (%d chars).", len(final_text))

                    await websocket.send_json({
                        "type": "transcript.completed",
                        "text": final_text,
                        "model": MODEL_NAME,
                    })
                    break

                elif msg_type == "stream.cancel":
                    logger.debug("Client cancelled stream")
                    break

            # Handle binary audio frames (PCM16 16kHz mono)
            elif "bytes" in message:
                pcm_bytes = message["bytes"]
                if not pcm_bytes:
                    continue

                # Convert int16 PCM to float32 normalized [-1.0, 1.0]
                samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                stream.accept_waveform(sample_rate, samples)

                # Decode while frames are ready
                while recognizer.is_ready(stream):
                    recognizer.decode_stream(stream)

                current_text = recognizer.get_result(stream).strip()

                if current_text and current_text != last_emitted_text:
                    seq += 1
                    confirmed, tentative = split_confirmed_tentative(current_text, confirmed_text)
                    confirmed_text = confirmed
                    last_emitted_text = current_text

                    await websocket.send_json({
                        "type": "transcript.partial",
                        "seq": seq,
                        "confirmed": confirmed,
                        "tentative": tentative,
                        "text": current_text,
                    })

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected normally")
    except Exception as e:
        logger.exception("Error in streaming STT session: %s", e)
        try:
            await websocket.send_json({"type": "error", "message": "Internal streaming transcription error"})
        except Exception:
            pass
    finally:
        del stream


if __name__ == "__main__":
    port = int(os.environ.get("STREAMING_STT_PORT", 8088))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
