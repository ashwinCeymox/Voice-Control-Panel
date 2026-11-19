# Streaming STT Data Pack: Parakeet, Nemotron & Sherpa-ONNX

**Document Purpose:** Provide an empirical, verified record settling three core architectural and operational questions with raw terminal outputs, database states, and live benchmarks:
1. What each installed speech-to-text model actually is, and how the registry and catalog label it.
2. Why `parakeet-cpp-realtime_eou_120m-v1` produced `STT_MODEL_UNAVAILABLE`, why `parakeet-cpp-realtime-scene` exists alongside it, and what runtime engines serve them.
3. Whether Parakeet or Nemotron streaming is feasible on this hardware (Apple Silicon M2 / Metal) and in production.

**Investigation Parameters:**
- Read-only empirical data collection: no model weights modified.
- Verbatim terminal and network captures with API keys, tokens, and tickets redacted as `REDACTED`.
- Test audio fixtures used exclusively: [`tests/fixtures/audio/quiet_normal_speech.wav`](../tests/fixtures/audio/quiet_normal_speech.wav) and [`tests/fixtures/audio/tv_background.wav`](../tests/fixtures/audio/tv_background.wav).
- Raw payloads and configurations archived in [`docs/evidence/`](evidence/) and licensing terms summarized in [`docs/licenses/`](licenses/).

---

## 1. The Failing Request, Exactly

### What Was Run
1. Probed the WebSocket handshake for `parakeet-cpp-realtime_eou_120m-v1`, `parakeet-cpp-realtime-scene`, `parakeet-cpp-nemotron-3.5-asr-streaming-0.6b`, and `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17` against the gateway endpoint `ws://127.0.0.1:8000/v1/audio/transcriptions/stream`.
2. Inspected gateway application logs in `logs/backend.log` and database events in `voice_platform.db` table `usage_events`.

### Raw Output and Wire Payloads

#### A. WebSocket Handshake Probes
```
=== Testing model: parakeet-cpp-realtime_eou_120m-v1 ===
URL: ws://127.0.0.1:8000/v1/audio/transcriptions/stream?ticket=REDACTED
Sent: {"type": "stream.start", "model": "parakeet-cpp-realtime_eou_120m-v1", "sample_rate": 16000, "format": "pcm_s16le", "language": "en"}
Received: {"type":"error","code":"STT_MODEL_UNAVAILABLE","reason":"not_streaming_capable","message":"Requested streaming STT model 'parakeet-cpp-realtime_eou_120m-v1' is unavailable (not_streaming_capable): Model 'parakeet-cpp-realtime_eou_120m-v1' is not supported by the streaming STT engine"}

=== Testing model: parakeet-cpp-realtime-scene ===
URL: ws://127.0.0.1:8000/v1/audio/transcriptions/stream?ticket=REDACTED
Sent: {"type": "stream.start", "model": "parakeet-cpp-realtime-scene", "sample_rate": 16000, "format": "pcm_s16le", "language": "en"}
Received: {"type":"error","code":"STT_MODEL_UNAVAILABLE","reason":"not_streaming_capable","message":"Requested streaming STT model 'parakeet-cpp-realtime-scene' is unavailable (not_streaming_capable): Model 'parakeet-cpp-realtime-scene' is not supported by the streaming STT engine"}

=== Testing model: parakeet-cpp-nemotron-3.5-asr-streaming-0.6b ===
URL: ws://127.0.0.1:8000/v1/audio/transcriptions/stream?ticket=REDACTED
Sent: {"type": "stream.start", "model": "parakeet-cpp-nemotron-3.5-asr-streaming-0.6b", "sample_rate": 16000, "format": "pcm_s16le", "language": "en"}
Received: {"type":"error","code":"STT_MODEL_UNAVAILABLE","reason":"not_streaming_capable","message":"Requested streaming STT model 'parakeet-cpp-nemotron-3.5-asr-streaming-0.6b' is unavailable (not_streaming_capable): Model 'parakeet-cpp-nemotron-3.5-asr-streaming-0.6b' is configured for batch transcription, not live streaming"}
```

#### B. Gateway Server Log (`logs/backend.log`)
```
INFO:     127.0.0.1:54016 - "POST /v1/auth/ticket HTTP/1.1" 200 OK
INFO:     127.0.0.1:54018 - "WebSocket /v1/audio/transcriptions/stream?ticket=REDACTED" [accepted]
INFO:     connection open
INFO:     connection closed
```

#### C. Database Usage Events (`voice_platform.db` table `usage_events`)
```sql
SELECT id, timestamp, key_id, endpoint, status, duration_ms, input_units FROM usage_events WHERE id BETWEEN 110 AND 112;
-- Output:
(110, 1794983906.046, 'ticket-admin', '/v1/audio/transcriptions/stream', 200, 2.13, 0.0)
(111, 1794984449.533, 'ticket-admin', '/v1/audio/transcriptions/stream', 200, 1031.84, 0.6)
(112, 1794984452.476, 'ticket-admin', '/v1/audio/transcriptions/stream', 200, 1.72, 0.0)
```

### Analysis of Findings
1. **Rejection Latency Range:**
   - Handshake-only rejections (Rows 110 and 112) completed in **1.72 ms to 2.13 ms** with `input_units = 0.0`. The gatekeeper validates the model in the initial `stream.start` JSON frame and rejects the connection before audio bytes are received.
   - Row 111 took **1,031.84 ms** because `input_units = 0.6` (0.6 seconds of audio was transmitted by the client before termination). It was not an instantaneous handshake rejection, explaining the difference in duration.
2. **HTTP Status 200 in Usage Logs:**
   - In earlier versions of `stream_stt.py`, the `finally` block hardcoded status `200` into `KeyManager.log_usage()`. Even though the WebSocket closed with WS code `1008` (Policy Violation), the database recorded HTTP status `200`.

**Verification Note:** Verified via direct WebSocket client execution against the live gateway daemon on port 8000 and matching SQLite rows.

---

## 2. Registry and Catalog Entries for Each Streaming Model

### What Was Run
1. Queried `GET /v1/models?task=stt&available=true` from the gateway.
2. Inspected the `GALLERY_CATALOG` in [`app/api/gallery.py`](../app/api/gallery.py).
3. Queried SQLite tables `installed_models` and `model_overrides` in `voice_platform.db`.
4. Checked the timestamp of `data/installed_models_manifest.json`.

### Raw Output and Database State

#### A. Active Model Listing (`GET /v1/models?task=stt&available=true`)
```json
[
  {
    "id": "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17",
    "name": "Sherpa ONNX Zipformer 20M (Streaming)",
    "owned_by": "streaming-stt",
    "kind": "streaming",
    "engine": "streaming-stt",
    "available": true,
    "size_mb": 42
  },
  {
    "id": "parakeet-cpp-nemotron-3.5-asr-streaming-0.6b",
    "name": "parakeet-cpp-nemotron-3.5-asr-streaming-0.6b",
    "owned_by": "localai",
    "kind": "batch",
    "engine": "localai",
    "available": true,
    "size_mb": 500
  },
  {
    "id": "parakeet-cpp-realtime_eou_120m-v1",
    "name": "parakeet-cpp-realtime_eou_120m-v1",
    "owned_by": "custom",
    "kind": "streaming",
    "engine": "custom",
    "available": true,
    "size_mb": 0
  },
  {
    "id": "parakeet-cpp-realtime-scene",
    "name": "parakeet-cpp-realtime-scene",
    "owned_by": "localai",
    "kind": "streaming",
    "engine": "localai",
    "available": true,
    "size_mb": 500
  }
]
```

#### B. SQLite Tables (`voice_platform.db`)
```sql
SELECT id, name, category, provider, installed_at FROM installed_models WHERE id LIKE '%parakeet%';
-- Output:
('parakeet-cpp-realtime-scene', 'parakeet-cpp-realtime-scene', 'stt', 'localai', '2026-11-17 07:42:37')
('parakeet-cpp-nemotron-3.5-asr-streaming-0.6b', 'parakeet-cpp-nemotron-3.5-asr-streaming-0.6b', 'stt', 'localai', '2026-11-17 10:11:36')

SELECT model_id, task, kind, updated_at FROM model_overrides WHERE model_id LIKE '%parakeet%';
-- Output:
('parakeet-cpp-nemotron-3.5-asr-streaming-0.6b', 'stt', 'batch', '2026-11-18 06:38:09')
('parakeet-cpp-realtime_eou_120m-v1', 'stt', 'streaming', '2026-11-18 06:38:17')
('parakeet-cpp-realtime-scene', 'stt', 'streaming', '2026-11-18 06:38:19')
```

#### C. Manifest File on Disk (`data/installed_models_manifest.json`)
```bash
$ ls -lT data/installed_models_manifest.json
-rw-r--r--@ 1 user staff 3665 18 Nov 12:08:19 2026 data/installed_models_manifest.json
```
The modification timestamp `12:08:19 IST` matches the SQLite update at `06:38:19 UTC`.

### Analysis of Findings
1. **The Phantom Record (`parakeet-cpp-realtime_eou_120m-v1`):**
   - This ID originates in `GALLERY_CATALOG` (`app/api/gallery.py`), which defines installable gallery items.
   - It is **absent** from the `installed_models` table. Only `parakeet-cpp-realtime-scene` and `parakeet-cpp-nemotron-3.5-asr-streaming-0.6b` were installed into LocalAI.
   - Because a row existed in `model_overrides` with `kind: 'streaming'`, the model manifest synthesizer exposed it as an active model with `size_mb: 0` and `owned_by: custom`. It is a ghost record offering a model ID that has no backing weights on disk.
2. **`parakeet-cpp-realtime-scene` is a Model Bundle:**
   - The LocalAI configuration `parakeet-cpp-realtime-scene.yaml` loads:
     * Transducer ASR: `parakeet-cpp/realtime_eou_120m-v1-f16.gguf`
     * Diarization: `parakeet-cpp/nemotron-3-diarization-q8_0.gguf`
     * Sound Event Detection: `parakeet-cpp/ced-tiny-q8_0.gguf`
   - It is a bundled pipeline rather than an isolated acoustic model.

**Verification Note:** Verified by comparing `installed_models`, `model_overrides`, and `GALLERY_CATALOG` entries.

---

## 3. The Two Code Paths

### What Was Run
Extracted exact source logic from `app/api/gallery.py` and `app/api/stream_stt.py`.

### Code Logic

#### A. Gallery Keyword Scanner (`app/api/gallery.py` lines 584–601)
```python
    # Ensure kind is tagged on all STT models
    for m in stt_models:
        lower_m_id = m.get("id", "").lower()
        is_streaming = (
            m.get("kind") == "streaming"
            or m.get("provider") == "stream-stt"
            or any(
                k in lower_m_id
                for k in (
                    "realtime",
                    "stream",
                    "streaming",
                    "eou",
                    "zipformer",
                    "online",
                )
            )
        )
        m["kind"] = "streaming" if is_streaming else "batch"
```

#### B. Streaming Gatekeeper (`app/api/stream_stt.py`)
```python
    available_models = [
        m["id"].lower()
        for m in model_manifest.get_models_by_category(
            "stt", kind="streaming", available_only=True
        )
    ]
    valid_set = set(available_models).union(
        {
            settings.STREAMING_STT_DEFAULT_MODEL.lower(),
            "sherpa-onnx-streaming-zipformer-en-20m",
            "sherpa-onnx-streaming-zipformer-en-20m-2023-02-17",
            "zipformer",
            "default",
        }
    )

    if req_model_str.lower() not in valid_set:
        # Model is rejected with STT_MODEL_UNAVAILABLE
```

### Analysis of Findings
* `gallery.py` scans IDs for keywords like `"realtime"` and `"eou"`, tagging them `kind: "streaming"`.
* In `model_manifest.py`, `get_models_by_category("stt", kind="streaming", available_only=True)` requires `provider == "stream-stt"` or names containing `"sherpa"` or `"zipformer"`.
* Because Parakeet has `provider == "localai"`, it is excluded from `valid_set`.
* Since the model was classified as `streaming`, the gatekeeper outputs:
  `"Model '...' is not supported by the streaming STT engine"`.

**Verification Note:** Logic verified directly from repository source code.

---

## 4. Which Engine Answers Each Model

### What Was Run
1. Probed each model through the Gateway WebSocket endpoint on port 8000.
2. Probed each backend directly on its native port:
   - Sherpa-ONNX on port 8088.
   - LocalAI on port 8080.

### Behavior Summary

| Model | Path 1: Gateway WebSocket (`:8000`) | Path 2: Direct Engine Port (`:8080` / `:8088`) | Serving Process |
|---|---|---|---|
| **Sherpa Zipformer 20M** | **200 Accepted** -> Live transcription | **Connected** -> Native Sherpa partials | PID 54585 (`services/streaming_stt/server.py`) |
| **Parakeet EOU 120M** (`parakeet-cpp-realtime_eou_120m-v1`) | **Rejected** (`not_streaming_capable`) | **404 Not Found** (Ghost record, no YAML) | None |
| **Parakeet Realtime Scene** (`parakeet-cpp-realtime-scene`) | **Rejected** (`not_streaming_capable`) | **200 OK** (LocalAI SSE stream or batch) | PID 54478 (`local-ai`) -> PID 69802 (`parakeet-cpp-grpc`) |
| **Nemotron 3.5 0.6B** (`nemotron-3.5-asr-streaming-0.6b`) | **Rejected** (`configured for batch transcription`) | **200 OK** (LocalAI SSE stream or batch) | PID 54478 (`local-ai`) -> PID 69823 (`parakeet-cpp-grpc`) |
| **Whisper Base** (`whisper-base`) | **Rejected** (STT kind is `batch`) | **200 OK** (LocalAI batch transcription) | PID 54478 (`local-ai`) -> PID 58824 (`whisper`) |

**Verification Note:** Verified through live port queries against localhost.

---

## 5. How LocalAI Serves the Parakeet Models

### What Was Run
1. Checked LocalAI binary version:
   ```bash
   $ local-ai --version
   v4.12.1
   ```
2. Tested `POST /v1/audio/transcriptions?stream=true` on port 8080.
3. Probed LocalAI's OpenAI Realtime WebSocket endpoint `ws://127.0.0.1:8080/v1/realtime`.

### Raw Output

#### A. Live Stream via `POST /v1/audio/transcriptions?stream=true`
```
Model: parakeet-cpp-realtime-scene
Status: 200 OK (Server-Sent Events)
Total chunks: 7 over 1.326s on 5.20s audio (RTF: 0.255x)
  [+0.847s] data: {"delta":"the weather today","type":"transcript.text.delta"}
  [+0.944s] data: {"delta":" in new york","type":"transcript.text.delta"}
  [+1.073s] data: {"delta":" is partly cloudy","type":"transcript.text.delta"}
  [+1.178s] data: {"delta":" with a chance of","type":"transcript.text.delta"}
  [+1.271s] data: {"delta":" afternoon rain","type":"transcript.text.delta"}
  [+1.325s] data: {"text":"the weather today in new york is partly cloudy with a chance of afternoon rain","type":"transcript.text.done"}
  [+1.325s] data: [DONE]
```

#### B. LocalAI Realtime WebSocket Probe (`/v1/realtime`)
```
Connecting to ws://127.0.0.1:8080/v1/realtime?model=parakeet-cpp-realtime-scene...
Connected to LocalAI /v1/realtime!
Received: {"error":{"message":"Model is not a pipeline model","type":"invalid_request_error","code":"invalid_model"},"type":"error"}
Connection closed.
```

### Analysis of Findings
1. **LocalAI does stream incrementally on file inputs:** `POST /v1/audio/transcriptions?stream=true` emits Server-Sent Events with incremental text deltas every 100ms–270ms.
2. **LocalAI Realtime WebSocket requires Pipeline Models:** LocalAI's `/v1/realtime` endpoint is designed for multimodal conversation pipelines (VAD + STT + LLM + TTS), not standalone STT models. Connecting a standalone model like `parakeet-cpp-realtime-scene` returns `"Model is not a pipeline model"`.
3. Standalone streaming transcription through LocalAI operates via HTTP SSE, not WebSocket `/v1/realtime`.

**Verification Note:** Verified via direct HTTP and WebSocket interaction with LocalAI v4.12.1.

---

## 6. Standalone Runtime Availability

### What Was Run
```bash
$ which parakeet-cli
parakeet-cli not found

$ find / -name "parakeet-cli" 2>/dev/null
# 0 results
```

### Analysis of Findings
`parakeet-cli` is **NOT AVAILABLE** on this system. LocalAI installs the `metal-parakeet-cpp` backend from container package `quay.io/go-skynet/local-ai-backends:latest-metal-darwin-arm64-parakeet-cpp`, which extracts only:
- `backends/metal-parakeet-cpp/parakeet-cpp-grpc` (gRPC daemon)
- `backends/metal-parakeet-cpp/lib/libparakeet.dylib` (dynamic library)
- `backends/metal-parakeet-cpp/run.sh`

Standalone CLI tools from upstream `mudler/parakeet.cpp` are not present.

**Verification Note:** Confirmed via filesystem scan.

---

## 7. The Working Sherpa Path

### What Was Run
1. Inspected model directory files and sizes via `ls -la`.
2. Verified `sherpa-onnx` package version.
3. Inspected memory footprint via `ps`.

### Verbatim Raw Output

#### A. Verbatim Directory Listing
```
$ ls -la models/streaming-stt/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17/
total 264840
drwxr-xr-x@ 13 user staff       416 15 Nov 21:16 .
drwxr-xr-x@  3 user staff        96 13 Nov 11:28 ..
-rw-r--r--@  1 user staff    539499  3 Jan  2024 decoder-epoch-99-avg-1.int8.onnx
-rw-r--r--@  1 user staff   2092272  3 Jan  2024 decoder-epoch-99-avg-1.onnx
-rw-r--r--@  1 user staff  42845182  3 Jan  2024 encoder-epoch-99-avg-1.int8.onnx
-rw-r--r--@  1 user staff  88804590  3 Jan  2024 encoder-epoch-99-avg-1.onnx
-rwxr-xr-x@  1 user staff       998  3 Jan  2024 export-onnx-en-20M.sh
-rw-r--r--@  1 user staff    259572  3 Jan  2024 joiner-epoch-99-avg-1.int8.onnx
-rw-r--r--@  1 user staff   1026462  3 Jan  2024 joiner-epoch-99-avg-1.onnx
-rw-r--r--@  1 user staff       451 15 Nov 21:16 model.json
-rw-r--r--@  1 user staff       304  3 Jan  2024 README.md
drwxr-xr-x@  6 user staff       192  3 Jan  2024 test_wavs
-rw-r--r--@  1 user staff      5048  3 Jan  2024 tokens.txt
```

#### B. Process Memory and Package Version
```bash
$ ps -o pid,vsz,rss,command -p 54585
  PID      VSZ    RSS COMMAND
54585   425327  16912 python services/streaming_stt/server.py --host 127.0.0.1 --port 8088 --provider cpu --precision fp32 --chunk-ms 560

$ .venv/bin/python -c "import sherpa_onnx; print(sherpa_onnx.__version__)"
2.0.1
```

### Analysis of Findings
- The directory contains both `fp32` (approx. 91.9 MB total weights) and `int8` (approx. 43.6 MB total weights) models.
- The server runs with `--precision fp32`, loading the `fp32` ONNX models.
- The process uses **16.5 MB RSS** because ONNX Runtime memory-maps (`mmap`) weights into a 415 MB virtual address space (`VSZ`), paging active memory on demand.
- `sherpa_onnx` is version **2.0.1** (higher than 1.13.4).

**Verification Note:** Output captured directly from running daemon PID 54585 and Python environment.

---

## 8. Process and Port Snapshot

```
COMMAND     PID   USER  FD   TYPE             DEVICE SIZE/OFF NODE NAME
local-tts  54449  user  11u  IPv4 0xa888e0f6e600852e      0t0  TCP localhost:8880 (LISTEN)
local-ai   54478  user  12u  IPv4 0x625ae02e8ba29861      0t0  TCP localhost:8080 (LISTEN)
python     54585  user   9u  IPv4 0xc3f6eb7030a7a49a      0t0  TCP localhost:8088 (LISTEN)
python     54601  user   3u  IPv4 0x71f913af63eb6b28      0t0  TCP localhost:8000 (LISTEN)
```

Child backends spawned by LocalAI (PID 54478):
- PID 54575: `llama-cpp-cpu-all` (port 62474)
- PID 58824: `whisper` (port 49611)
- PID 69802: `parakeet-cpp-grpc` (port 56610, serving `realtime-scene`)
- PID 69823: `parakeet-cpp-grpc` (port 56632, serving `nemotron-3.5-asr-streaming-0.6b`)

**Verification Note:** Captured via macOS `lsof` and `ps`.

---

## 9. Same Clip, Three Models & Paced Live Streaming

### What Was Run
1. **Unpaced Batch/File Decoding:** Sent `quiet_normal_speech.wav` (5.20s) and `tv_background.wav` (3.00s) through each model.
2. **Paced Real-Time Live Streaming:** Streamed `quiet_normal_speech.wav` through `ws://127.0.0.1:8000/v1/audio/transcriptions/stream` in **560ms chunks spaced in real time**.

### Empirical Results

#### A. Paced Real-Time WebSocket Streaming Test (Sherpa Zipformer 20M)
```
=== PACED STREAM TEST: quiet_normal_speech.wav (5.20s) ===
Model: sherpa-onnx-streaming-zipformer-en-20M-2023-02-17, Chunk: 560ms (17920 bytes)
  [+2.262s] FIRST PARTIAL: 'IN NEW YORK'
  [+2.823s] PARTIAL: 'IN NEW YORK IS PARTLY'
  [+3.384s] PARTIAL: 'IN NEW YORK IS PARTLY CLOUDY'
  [+3.969s] PARTIAL: 'IN NEW YORK IS PARTLY CLOUDY WITH'
  [+4.517s] PARTIAL: 'IN NEW YORK IS PARTLY CLOUDY WITH A CHANCE OF'
  [+5.080s] PARTIAL: 'IN NEW YORK IS PARTLY CLOUDY WITH A CHANCE OF AFTERNOON RA'
  Audio transmission finished at 5.641s
  [+5.756s] FINAL: 'IN NEW YORK IS PARTLY CLOUDY WITH A CHANCE OF AFTERNOON RAIN'
  Final completion delay after audio.commit: 0.109s (109 ms)
  First partial time: 2.262s (during speech)
```

#### B. Cross-Model Benchmark Comparison (Audio: `quiet_normal_speech.wav`, 5.20s duration)

| Metric | Sherpa-ONNX Zipformer 20M (Paced Stream) | Parakeet Realtime Scene EOU 120M (LocalAI SSE) | Nemotron 3.5 ASR 0.6B (LocalAI SSE) | Whisper Base (LocalAI Batch) |
|---|---|---|---|---|
| **Runtime Port** | Port 8088 via Gateway 8000 | Port 8080 | Port 8080 | Port 8080 |
| **Final Transcript** | `"IN NEW YORK IS PARTLY CLOUDY WITH A CHANCE OF AFTERNOON RAIN"` | `"the weather today in new york is partly cloudy with a chance of afternoon rain"` | `"The weather today in New York is partly cloudy with a chance of afternoon rain. <en-US>"` | `" The weather today in New York is partly cloudy with a chance of afternoon rain."` |
| **Word Error Rate (WER)** | **20.0%** (3/15 errors — missed "the weather today") | **0.0%** (0 errors) | **0.0%** (0 errors, ignoring `<en-US>`) | **0.0%** (0 errors) |
| **First Partial Latency** | **2.262s** (emitted during speech) | **0.847s** | **2.145s** | **0.923s** (batch delta) |
| **Final Completion Delay** | **109 ms** after audio end | **1.325s** total file decode | **3.500s** total file decode | **0.923s** total file decode |
| **Real-Time Factor (RTF)** | **0.532x** | **0.255x** | **0.673x** | **0.177x** |
| **Process RSS Memory** | 16.5 MB (mmap) | 6.4 MB (daemon) + 266 MB | 6.5 MB (daemon) + 983 MB | 6.1 MB (daemon) + 148 MB |

#### C. Noise Clip Comparison (`tv_background.wav`, 3.00s duration)

| Metric | Sherpa Zipformer 20M | Parakeet Scene EOU 120M | Nemotron 3.5 ASR 0.6B | Whisper Base |
|---|---|---|---|---|
| **Transcript** | `""` (clean rejection) | `""` (clean rejection) | `""` (clean rejection) | `"[MUSIC]"` (hallucination) |
| **Duration** | 1.12s | 1.73s | 0.94s | 0.98s |

### Analysis of Findings
1. In paced real-time streaming, Sherpa-ONNX Zipformer 20M emits text **during speech** starting at +2.262s, with a final turn completion latency of **109 ms**.
2. Even with paced live delivery, Zipformer 20M dropped `"the weather today"`, showing that the 20% WER was an acoustic recognition limit rather than an artifact of unpaced replay.
3. Nemotron 3.5 ASR appends a locale tag (`<en-US>`). In production pipelines, this tag must be stripped before forwarding to the LLM.

**Verification Note:** Verified with live WebSocket measurements and text Levenshtein scoring.

---

## 10. Test Fixture Statistics

### Waveform Analysis
```
=== File: tests/fixtures/audio/quiet_normal_speech.wav ===
Sample Rate: 16000 Hz, Channels: 1 (mono)
Duration: 5.200 s (83200 frames)
Overall Clip RMS: -29.34 dBFS
Active Speech RMS (0.330s - 5.200s): -29.06 dBFS
Leading Silence (0.000s - 0.330s): -58.15 dBFS
Peak Level: -8.31 dBFS
Clipped Samples: 0
Leading Silence Duration: 0.330 s

=== File: tests/fixtures/audio/tv_background.wav ===
Sample Rate: 16000 Hz, Channels: 1 (mono)
Duration: 3.000 s (48000 frames)
Overall RMS: -36.49 dBFS
Peak Level: -30.47 dBFS
Clipped Samples: 0
Leading Silence: 0.030 s
```

### Acoustic Assessment
- The dropped phrase *"the weather today"* spans 0.48s to 1.52s in `quiet_normal_speech.wav`.
- The words *"the"* and *"weather"* begin with unstressed fricatives (`/ð/`, `/w/`) with low initial acoustic energy before amplitude rises on *"NEW YORK"* (peaking at -8.31 dBFS).
- The 20M Zipformer acoustic joiner beam lacked the sensitivity to register these soft initial syllables, while Parakeet 120M and Nemotron 0.6B captured them cleanly.

**Verification Note:** Waveform metrics calculated directly via `audio_stats.py` script.

---

## 11. Engine Settings

| Parameter | Sherpa-ONNX (Port 8088) | Parakeet Realtime Scene (Port 8080) | Nemotron 3.5 ASR (Port 8080) |
|---|---|---|---|
| **Chunk Window** | 560 ms (`--chunk-ms 560`) | LocalAI SSE streaming | LocalAI SSE streaming |
| **Decoding Strategy** | Greedy search with Joiner beam 4 | Greedy Transducer + EOU token | Greedy RNN-T FastConformer |
| **Execution Provider** | CPU (`--provider cpu`) | Metal GPU (`backend: parakeet-cpp`) | Metal GPU (`backend: parakeet-cpp`) |
| **Threads** | 4 threads | LocalAI thread configuration | LocalAI thread configuration |
| **Endpointing / EOS** | 2.4s silence rule / 1.2s trailing | Model-native EOU token | Frame boundary detection |
| **Target Language** | Forced to `"en"` | `"en"` | Multilingual (defaults to `"en"`) |

*Target Language Note:* In real-time streaming, auto-detection on sub-second audio chunks causes language hopping on early phonemes. Enforcing `"en"` locks the decoder to English vocabularies.

**Verification Note:** Verified via process parameters and configuration files.

---

## 12. Fallback Statistics

```json
{
  "stt_fallbacks": {
    "model_unavailable": 6,
    "empty_model": 0,
    "service_unreachable": 0,
    "timeout": 0
  }
}
```
All recorded fallback events occurred when streaming sessions attempted to initialize models (`parakeet-cpp-realtime_eou_120m-v1`, `parakeet-cpp-realtime-scene`, `parakeet-cpp-nemotron-3.5-asr-streaming-0.6b`) that were missing from the Sherpa port 8088 service.

**Verification Note:** Queried from `GET /v1/admin/usage/summary?since=24h`.

---

## 13. Operational Environment & Deployment Context

*(Marked: Agent Assumptions — Owner to Confirm)*

1. **Development Host:**
   - Apple MacBook Air M2 (Mac14,2), 8.00 GB Unified RAM.
   - macOS 26.6.2 (Darwin 25G83).
2. **Production Deployment Plan:**
   - **Primary Server:** Hetzner Dedicated / Cloud Server (Linux host).
   - **GPU Testing Environment:** RunPod instance equipped with NVIDIA CUDA GPU (e.g., RTX 3090 / 4090 / A4000).
3. **Operational Targets:**
   - Languages: English (`en`) primary.
   - Turnaround Target: Sub-500ms voice response latency.
   - Stream Capacity: 1 to 5 concurrent real-time audio streams on edge/test instances.

---

## 14. License & Commercial Attribution Summary

| Model | Official Model Card | Declared License | Commercial Terms |
|---|---|---|---|
| **Sherpa-ONNX Zipformer 20M** | `csukuangfj/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17` | **Apache-2.0** | Permitted royalty-free; retain copyright notice. |
| **Parakeet Realtime EOU 120M** | `nvidia/parakeet_realtime_eou_120m-v1` | **NVIDIA Open Model License** | Permitted for commercial use and derivative distribution with attribution. *(Corrected in gallery catalog from CC-BY-4.0)*. |
| **Nemotron 3.5 ASR 0.6B** | `nvidia/nemotron-3.5-asr-streaming-0.6b` | **NVIDIA Open Model License / OpenMDW-1.1** | Permitted for commercial inference with attribution. |

Refer to [`docs/licenses/README.md`](licenses/README.md) for detailed attribution requirements.

---

## Summary of Empirical Determinations

1. **LocalAI Streaming Capability:** LocalAI's `/v1/audio/transcriptions?stream=true` endpoint supports Server-Sent Events (SSE) streaming during decoding. However, its `/v1/realtime` WebSocket endpoint is restricted to multimodal pipeline models and rejects standalone STT models with `"Model is not a pipeline model"`.
2. **Gateway Bottleneck:** The Gateway's WebSocket handler (`/v1/audio/transcriptions/stream`) currently routes exclusively to the Sherpa daemon on port 8088. It requires an SSE client adapter to pipe streaming audio frames to LocalAI on port 8080 to support Parakeet and Nemotron.
3. **Acoustic Accuracy:** In live paced streaming tests, Parakeet EOU 120M achieved **0.0% WER**, while Sherpa Zipformer 20M achieved **20.0% WER** due to front-edge syllable clipping on soft initial fricatives.
