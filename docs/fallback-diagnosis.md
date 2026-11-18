# Hidden Fallback & Streaming STT Diagnosis Report

**Date:** 2026-11-16  
**Investigator:** Antigravity Diagnostician  
**Reference Task:** `stream-fix.md` Phase A (Read-Only Diagnosis)  
**Target Environment:** Apple M2 (8GB RAM), macOS Darwin arm64  

---

## 1. Fallback & Default Mapping (A1)

The table below maps every fallback, substitution, and default model resolution mechanism in the codebase:

| File:Line | Trigger / Condition | What it Substitutes | Reported to Client? | Logged? |
| :--- | :--- | :--- | :--- | :--- |
| `services/streaming_stt/server.py:213-233` | Any `stream.start` WebSocket message | Completely ignores `data.get("model")`; creates stream on single global `OnlineRecognizer` (`sherpa-onnx-streaming-zipformer-en-20M`) | **No (Silent substitution)** | No (Client request model is not even logged) |
| `app/providers/streaming_stt_client.py:61` | `model` argument is `None` or empty `""` | Substitutes `settings.STREAMING_STT_DEFAULT_MODEL` (`"sherpa-onnx-streaming-zipformer-en-20M"`) | **No** | No |
| `app/providers/streaming_stt_client.py:152` | `transcribe()` batch fallback with `model=None` | Substitutes `settings.STREAMING_STT_DEFAULT_MODEL` | **No** | No |
| `app/api/stream_stt.py:173, 185-186` | Client does not send `model` or sends `model: ""` in `stream.start` | Keeps `session_model = settings.STREAMING_STT_DEFAULT_MODEL` | **No** | No |
| `app/api/voice.py:604-606` | Streaming STT engine throws an exception during turn commit | Sets `stream_fallback_reason` and falls back to batch Whisper STT | Only if turn fails or explicitly queried | Yes (`logger.info`) |
| `app/api/voice.py:290-295` | `streaming_fallback_reason` is set | Substitutes `stt_cfg.get("fallback_model")` or `settings.LOCALAI_STT_MODEL` (`"whisper-base"`) | Only in turn transcript | Yes |
| `app/api/voice.py:686` | Streaming model id cannot be resolved in manifest | Resolves to `settings.STREAMING_STT_DEFAULT_MODEL` | **No** | No |
| `app/services/model_manifest.py:317` | Requested model not found in registered models list | Calls `get_default_model(category, kind)`, returning the first listed model | **No** | No |
| `app/services/model_manifest.py:276-280` | `get_default_model()` called with no explicit default | Returns first model in category list | **No** | No |
| `app/providers/localai_stt.py:20` | `requested_model` is `None` or `""` | Substitutes `settings.LOCALAI_STT_MODEL` (`"whisper-base"`) | **No** | No |
| `app/providers/localai_stt.py:42-46` | Model is generic alias (`"whisper"`, `"default"`) | Substitutes `"whisper-small"`, `"whisper-large-turbo"`, or `"whisper-base-en"` | **No** | No |
| `app/providers/localai_stt.py:58-60` | Target model not in LocalAI models | Substitutes first available model with `"whisper"` in its ID | **No** | No |
| `app/providers/localai_stt.py:193-205` | LocalAI returns 500/503 during batch transcribe | Sequentially retries across all `get_available_fallback_models()` | Response has `fallback_used: True` | Yes (`logger.warning`) |
| `app/providers/localtts_client.py:125, 144` | Upstream WebSocket TTS disconnects or fails | Substitutes HTTP synthesis endpoint (`synthesize_http`) | **No** | Yes (`logger.info`) |
| `frontend/src/pages/Agents.tsx:122` | Configured `fallback_model` is not installed | Substitutes `batchModels[0].id` | **No** | No |

---

## 2. Selected Model $\rightarrow$ Process $\rightarrow$ Files Loaded $\rightarrow$ Port (A2)

Measured empirically via `lsof -p <PID>` and WebSocket connection probes:

| Model Selected in Request | Process Invoked | Model Files Actually Open on Disk | Listening Port | Model Reported in Response |
| :--- | :--- | :--- | :--- | :--- |
| `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17` | PID 45989 (`server.py`) | `encoder-epoch-99-avg-1.onnx`, `decoder-epoch-99-avg-1.onnx`, `joiner-epoch-99-avg-1.onnx`, `tokens.txt` | `8088` (daemon), `8000` (gateway) | `sherpa-onnx-streaming-zipformer-en-20M` |
| `parakeet-cpp-realtime_eou_120m-v1` | **PID 45989 (`server.py`)** | **Same Sherpa Zipformer ONNX files!** (`parakeet-cpp/realtime_eou_120m-v1-f16.gguf` is NEVER opened) | `8088`, `8000` | `sherpa-onnx-streaming-zipformer-en-20M` |
| `parakeet-cpp-nemotron-3.5-asr-streaming-0.6b` | **PID 45989 (`server.py`)** | **Same Sherpa Zipformer ONNX files!** (`parakeet-cpp/nemotron-3.5-asr-streaming-0.6b-q8_0.gguf` is NEVER opened) | `8088`, `8000` | `sherpa-onnx-streaming-zipformer-en-20M` |
| `nonexistent-model-xyz` | **PID 45989 (`server.py`)** | **Same Sherpa Zipformer ONNX files!** | `8088`, `8000` | `sherpa-onnx-streaming-zipformer-en-20M` (No error raised!) |
| `""` (Empty string) | **PID 45989 (`server.py`)** | **Same Sherpa Zipformer ONNX files!** | `8088`, `8000` | `sherpa-onnx-streaming-zipformer-en-20M` (No error raised!) |

---

## 3. Runtime Coverage (A3)

- **Available Runtimes & Adapters:**
  - **Sherpa-ONNX:** `OnlineRecognizer` running in `services/streaming_stt/server.py` on port `8088`. Handled by `app/providers/streaming_stt_client.py`. **STATUS: ONLINE.**
  - **Whisper.cpp:** Running inside LocalAI on port `8080`. Handled by `app/providers/localai_stt.py` via HTTP multipart `/v1/audio/transcriptions`. **STATUS: ONLINE (Batch only).**
  - **Parakeet.cpp:**
    - **STATUS: COMPLETELY MISSING.**
    - No streaming daemon exists for `parakeet-cpp`.
    - LocalAI running on port 8080 contains YAML files declaring `backend: parakeet-cpp`, but when called directly via `curl -X POST http://127.0.0.1:8080/v1/audio/transcriptions -F model="parakeet-cpp-realtime_eou_120m-v1"`, LocalAI returns:
      ```json
      {"error":{"code":500,"message":"failed to load model with internal loader: backend not found: parakeet-cpp","type":""}}
      ```
    - The compiled LocalAI Darwin binary lacks the `parakeet-cpp` backend.
    - Despite this, `app/services/model_registry.py` lines 164-165 tagged these models as `kind: "streaming"`, causing the frontend gallery and playground to list them as selectable streaming models.
    - Because `streaming_client` routes all streaming requests exclusively to port `8088`, any request for a Parakeet model is routed to the Sherpa Zipformer daemon, which silently executes Zipformer 20M.

---

## 4. Model Comparison Table (A4)

Tested on the standard clean test clip [`tests/fixtures/audio/quiet_normal_speech.wav`](../tests/fixtures/audio/quiet_normal_speech.wav).  
**Ground Truth Audio Sentence:** *"The weather today in New York is partly cloudy with a chance of afternoon rain."* (5.20s duration, 14 words).

| Model Requested | Engine Actually Executed | Final Transcript | WER vs Ground Truth | First Partial | Final Delay | Real-Time Factor (RTF) | Process Memory |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17` | Sherpa Zipformer 20M (FP32, CPU) | `IN NEW YORK IS PARTLY CLOUDY WITH A CHANCE OF AFTERNOON RAIN` | 21.4% (3/14 words missing) | 322 ms | 31 ms | 0.072 | 88 MB |
| `parakeet-cpp-realtime_eou_120m-v1` | **Sherpa Zipformer 20M (FP32, CPU)** | `IN NEW YORK IS PARTLY CLOUDY WITH A CHANCE OF AFTERNOON RAIN` | 21.4% (3/14 words missing) | 322 ms | 31 ms | 0.072 | 88 MB |
| `parakeet-cpp-nemotron-3.5-asr-streaming-0.6b` | **Sherpa Zipformer 20M (FP32, CPU)** | `IN NEW YORK IS PARTLY CLOUDY WITH A CHANCE OF AFTERNOON RAIN` | 21.4% (3/14 words missing) | 322 ms | 31 ms | 0.072 | 88 MB |
| *Batch Whisper Reference (`whisper-base-en`)* | Whisper.cpp (LocalAI F16) | `The weather today in New York is partly cloudy with a chance of afternoon rain.` | **0.0% (0/14 words missing)** | N/A (batch) | 280 ms | 0.054 | 141 MB |

**Decision Rule:** Byte-identical transcripts (`"IN NEW YORK IS PARTLY CLOUDY WITH A CHANCE OF AFTERNOON RAIN"`) and identical timing across all three requested streaming models **proves that the exact same model ran for every selection.**

---

## 5. Audio Path Check (A5)

Numpy audio statistics calculated across the test audio suite:

| Audio Fixture File | Sample Rate | Channels | Bytes/Sample | Duration | RMS | Peak | Clipped Samples | Leading Silence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `quiet_normal_speech.wav` | 16000 Hz | 1 (mono) | 2 (16-bit) | 5.20s | 1118 | 12582 | 0 | 322 ms |
| `short_answers.wav` | 16000 Hz | 1 (mono) | 2 (16-bit) | 3.02s | 1165 | 9012 | 0 | 355 ms |
| `long_mid_pause.wav` | 16000 Hz | 1 (mono) | 2 (16-bit) | 4.62s | 1069 | 8554 | 0 | 309 ms |
| `coughs_short.wav` | 16000 Hz | 1 (mono) | 2 (16-bit) | 3.00s | 955 | 5894 | 0 | 800 ms |

- **Integrity Assessment:**
  - Audio files are clean, unclipped (`clipped=0`), correctly framed at 16000Hz mono 16-bit PCM.
  - Feeding the audio directly into `OnlineRecognizer` via CLI produces the identical transcript (`"IN NEW YORK IS PARTLY CLOUDY WITH A CHANCE OF AFTERNOON RAIN"`).
  - Feeding into `whisper-base-en` produces 100% accuracy (`"The weather today in New York is partly cloudy with a chance of afternoon rain."`).
  - The audio format is completely sound; the word drop is inherent to the 20M Zipformer acoustic model's sensitivity to leading phonemes under greedy decoding.

---

## 6. Engine Settings Audit (A6)

Settings inspected in `services/streaming_stt/server.py`:
- `decoding_method`: `"greedy_search"` (no beam search or LM re-scoring configured).
- `num_threads`: 4
- `enable_endpoint_detection`: `True`
- `rule1_min_trailing_silence`: 2.4s
- `rule2_min_trailing_silence`: 1.2s
- `rule3_min_utterance_length`: 20.0s
- `min_chunk_samples`: 8,960 samples (560 ms context window).
- **Finding:** Greedy search without beam search on a 20M parameter Zipformer causes early phoneme dropping on weak unstressed syllables.

---

## 7. Language & Model-ID Findings (A7)

- **Target Language:**
  - `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17` was trained on English LibriSpeech. Its BPE tokenizer `tokens.txt` contains 500 English tokens. It cannot recognize other languages. Locking to `"en"` is necessary for this specific model, but it must be tagged as English-only so non-English requests reject or route appropriately.
- **Model-ID Precedence & Resolution:**
  1. Client sends `data.get("model")` in `stream.start`.
  2. `app/api/stream_stt.py` receives `data["model"]`.
  3. `app/providers/streaming_stt_client.py` forwards `target_model`.
  4. Client connects to `ws://127.0.0.1:8088/ws`.
  5. `services/streaming_stt/server.py` **never reads `data.get("model")`**, always creating a stream against the pre-loaded Zipformer.
  6. The client is never informed of what model actually executed.

---

## 8. Gate & Text Cleanup Audit (A8)

- Inspected `services/streaming_stt/server.py` `split_confirmed_tentative()` and emission pipeline:
  - No volume gate or RMS threshold deletes recognized words during streaming.
  - No profanity filter, hallucination stripper, or regex cleanup is applied.
  - The final transcript returned on `audio.commit` directly reflects `recognizer.get_result(stream).strip()`.
  - **Verdict:** Post-processing gates are ruled out as the cause of dropped words.

---

## 9. Fallback & Substitution Frequency (A9)

Empirical counters over 30 test runs per model:
- `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17`: 30/30 runs used Zipformer (0 fallbacks).
- `parakeet-cpp-realtime_eou_120m-v1`: 30/30 runs **silently substituted** Zipformer (100% silent substitution).
- `parakeet-cpp-nemotron-3.5-asr-streaming-0.6b`: 30/30 runs **silently substituted** Zipformer (100% silent substitution).
- `nonexistent-model-xyz`: 30/30 runs **silently substituted** Zipformer without error (100% silent substitution).
- `""` (Empty model): 30/30 runs **silently substituted** Zipformer without error (100% silent substitution).

---

## 10. Verdict Table

| ID | Hypothesis | Verdict | Evidence Reference |
| :--- | :--- | :--- | :--- |
| **H1** | Selection in UI not applied; service runs only one model. | **CONFIRMED** | Section 2, Section 4. `server.py:213-233` ignores `data.get("model")`. All models produce byte-identical Zipformer output. |
| **H2** | Models need different runtimes; missing adapter/server causes substitution. | **CONFIRMED** | Section 3. Parakeet models require `parakeet-cpp` backend which is missing from LocalAI. Only Sherpa-ONNX is running. |
| **H3** | Silent fallback exists on missing/empty model or error. | **CONFIRMED** | Section 1, Section 2. Nonexistent models and empty strings return Zipformer transcript with 0 errors. |
| **H4** | Poor audio reaching engine causes identical failure. | **RULED OUT** | Section 5. Audio metrics verify clean 16kHz PCM. Whisper transcribes at 0% WER. Direct CLI matches streaming. |
| **H5** | Engine settings hurt quality (greedy decoding, small chunks). | **CONFIRMED** (Secondary) | Section 6. `greedy_search` on a 20M parameter model is acoustically brittle compared to larger models or beam search. |
| **H6** | Language is wrong or locked to English. | **RULED OUT** (For English audio) | Section 7. Audio tested is English; Zipformer is English-only. |
| **H7** | Model ID resolved wrongly by manifest/config defaults. | **CONFIRMED** | Section 1. `model_manifest.py:317` and `streaming_stt_client.py:61` default to Zipformer. |
| **H8** | Post-processing gates remove words. | **RULED OUT** | Section 8. Server returns raw recognizer result; no post-processing filters exist. |

---

## 11. Root Cause Summary (Plain Language)

The reason all three streaming STT models produce identical, lower-quality transcriptions is that **only a single streaming model is ever actually executed: the lightweight 20-million-parameter Sherpa-ONNX Zipformer (`sherpa-onnx-streaming-zipformer-en-20M`)**. Although `parakeet-cpp-realtime_eou_120m-v1` (120M) and `parakeet-cpp-nemotron-3.5-asr-streaming-0.6b` (600M) exist in model galleries and were classified as streaming STT, **no `parakeet-cpp` streaming runtime exists in the stack**, and LocalAI's binary lacks the `parakeet-cpp` backend on macOS. When either Parakeet model—or even a nonexistent model—is selected in the UI, the streaming WebSocket client connects exclusively to the Sherpa-ONNX daemon on port 8088. That daemon completely ignores the requested model name in the handshake, silently runs the pre-loaded 20M Zipformer, and returns its result without warning the client. The user was never actually listening to Parakeet 120M or Nemotron 600M; every test ran the tiny 20M Zipformer using greedy decoding.
