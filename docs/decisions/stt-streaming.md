# Architectural Decision Record: STT Streaming Strategy (Phase 1)

**Status:** APPROVED  
**Date:** November 2026  
**Context:** Migration Phase 1 (Cloud Readiness & Internal Callers)  
**Deliverable:** Evaluation of batch vs streaming STT for internal service callers and voice platform.

---

## 1. Decision

**For Phase 1 (Internal Callers & Raw Endpoints), retain Batch STT via `STTProvider` (LocalAI Whisper / whisper.cpp) and defer live streaming STT.**

### Rationale & Trigger Evaluation:
1. **Target Callers in Phase 1:** Internal projects calling raw endpoints (`/v1/chat/completions`, `/v1/audio/transcriptions`, `/v1/text-to-speech`). The `/v1/audio/transcriptions` OpenAI-compatible contract is inherently a batch endpoint (audio file upload -> complete transcript JSON).
2. **Turn Latency Budget:** In P0 benchmark testing on 3-second audio clips, Whisper Base processed audio in ~410-480 ms (RTF ~0.14). The entire speech-to-text stage completes well under the 800 ms threshold defined in `migration.md`.
3. **No Live Partial Requirement:** Neither internal caller application currently requires interim word displays or live live-captioning tokens.
4. **Clean Abstraction:** With the `STTProvider` ABC implemented in Phase P3 (`app/providers/stt_base.py`), the Gateway is completely decoupled from the underlying STT engine. A dedicated streaming provider or faster-whisper backend can be swapped in without modifying any routing or caller logic.

---

## 2. Evaluation of Future Spike Options (S1 - S4)

| Option | Architecture | Feasibility | Latency / Accuracy Impact | Recommendation |
|---|---|---|---|---|
| **S1: Speculative Transcription** | Trigger STT after 350-400 ms VAD silence; cancel if speech resumes | High | Saves ~300 ms perceived turnaround time; increases STT calls by ~15% on false pauses | Recommended for Phase 2 when external voice pipeline is exposed |
| **S2: Rolling-Window Partials** | 1-second rolling audio buffers dispatched every 300 ms for UI | Medium | Provides interim UI text, but multiplies STT compute by 3x-4x | Rejected for Phase 1 (too heavy on CPU at 5 concurrent sessions) |
| **S3: LocalAI Streaming Engine** | NeMo / llama-cpp streaming ASR in LocalAI | Low | Experimental in LocalAI v2.26; high memory footprint (~2.5 GB) | Rejected due to stability concerns |
| **S4: Dedicated faster-whisper / whisper.cpp** | Standalone C++ service implementing `STTProvider` | High | Zero HTTP/multipart overhead; RTF ~0.09 on Linux CPU; ~25% faster than LocalAI wrapper | Recommended replacement when migrating to dedicated Linux cluster |

---

## 3. Implementation Plan for Phase 2

If a caller requires live partial streaming or if server-side VAD turns are exposed to external web clients:
1. Implement `FasterWhisperStreamingProvider(STTProvider)` with WebSocket duplex streaming.
2. Enable Speculative Silence Dispatch (S1) in `VoiceOrchestrator`.
