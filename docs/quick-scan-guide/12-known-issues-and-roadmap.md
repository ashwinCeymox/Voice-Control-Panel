# 12. Known Issues and Roadmap (2 minutes)

> **TL;DR:** Core security findings are completely resolved. Current operational focus is on Linux TTS engine parity, multi-model streaming STT management, and scaling to multi-worker Redis architectures when load demands.

---

## 12.1 Current Operational Characteristics

- **LocalAI Whisper Batch STT:** Whisper via LocalAI returns full audio transcriptions after speech ends, not live streaming tokens. Live partial transcription is handled natively by Sherpa-ONNX on port 8088.
- **Model Cold Start:** First request to LocalAI or Kokoro after a cold service start takes 3–8 seconds while model weights map into memory. Gateway startup pre-warms the default LLM.
- **Laptop Speaker Echo:** Acoustic feedback occurs when users play audio on open speakers while testing near high-gain laptop microphones. Echo cancellation is enabled in the browser; headphones are recommended during development.
- **Single Gateway Worker Requirement:** Single-worker process model is intentional to ensure memory-resident ticket pools and concurrency semaphores remain consistent without requiring external Redis instances.

---

## 12.2 Roadmap Priorities

| Priority | Feature / Objective | Architecture Impact |
|---|---|---|
| **High** | **Linux CUDA TTS Service:** Deploy Kokoro PyTorch CUDA runtime on Hetzner to replace macOS MLX engine. | Zero API changes; gateway uses existing OpenAI/ElevenLabs TTS protocol. |
| **High** | **Dynamic Multi-Model Streaming STT:** Dynamic routing of WebSocket connections across multiple model instances on loopback ports. | Port multiplexing inside `streaming_stt_client.py`. |
| **Medium** | **Session History UI & Audio Export:** Enable users to view past voice turns and export transcripts from the control panel. | Reads existing `turns` and `sessions` SQLite tables. |
| **Medium** | **Per-Key Concurrency Queuing:** Add explicit queue priority for premium caller keys. | Enhanced `BackendLimiter` queue priorities. |
| **Later** | **Distributed Scaling (Stage 3):** Scale to multiple gateway workers across multiple GPU nodes. | Requires external Redis for tickets/quotas and PostgreSQL for data. |
