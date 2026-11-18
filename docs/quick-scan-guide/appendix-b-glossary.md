# Appendix B. Glossary (1 minute)

> **TL;DR:** Essential voice, AI, and infrastructure terminology explained in one line each.

---

| Term | Definition |
|---|---|
| **STT** | **Speech-to-Text:** Converting human vocal audio into machine-readable text. |
| **TTS** | **Text-to-Speech:** Converting written text into natural human-sounding synthesized speech. |
| **LLM** | **Large Language Model:** Neural network (e.g. Llama 3) that generates conversational responses. |
| **VAD** | **Voice Activity Detection:** Algorithm (e.g. Silero) detecting when a user starts and stops talking. |
| **PCM16** | **Pulse-Code Modulation 16-bit:** Uncompressed raw digital audio format (`pcm_s16le`). |
| **Barge-in** | The ability for a user to interrupt the assistant mid-sentence and have speech output halt immediately. |
| **Ticket** | Single-use, cryptographically secure 60-second token used to safely authenticate WebSockets without leaking API keys in URLs. |
| **Scope** | Granular permission attached to an API key (e.g. `llm`, `stt`, `tts`, `voice_stream`). |
| **Gateway** | The central FastAPI Python server managing API routing, security, and pipeline orchestration. |
| **Zipformer** | Modern streaming neural network architecture in Sherpa-ONNX offering fast streaming speech recognition. |
| **WAL** | **Write-Ahead Logging:** SQLite performance mode allowing simultaneous reads while a write occurs. |
| **Quarantine** | Moving junk files to `.trash/<run-id>/` for 7 days before permanent deletion, enabling full undo/restore. |
