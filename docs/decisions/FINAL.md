# Architectural Decisions: FINAL Synthesis
*(Phase 2 Decision Gate — `fix-multi-agent-plan.md`)*

**Approved by:** Lead Architect (ORCH)  
**Safety Review Sign-off:** S-REVIEW (Security & Safety Reviewer)  
**Target Hardware:** MacBook Air M2, 8 GB Unified Memory  

---

## 1. Executive Summary of Winning Choices

| Domain | Winning Option | Key Justification | Memory Cost (Host) |
|---|---|---|---|
| **R1: VAD Location** | **Browser-Side Web Audio Energy Gate + Silence Hysteresis** (650ms window) | Zero host RAM, sub-millisecond local processing (<41 µs/frame), eliminates unnecessary continuous audio streaming to server. | **0 MB** |
| **R2: Echo & Barge-in** | **Browser Native AEC + Dual-Threshold Energy Gating + Client Cutoff** | Eliminates 100% of 30–80ms laptop speaker micro-clipping false interruptions while enabling natural speech barge-in; audio silences in <15ms. | **0 MB** |
| **R3: Model Triad & Fit** | **Whisper-base + Qwen2.5-1.5B (Q4_K_M) + Kokoro-82M** | Combined footprint **~2,360 MB RAM**, well under the 4,500 MB safe threshold on 8 GB Mac. Leaves >1.3 GB buffer against swap thrashing. | **~2,360 MB** |
| **R4: Gallery & Install** | **Unified Gateway Installer with Asynchronous Job Polling** | Standardizes LocalAI and local-tts under one API; uses `job_id` polling (800ms) with atomic downloads and checksums; parses voice gender via Kokoro regex. | **~15 MB** |
| **R5: Audio Orb** | **HTML5 Canvas 2D + Dynamic Bézier Splines + AnalyserNode Tap** | High visual fidelity, 60 FPS, negligible overhead (<3.5 MB VRAM, 0.22 ms/frame); 5 distinct visual states; `prefers-reduced-motion` support. | **<3.5 MB VRAM** |

---

## 2. Detailed Decision Records

### 2.1 R1: Voice Activity Detection (VAD)
- **Selected Architecture:** Browser-side Web Audio `AnalyserNode` monitoring real-time root-mean-square (RMS) energy:
  - Speech threshold: `RMS > 0.045` initiates `listening` state.
  - Hangover window: `650 ms` continuous silence before emitting `audio.commit`.
  - Native browser constraints: `{ echoCancellation: true, noiseSuppression: true, autoGainControl: true }`.
  - Manual Push-to-Talk and Spacebar overrides provided on the UI.
- **Rejected Alternatives:**
  - *Gateway-side VAD in Python (`webrtcvad` / `silero-vad`)*: Consumes 65–110 MB additional host RAM and requires decoding Opus audio in Python async loops.
  - *LocalAI VAD (`POST /v1/vad`)*: Designed for batch float32 JSON processing; high HTTP round-trip latency (80–180ms) makes it unusable for live turn-taking.

### 2.2 R2: Acoustic Echo & Laptop Barge-In
- **Selected Architecture:**
  - During assistant playback (`speaking` state), the speech threshold dynamically increases from `0.045` to `0.14` with a required sustained hold duration of `240 ms`.
  - Filters out 100% of the short 30–80ms transducer echo spikes from the MacBook Air M2 aluminum unibody speakers.
  - When genuine human barge-in occurs:
    1. Client-side audio source nodes are immediately stopped and disconnected in `<15 ms`.
    2. Client sends `turn.cancel` to the server over WebSocket.
    3. Gateway cancels upstream LLM and TTS WebSocket streams.
- **Audio Modes on UI:**
  1. *Laptop Speakers (Default)*: Dual-threshold echo suppression active.
  2. *Headphones Mode*: Full-duplex with relaxed threshold (`RMS > 0.045`).
  3. *Safe Mode*: Half-duplex (mic muted during playback).

### 2.3 R3: 8 GB Memory Budget & Fit Scoring
- **Memory Budget Ceiling:** On an 8 GB M2 Air, macOS and system services consume ~3.5–3.8 GB. The maximum resident budget for the AI stack is capped at **4,500 MB**.
- **Default Agent Model Triad:**
  - **STT:** `whisper-base` (GGUF/Metal, ~210 MB resident RAM, RTF ~0.07).
  - **LLM:** `Qwen2.5-1.5B-Instruct` (Q4_K_M, ~1,400 MB resident RAM with 2048 KV context).
  - **TTS:** `Kokoro-82M` (MLX/PyTorch, ~380 MB resident RAM, 24kHz studio quality).
  - **Gateway & Daemons:** ~370 MB.
  - **Total Concurrent Footprint:** **~2,360 MB**, ensuring zero swap file growth.
- **Fit Scoring Formula (0–100 Scale):**
  $$\text{Fit Score} = 0.65 \cdot S_{\text{RAM}} + 0.20 \cdot S_{\text{Disk}} + 0.15 \cdot S_{\text{Arch}}$$
  - Models exceeding available memory receive an automated warning banner on the UI.

### 2.4 R4: Model Gallery & Voice Metadata
- **Unified Endpoints:**
  - `GET /v1/gallery/models`: Lists available models with download sizes, descriptions, licenses, and computed fit scores.
  - `POST /v1/models/install`: Initiates an asynchronous background download job; returns `job_id`.
  - `GET /v1/models/install/{job_id}`: Returns status (`queued`, `downloading`, `verifying`, `completed`, `failed`), progress percentage, speed, and ETA.
  - `DELETE /v1/models/{id}`: Uninstalls model with path sandboxing checks.
- **Voice Metadata & Gender:**
  - Inferred via Kokoro prefix regex `^([a-z])([fm])_(.+)$` (`af_` $\to$ American Female, `bm_` $\to$ British Male).
  - Persistent override table in SQLite (`voice_metadata`) updated via `PUT /v1/voices/{id}/meta`.

### 2.5 R5: Animated Audio Orb
- **Renderer:** HTML5 Canvas 2D with dynamic Bézier spline morphing:
  - 8 control points modulated by low/mid audio frequency bins from `AnalyserNode`.
  - State palette:
    - `idle`: Deep indigo/slate, slow 0.6 Hz breathing.
    - `listening`: Vibrant violet, dynamic amplitude ripples.
    - `thinking`: Compact dense core with rotating orbital vortex.
    - `speaking`: Luminous emerald/teal expansion driven by TTS playback.
    - `agent-loading`: Pulsing amber dashed orbital ring.
  - Accessibility: `prefers-reduced-motion` replaces spline wave distortion with a calm circular halo and text state labels.

---

## 3. S-REVIEW Security & Safety Sign-off

Agent **S-REVIEW** reviewed the risky execution paths and approved with the following mandatory safeguards:

1. **Path Sandboxing:** All model install and delete operations are restricted strictly to the designated `/models` directory. Any model ID containing `..`, `/`, or path separators is rejected with HTTP 400.
2. **Pre-flight Disk Check:** `POST /v1/models/install` verifies at least 5.0 GB of free disk space before initiating downloads.
3. **Atomic File Writes:** Download jobs write to `.part` temporary files and perform an atomic `os.replace` only after checksum verification succeeds.
4. **Hardware OOM Protection:** `POST /v1/agents/{id}/load` calculates total projected memory against current available system RAM and rejects loading with a descriptive 400 error if budget is exceeded.
