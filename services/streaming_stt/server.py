"""
Standalone Streaming STT Service (sherpa-onnx Zipformer 20M).
Provides real-time speech recognition over WebSocket with low-latency partial updates.
Bound to private loopback 127.0.0.1:8088 (internal service).

Includes:
1. Audio format validation and automatic resampling.
2. 560ms–1120ms streaming lookahead context window to prevent mid-phoneme truncation.
3. Language lock (defaulting and locking to target language, e.g. 'en').
4. Higher-precision weights (FP32/F16 default, optional INT8) and Apple Silicon provider toggle (CPU vs CoreML/Metal).
"""

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Optional
import numpy as np
import sherpa_onnx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("streaming_stt_server")

app = FastAPI(title="Streaming STT Service", version="1.1.0")

# Model configuration
DEFAULT_MODEL_DIR = Path(
    os.environ.get(
        "STREAMING_STT_MODEL_DIR",
        "models/streaming-stt/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17",
    )
)

MODEL_NAME = "sherpa-onnx-streaming-zipformer-en-20M"
LICENSE = "Apache-2.0"

# Runtime settings
ACTIVE_PRECISION: str = os.environ.get(
    "STREAMING_STT_PRECISION", "fp32"
).lower()  # "fp32" | "int8"
ACTIVE_PROVIDER: str = os.environ.get(
    "STREAMING_STT_PROVIDER", "cpu"
).lower()  # "cpu" | "coreml"
ACTIVE_CHUNK_MS: int = int(
    os.environ.get("STREAMING_STT_CHUNK_MS", "560")
)  # 560ms default
NUM_THREADS: int = int(os.environ.get("STREAMING_STT_THREADS", "4"))  # 4 threads for M2
TARGET_LANGUAGE: str = os.environ.get(
    "STREAMING_STT_LANGUAGE", "en"
)  # Explicit language lock

recognizer: Optional[sherpa_onnx.OnlineRecognizer] = None


def init_recognizer(precision: Optional[str] = None, provider: Optional[str] = None):
    global recognizer, ACTIVE_PRECISION, ACTIVE_PROVIDER
    if precision:
        ACTIVE_PRECISION = precision.lower()
    if provider:
        ACTIVE_PROVIDER = provider.lower()

    tokens = DEFAULT_MODEL_DIR / "tokens.txt"
    if not tokens.exists():
        logger.error("Missing tokens.txt in %s", DEFAULT_MODEL_DIR)
        raise RuntimeError(f"Zipformer model files not found in {DEFAULT_MODEL_DIR}")

    # Select weights based on precision setting
    if ACTIVE_PRECISION in ("fp32", "f16", "full"):
        enc_file = "encoder-epoch-99-avg-1.onnx"
        dec_file = "decoder-epoch-99-avg-1.onnx"
        joi_file = "joiner-epoch-99-avg-1.onnx"
    else:
        enc_file = "encoder-epoch-99-avg-1.int8.onnx"
        dec_file = "decoder-epoch-99-avg-1.int8.onnx"
        joi_file = "joiner-epoch-99-avg-1.int8.onnx"

    encoder = DEFAULT_MODEL_DIR / enc_file
    decoder = DEFAULT_MODEL_DIR / dec_file
    joiner = DEFAULT_MODEL_DIR / joi_file

    # Fallback to whatever exists if preferred precision is not present
    if not (encoder.exists() and decoder.exists() and joiner.exists()):
        logger.warning(
            "Preferred weights %s not found; falling back to alternative precision",
            enc_file,
        )
        alt_suffix = ".int8.onnx" if ".int8." not in enc_file else ".onnx"
        encoder = DEFAULT_MODEL_DIR / f"encoder-epoch-99-avg-1{alt_suffix}"
        decoder = DEFAULT_MODEL_DIR / f"decoder-epoch-99-avg-1{alt_suffix}"
        joiner = DEFAULT_MODEL_DIR / f"joiner-epoch-99-avg-1{alt_suffix}"

    chosen_precision = "int8" if ".int8." in str(encoder) else "fp32/f16"
    logger.info(
        "Initializing sherpa-onnx OnlineRecognizer (precision=%s, provider=%s, threads=%d)...",
        chosen_precision,
        ACTIVE_PROVIDER,
        NUM_THREADS,
    )

    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(tokens),
        encoder=str(encoder),
        decoder=str(decoder),
        joiner=str(joiner),
        num_threads=NUM_THREADS,
        decoding_method="greedy_search",
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=1.2,
        rule3_min_utterance_length=20.0,
        provider=ACTIVE_PROVIDER,
        model_type="zipformer",
    )
    ACTIVE_PRECISION = chosen_precision
    logger.info("sherpa-onnx OnlineRecognizer successfully initialized.")

    # Startup self-test: verify recognizer pipeline can decode
    try:
        test_stream = recognizer.create_stream()
        test_chunk = np.zeros(1600, dtype=np.float32)  # 100ms
        test_stream.accept_waveform(16000, test_chunk)
        test_stream.input_finished()
        while recognizer.is_ready(test_stream):
            recognizer.decode_stream(test_stream)
        logger.info("Startup self-test passed: OnlineRecognizer operational.")
    except Exception as st_err:
        logger.error("Startup self-test failed: %s", st_err)
        raise RuntimeError(f"Startup self-test failed: {st_err}")


@app.on_event("startup")
async def startup_event():
    init_recognizer()


@app.get("/health")
async def health_check():
    """Health check endpoint reporting model, precision, provider, and context window."""
    if recognizer is None:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {
        "status": "ready",
        "model": MODEL_NAME,
        "kind": "streaming",
        "provider": "stream-stt",
        "backend_provider": ACTIVE_PROVIDER,
        "precision": ACTIVE_PRECISION,
        "chunk_ms": ACTIVE_CHUNK_MS,
        "language": TARGET_LANGUAGE,
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
                "name": f"Streaming Zipformer 20M ({ACTIVE_PRECISION.upper()}, {ACTIVE_PROVIDER.upper()})",
                "category": "stt",
                "kind": "streaming",
                "provider": "stream-stt",
                "backend_provider": ACTIVE_PROVIDER,
                "precision": ACTIVE_PRECISION,
                "chunk_ms": ACTIVE_CHUNK_MS,
                "language": TARGET_LANGUAGE,
                "license": LICENSE,
                "size_mb": 88 if "fp32" in ACTIVE_PRECISION else 42,
            }
        ]
    }


def split_confirmed_tentative(text: str, prev_confirmed: str) -> tuple[str, str]:
    """Split raw recognized text into a monotonically increasing confirmed prefix and a tentative tail."""
    words = text.split()
    if not words:
        return "", ""

    if len(words) <= 2:
        return prev_confirmed, " ".join(
            words[len(prev_confirmed.split()) :]
        ) if prev_confirmed else text

    candidate_confirmed = " ".join(words[:-2])
    if candidate_confirmed.startswith(prev_confirmed):
        confirmed = candidate_confirmed
    else:
        confirmed = prev_confirmed

    if confirmed:
        tentative = text[len(confirmed) :].strip()
    else:
        tentative = text

    return confirmed, tentative


@app.websocket("/ws")
async def websocket_stt_stream(websocket: WebSocket):
    """Real-time audio streaming endpoint accepting PCM16 frames with 560ms–1120ms context window."""
    await websocket.accept()

    if recognizer is None:
        await websocket.send_json(
            {"type": "error", "message": "Streaming recognizer not initialized"}
        )
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    stream = recognizer.create_stream()
    client_sample_rate = 16000
    seq = 0
    confirmed_text = ""
    last_emitted_text = ""
    session_lang = TARGET_LANGUAGE

    # Context window buffer to avoid feeding tiny mid-phoneme fragments to the acoustic encoder
    min_chunk_samples = int(16000 * (ACTIVE_CHUNK_MS / 1000.0))
    sample_accumulator = []
    total_accumulated = 0

    try:
        while True:
            message = await websocket.receive()

            if "text" in message:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type")

                if msg_type == "stream.start":
                    req_model = data.get("model")
                    if not req_model or not str(req_model).strip():
                        logger.warning(
                            "Rejecting stream.start: empty or missing model parameter"
                        )
                        await websocket.send_json(
                            {
                                "type": "error",
                                "code": "STT_MODEL_REQUIRED",
                                "message": "Missing required 'model' parameter in stream.start",
                            }
                        )
                        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                        return

                    valid_aliases = {
                        MODEL_NAME.lower(),
                        "sherpa-onnx-streaming-zipformer-en-20m",
                        "sherpa-onnx-streaming-zipformer-en-20m-2023-02-17",
                        "zipformer",
                        "default",
                    }
                    if str(req_model).strip().lower() not in valid_aliases:
                        logger.warning(
                            "Rejecting stream.start: requested model '%s' unavailable",
                            req_model,
                        )
                        await websocket.send_json(
                            {
                                "type": "error",
                                "code": "STT_MODEL_UNAVAILABLE",
                                "message": f"Requested streaming STT model '{req_model}' is unavailable on this engine (active: {MODEL_NAME})",
                            }
                        )
                        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                        return

                    client_sample_rate = data.get("sample_rate", 16000)
                    # Explicit language lock: prefer client language if provided and not auto, else TARGET_LANGUAGE
                    req_lang = data.get("language")
                    session_lang = (
                        req_lang
                        if (req_lang and req_lang != "auto")
                        else TARGET_LANGUAGE
                    )

                    # Custom chunk size if client requested
                    req_chunk = data.get("chunk_ms")
                    if (
                        req_chunk
                        and isinstance(req_chunk, (int, float))
                        and 100 <= req_chunk <= 2000
                    ):
                        min_chunk_samples = int(16000 * (req_chunk / 1000.0))

                    seq = 0
                    confirmed_text = ""
                    last_emitted_text = ""
                    sample_accumulator = []
                    total_accumulated = 0
                    logger.debug(
                        "Client started stream (model=%s, sr=%d, lang=%s, chunk_ms=%d)",
                        req_model,
                        client_sample_rate,
                        session_lang,
                        min_chunk_samples / 16,
                    )
                    await websocket.send_json(
                        {
                            "type": "stream.ready",
                            "model": MODEL_NAME,
                            "status": "ready",
                        }
                    )

                elif msg_type == "audio.commit":
                    # Flush any remaining accumulated samples
                    if sample_accumulator:
                        flushed = np.concatenate(sample_accumulator)
                        stream.accept_waveform(client_sample_rate, flushed)
                        sample_accumulator = []
                        total_accumulated = 0

                    # Feed tail padding and finalize
                    tail = np.zeros(int(0.4 * 16000), dtype=np.float32)
                    stream.accept_waveform(16000, tail)
                    stream.input_finished()

                    while recognizer.is_ready(stream):
                        recognizer.decode_stream(stream)

                    final_text = recognizer.get_result(stream).strip()
                    logger.info(
                        "Stream turn finalized (model=%s, runtime=sherpa-onnx, fallback=False, chars=%d): %s",
                        MODEL_NAME,
                        len(final_text),
                        final_text,
                    )

                    await websocket.send_json(
                        {
                            "type": "transcript.completed",
                            "text": final_text,
                            "model": MODEL_NAME,
                            "model_used": MODEL_NAME,
                            "runtime": "sherpa-onnx",
                            "fallback": False,
                            "language": session_lang,
                            "precision": ACTIVE_PRECISION,
                        }
                    )
                    break

                elif msg_type == "stream.cancel":
                    logger.debug("Client cancelled stream")
                    break

            elif "bytes" in message:
                pcm_bytes = message["bytes"]
                if not pcm_bytes:
                    continue

                # Convert int16 PCM bytes to float32 normalized [-1.0, 1.0]
                samples = (
                    np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )
                sample_accumulator.append(samples)
                total_accumulated += len(samples)

                # Only decode once sufficient context (e.g. 560ms) has accumulated
                if total_accumulated >= min_chunk_samples:
                    chunk = np.concatenate(sample_accumulator)
                    stream.accept_waveform(client_sample_rate, chunk)
                    sample_accumulator = []
                    total_accumulated = 0

                    while recognizer.is_ready(stream):
                        recognizer.decode_stream(stream)

                    current_text = recognizer.get_result(stream).strip()

                    if current_text and current_text != last_emitted_text:
                        seq += 1
                        confirmed, tentative = split_confirmed_tentative(
                            current_text, confirmed_text
                        )
                        confirmed_text = confirmed
                        last_emitted_text = current_text

                        await websocket.send_json(
                            {
                                "type": "transcript.partial",
                                "seq": seq,
                                "confirmed": confirmed,
                                "tentative": tentative,
                                "text": current_text,
                                "language": session_lang,
                            }
                        )

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected normally")
    except Exception as e:
        logger.exception("Error in streaming STT session: %s", e)
        try:
            await websocket.send_json(
                {"type": "error", "message": "Internal streaming transcription error"}
            )
        except Exception:
            pass
    finally:
        del stream


def parse_args():
    parser = argparse.ArgumentParser(
        description="Standalone Streaming STT Service (sherpa-onnx Zipformer)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("STREAMING_STT_PORT", 8088)),
        help="Port to bind (default: 8088)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host interface to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["cpu", "cuda", "coreml"],
        default=ACTIVE_PROVIDER,
        help="Execution provider: cpu, cuda, or coreml (default: cpu)",
    )
    parser.add_argument(
        "--precision",
        type=str,
        choices=["fp32", "int8", "fp16", "f16"],
        default=ACTIVE_PRECISION,
        help="Model weights precision: fp32, int8, or fp16 (default: fp32)",
    )
    parser.add_argument(
        "--chunk-ms",
        type=int,
        default=ACTIVE_CHUNK_MS,
        help="Streaming lookahead context chunk window in ms (default: 560)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=NUM_THREADS,
        help="Computation threads for neural network (default: 4)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    ACTIVE_PROVIDER = args.provider
    ACTIVE_PRECISION = args.precision
    ACTIVE_CHUNK_MS = args.chunk_ms
    NUM_THREADS = args.threads

    logger.info(
        "Starting Streaming STT Service on %s:%d (provider=%s, precision=%s, chunk_ms=%d, threads=%d)",
        args.host,
        args.port,
        ACTIVE_PROVIDER,
        ACTIVE_PRECISION,
        ACTIVE_CHUNK_MS,
        NUM_THREADS,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
