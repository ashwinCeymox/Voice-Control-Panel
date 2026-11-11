# Refurbishment Architectural Decisions: Live Transcription & Turn-Taking

**Date:** 2026-11-11  
**Status:** Approved  

---

## Decision R1: Live Transcription Architecture

### Context
User requested real-time transcription on the voice page during voice turns on an 8 GB unified memory M2 MacBook Air running LocalAI Whisper and local-tts Kokoro concurrently.

### Options Evaluated
1. **Option A: Rolling Re-transcription (1 Hz)**
   - *Pros:* Generates actual partial text every second.
   - *Cons:* 1 Hz Whisper inferences cause CPU/GPU spikes, competing with Kokoro/LLM memory buffers and causing thermal throttling on M2 Air.
2. **Option B: VAD-Driven Turn Streaming with Immediate Interim Visuals & Fast Final Commit**
   - *Pros:* Zero overhead during speech capture. Whisper runs once upon VAD silence endpointing (takes ~120ms on Apple Silicon Metal). UI displays a responsive interim transcription bubble with pulsing waveform and speech status, instantly transitioning to the completed transcription and streaming LLM tokens.
   - *Cons:* Interim words are not displayed word-by-word before pause, but latency to final text is negligible (< 150ms).

### Winner
**Option B.** Perfect fit for the 8 GB M2 thermal and memory envelope. Provides crisp visual feedback while speaking, followed by instant transcription display.

---

## Decision R2: Autonomous Turn-Taking & Echo-Safe Barge-In

### Context
The previous UI featured a manual "Stop Speaking" / "Done Speaking" button because `stopListening()` previously closed the browser `AudioContext` and mic tracks after each turn.

### Winner
1. **Persistent Session Context**: `startSession()` opens the mic stream and WebSocket once. The session stays active across multiple conversational turns until user clicks "End Session" or navigates away.
2. **VAD Silence Endpointing**:
   - `onSpeechStart`: Activates `listening` state.
   - Trailing silence reaches `agent.vad.min_silence_ms` (default 650ms) → `onSpeechEnd` dispatches `audio.commit` over WebSocket.
   - Transitions state to `thinking`. Mic stream remains open.
3. **Barge-In (Interruption)**:
   - While PCM player is playing audio, VAD enters `playbackState = true` with dual-threshold energy detection (0.14 for laptop speakers, 0.06 for headphones) and a 200ms hold window.
   - On sustained user speech: `onBargeIn` halts PCM player, sends `turn.cancel`, transitions immediately to `listening`, and captures the new utterance.
4. **Zero Turn-Taking Buttons**: "Done speaking" and "Stop speaking" buttons are permanently eliminated. Controls consist of "Start Session" / "End Session" and a "Mute Mic" toggle.
