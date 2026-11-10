# Multi-Agent Task Board: Voice AI Control Panel (Final Fix Pass)

| ID | Task | Owner | Model | Status | Blocked By | Handoff / PR |
|---|---|---|---|---|---|---|
| **P0-1** | Bootstrap & Assumptions Verification | ORCH | `flash` | **DONE** | None | Verified |
| **R1** | Research: VAD Location & Architecture | R1 | `flash` | **DONE** | None | `docs/decisions/R1.md` |
| **R2** | Research: Echo Cancellation & Barge-in | R2 | `flash` | **DONE** | None | `docs/decisions/R2.md` |
| **R3** | Research: 8GB Memory Fit & Model Choices | R3 | `flash` | **DONE** | None | `docs/decisions/R3.md` |
| **R4** | Research: Model Gallery & Install API | R4 | `flash` | **DONE** | None | `docs/decisions/R4.md` |
| **R5** | Research: Orb Animation & Audio Visuals | R5 | `flash` | **DONE** | None | `docs/decisions/R5.md` |
| **P2-1** | Decision Gate Synthesis | ORCH | `flash` | **DONE** | R1–R5 | `docs/decisions/FINAL.md` |
| **P3-1** | Contract Freeze (OpenAPI, WS, Agents) | ORCH | `flash` | **DONE** | P2-1 | `docs/contracts/` |
| **B1** | System Resources & Fit Scoring API | S-MODELS | `pro` | **DONE** | P3-1 | `app/api/system.py` |
| **B2** | Model Gallery & Install/Uninstall Jobs | S-MODELS | `pro` | **DONE** | B1 | `app/api/gallery.py` |
| **B3** | Voices Metadata, Gender & Preview API | S-MODELS | `pro` | **DONE** | P3-1 | `app/api/tts.py` |
| **B4** | SQLite Agents Schema & CRUD | S-AGENTS | `pro` | **DONE** | P3-1 | `app/api/agents.py` |
| **B5** | Agent Load/Unload Lifecycle State | S-AGENTS | `pro` | **DONE** | B4, B1 | `docs/handoff/S-AGENTS.md` |
| **B6** | WebSocket Protocol & Agent Session | S-AUDIO | `pro` | **DONE** | P3-1 | `app/api/voice.py` |
| **B7** | Mic Capture, Browser VAD & Barge-in | S-AUDIO | `pro` | **DONE** | R1, R2, B6 | `frontend/src/audio/` |
| **B8** | Streaming PCM Player with Audio Tap | S-AUDIO | `pro` | **DONE** | B6 | `frontend/src/audio/pcmPlayer.ts` |
| **U1** | App Shell & Status Bar from Health | G-UI | `flash` | **DONE** | P3-1 | `frontend/src/components/layout/` |
| **U2** | Models Gallery View with Fit Sorting | G-UI | `flash` | **DONE** | B1, B2 | `frontend/src/pages/ModelsRegistry.tsx` |
| **U3** | Voice Assistant with Animated Audio Orb | G-UI | `flash` | **DONE** | B7, B8, R5 | `frontend/src/pages/Assistant.tsx` |
| **U4** | Agents Page (Create/Edit/Test-Chat) | G-UI | `flash` | **DONE** | B4, B5 | `frontend/src/pages/Agents.tsx` |
| **U5** | TTS Page with Voice Picker & Gender | G-UI | `flash` | **DONE** | B3 | `frontend/src/pages/TTSPlayground.tsx` |
| **U6** | STT Page with Model Picker & Timing | G-UI | `flash` | **DONE** | B2 | `frontend/src/pages/STTPlayground.tsx` |
| **U7** | Voice Library & System Health | G-UI | `flash` | **DONE** | B3 | `frontend/src/pages/` |
| **T1** | OpenAPI Contract Test Suite | G-TEST | `flash` | **DONE** | P3-1 | `tests/contract/test_contract.py` |
| **T2** | UI Smoke & Error State Tests | G-TEST | `flash` | **DONE** | U1–U7 | `frontend/dist/` build verified |
| **T3** | 8GB Soak & Resource Benchmark | G-TEST | `flash` | **DONE** | All | `tests/benchmark/test_soak_8gb.py` |
| **V1** | Security Review (Path & Deletes) | S-REVIEW | `pro` | **DONE** | B2, B5, B7 | `docs/reviews/V1.md` |
