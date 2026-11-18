# Diagnosis Report: Model Classification and Dashboard Telemetry (Phase F0)

Date: 2026-11-15
Author: Antigravity Agent (DIAG)
Status: Completed with Live Empirical Evidence

---

## 1. Issue A1: STT Playground Streaming Sub-Selection

### 1.1 Where does the Streaming list come from?
In `frontend/src/pages/STTPlayground.tsx` (lines 80–94), models are fetched from `GET /v1/models/installed` and filtered by `kind === 'streaming'`:
```tsx
const sttList: STTModelItem[] = Array.isArray(data.stt)
  ? data.stt.map((m: any) => ({
      id: m.id,
      name: m.name || m.id,
      kind: (m.kind === 'streaming' || m.provider === 'stream-stt') ? 'streaming' : 'batch',
      provider: m.provider,
    }))
  : [];
```
On the backend, `GET /v1/models/installed` queries `app/api/gallery.py:get_installed_models_data()`.

### 1.2 Why exactly two entries?
In `app/api/gallery.py` (lines 409–456), two separate discovery mechanisms execute:
1. It queries `StreamingSTTClient.list_models()`, which connects to `services/streaming_stt/server.py:list_models()` (line 86) returning:
   ```json
   {"id": "sherpa-onnx-streaming-zipformer-en-20M", "name": "Streaming Zipformer 20M (English)"}
   ```
2. Then in `app/api/gallery.py` lines 431–456, it iterates `models/streaming-stt/`:
   ```python
   for folder in streaming_base.iterdir():
       if folder.is_dir() and (folder / "tokens.txt").exists():
           mid = folder.name # "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17"
           stt_models.append({"id": mid, "name": "Sherpa ONNX Zipformer 20M (Streaming)", ...})
           alias = "sherpa-onnx-streaming-zipformer-en-20M"
           stt_models.append({"id": alias, "name": "Sherpa ONNX Zipformer 20M (Streaming)", ...})
   ```
   Because `folder.name` is `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17` and line 445 explicitly appends `alias = "sherpa-onnx-streaming-zipformer-en-20M"`, both entries end up in the list with identical descriptions.
   Furthermore, "Sherpa-ONNX" was shown in UI badges because it is the **runtime/engine**, not the model name.

### 1.3 What metadata does a newly installed streaming model have?
Inspection of `models/streaming-stt/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17`:
- Files present: `tokens.txt`, `encoder-epoch-99-avg-1.onnx`, `decoder-epoch-99-avg-1.onnx`, `joiner-epoch-99-avg-1.onnx`, `export-onnx-en-20M.sh`, `README.md`.
- **No `model.json` or manifest file exists**.
- Newly installed streaming models never had a manifest, so the backend was forced to rely on folder name inspection and hardcoded aliases.

---

## 2. Issue A2: TTS Playground & Engine Weight Availability

### 2.1 Live responses from local-tts (Port 8880)
1. `GET http://127.0.0.1:8880/v1/models`:
   ```json
   [
     {"model_id": "kokoro", "name": "Kokoro"},
     {"model_id": "pocket", "name": "Pocket"},
     {"model_id": "kitten", "name": "Kitten"}
   ]
   ```
2. `GET http://127.0.0.1:8880/v1/voices`:
   Returns 28 voices (e.g., `af_heart`, `af_alloy`, `bf_alice`), all of which are Kokoro ONNX voice style embeddings.

### 2.2 Live Synthesis Verification
- `POST http://127.0.0.1:8880/v1/text-to-speech/af_heart/stream` with `{"model_id": "kokoro"}`:
  **HTTP 200 OK**, synthesizes 24 kHz audio stream.
- `POST http://127.0.0.1:8880/v1/text-to-speech/af_heart/stream` with `{"model_id": "pocket"}`:
  **Transfer closed / 0 bytes returned**. `pocket` weights are missing.
- `POST http://127.0.0.1:8880/v1/text-to-speech/af_heart/stream` with `{"model_id": "kitten"}`:
  **Hangs indefinitely / Timeout**. `kitten` weights are missing.

### 2.3 Diagnosis
`pocket` and `kitten` are stub/placeholder entries reported by local-tts without actual weights installed. Because the gateway lacked an active availability probe, these dummy engines appeared in pickers and crashed downstream calls.

---

## 3. Issue A3: API Docs & Snippets Model Dropdown Filtering

### 3.1 Code Inspection
In `frontend/src/pages/developers/DocsSnippets.tsx`:
- Line 43 calls `GET /v1/models/installed`, storing the full flat array in `models`.
- Line 428 maps over `models` directly:
  ```tsx
  {models.map((m) => (
    <option key={m.id} value={m.id}>{m.name} ({m.id})</option>
  ))}
  ```
- **Finding:** The dropdown performs zero filtering by endpoint type. When viewing `/v1/chat/completions`, STT and TTS models appear in the dropdown. When viewing `/v1/audio/transcriptions`, LLM models appear in the dropdown.

---

## 4. Issue A4: "Try it" 502 Reproductions & Honest Error Mapping

### 4.1 Reproduction of 502
- Selecting `whisper-base` (an STT model) in the Chat Completions snippet and executing `handleTryIt()` sends:
  `POST /v1/chat/completions` with `{"model": "whisper-base", "messages": [...]}`.
- LocalAI rejects `whisper-base` for chat completions.
- `app/api/chat.py` catches the provider failure and raises:
  `HTTP 502 Bad Gateway: LocalAI LLM service is unreachable or returned a gateway error`.
- Similarly, executing TTS with `model_id: "pocket"` against `/v1/text-to-speech/af_heart/stream` fails upstream and returns `502`.
- Valid inputs (`llama-3.2-1b-instruct` for chat and `kokoro` for TTS) return **HTTP 200 OK**.

### 4.2 Diagnosis
The gateway did not validate that the requested model belongs to the required capability for that endpoint prior to forwarding upstream. When an incompatible model is supplied, it must return a clean **400 Bad Request** ("Model 'whisper-base' is an STT model, not an LLM") rather than an upstream 502.

---

## 5. Issue A5: Request Attribution & "None" Labels in Request Log

### 5.1 Code Inspection
In `app/services/usage_service.py` (lines 390–420):
```sql
SELECT ue.id, ue.ts, ue.key_id, k.prefix, ue.profile_id, p.name, ...
FROM usage_events ue
LEFT JOIN api_keys k ON ue.key_id = k.id
LEFT JOIN profiles p ON ue.profile_id = p.id
```
- In `items.append`:
  `"key_prefix": r[3] or "none"`, `"profile_name": r[5] or "none"`.
- In `app/api/chat.py`, `app/api/transcriptions.py`, `app/api/tts.py`:
  `KeyManager.log_usage(key.id, ...)` does not capture key name or profile name snapshots.
- When requests originate from Admin sessions (Docs "Try It", UI test-chat, or voice WebSocket pipeline), `key_id` is null or uses internal bypass.
- **Finding:** Because `k.name` and `p.name` snapshots were never captured at event creation time, any unauthenticated or system traffic shows as `"None"`.

---

## 6. Issue A6: Overview Telemetry Chart Discontinuity

### 6.1 Direct Endpoint Query
`curl -s "http://127.0.0.1:8000/v1/admin/usage/timeseries?interval=hour&since=24h"`
Returns:
```json
{
  "series": [
    {"timestamp": 1794740400, "bucket_label": "2026-11-15 11:00", "requests": 5},
    {"timestamp": 1794744000, "bucket_label": "2026-11-15 12:00", "requests": 8},
    {"timestamp": 1794747600, "bucket_label": "2026-11-15 13:00", "requests": 1},
    {"timestamp": 1794751200, "bucket_label": "2026-11-15 14:00", "requests": 5},
    {"timestamp": 1794754800, "bucket_label": "2026-11-15 15:00", "requests": 4}
  ]
}
```
### 6.2 Diagnosis
`UsageService.get_timeseries()` only iterates buckets that had rows in SQLite (`for b_ts in sorted(buckets.keys())`). It does not zero-fill empty time steps across the requested window (`since` to `until`). When rendered in `frontend/src/pages/developers/Overview.tsx`, hours without traffic are omitted completely rather than showing zero bars, distorting time progression.

---

## 7. Issue A7: Warm Engine, Memory & Model Unloading

### 7.1 Hardcoded Values in UsageService
In `app/services/usage_service.py:get_models_status()`:
- Line 490: `is_warm = True if cat in ("tts", "stt") else False` (completely static rule).
- Line 502: `system_memory = {"total_mb": 8192, "available_mb": 4096, "used_mb": 4096}` fallback.

### 7.2 Real Engine Capabilities Discovered
1. **LocalAI Loaded Models & Memory**:
   `GET http://127.0.0.1:8080/system` provides live process telemetry:
   ```json
   {
     "loaded_models": [
       {"id": "llama-3.2-1b-instruct:q4_k_m", "process": {"pid": 72405, "rss_bytes": 5799936}}
     ]
   }
   ```
2. **LocalAI Model Unloading**:
   `POST http://127.0.0.1:8080/backend/shutdown` with `{"model": "<model_id>"}` terminates the backend process and unloads the model cleanly (`{"message": "model stopped"}`).
3. **LocalAI Metadata Discovery**:
   `GET http://127.0.0.1:8080/v1/models/capabilities` explicitly tags each model with its real capabilities:
   - `["chat", "completion"]` -> LLM
   - `["transcript"]` -> STT
   - `["tts"]` -> TTS
   - `["vad"]` -> VAD
   Eliminating the need for keyword heuristics!

---

## 8. Conclusion & Action Plan
With empirical findings established, we proceed immediately into Phase F1 through F6 as defined in `dash-fix.md`.
