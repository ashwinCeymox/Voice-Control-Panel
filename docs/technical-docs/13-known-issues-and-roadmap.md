# 13. Known Issues and Roadmap

## 13.1 Operational Characteristics & Technical Realities

The platform operates under specific architectural characteristics and hardware constraints that system administrators and developers must consider **[Confirmed]**:

1. **Whisper Batch Processing vs Real-Time Streaming:**
   - The LocalAI `whisper.cpp` backend operates strictly in batch transcription mode; it requires the complete audio utterance before beginning decoding and cannot produce streaming interim tokens.
   - Real-time partial transcription is provided exclusively by the dedicated Sherpa-ONNX streaming STT engine (`services/streaming_stt/server.py`). If this engine is absent or fails, the platform falls back to batch Whisper, introducing latency until speech finishes.
2. **Cold-Start Model Weight Transfer Latency:**
   - Loading large LLM or Whisper models into GPU VRAM takes 3 to 8 seconds on initial startup.
   - **Mitigation:** The gateway lifespan (`app/main.py`) executes an automatic background pre-warming sequence upon launch, running a minimal STT and LLM query to ensure weights are resident before user traffic arrives.
3. **Acoustic Feedback & Echo on Open Laptop Speakers:**
   - In browser environments using open laptop speakers without headphones, loud audio playback from Kokoro TTS can bleed back into the microphone, triggering premature VAD interrupts or looping speech.
   - **Mitigation:** Web Audio AEC (Acoustic Echo Cancellation) is enabled by default in `vadController.ts`, and the client barge-in controller immediately cancels playback upon new speech detection. For optimal quality, headphones or directional microphones are recommended.
4. **Hardware Runtime Portability:**
   - On macOS, speech synthesis relies on Apple Metal Performance Shaders via Kokoro MLX.
   - On Linux bare-metal hosts (e.g. Hetzner), Kokoro requires either `kokoro-onnx` or the PyTorch CUDA backend. The deployment script configures appropriate dependencies for Linux GPU targets.
5. **Single-Worker In-Memory State Coherence:**
   - To prevent state drift across in-memory token buckets, rate limiters, and single-use WebSocket tickets, Uvicorn is strictly configured to run with a single worker process (`--workers 1`). Multi-worker scaling requires transitioning state to Redis.

---

## 13.2 Strategic Technical Roadmap

The platform development roadmap is divided into three phased execution tiers:

```
[ Phase 1: Production Hardening ]
  - Complete Hetzner bare-metal deployment with NVIDIA CUDA
  - Transition local-tts to PyTorch/ONNX CUDA on Linux
  - Implement off-server S3 backup replication for voice_platform.db
        |
        v
[ Phase 2: Enhanced Voice Capabilities ]
  - Full session conversation export (MP3/WAV audio + JSON transcripts)
  - Dynamic STT model switching without daemon restarts
  - Per-key concurrency queues with explicit HTTP 429 Retry-After headers
  - Server-side VAD engine for standalone API clients without browser Web Audio
        |
        v
[ Phase 3: Distributed Multi-Node Clustering ]
  - Migrate SQLite database to highly available PostgreSQL cluster
  - Migrate ticket validation and rate limiting to Redis Sentinel / Cluster
  - Deploy N+1 Gateway replicas behind Caddy / HAProxy load balancers
  - Segregated GPU worker pools for high-throughput batch and streaming workloads
```

### Prioritized Deliverables Table

| Target Phase | Feature / Component | Description | Architecture Impact |
|---|---|---|---|
| **Phase 1** | **Linux CUDA TTS** | Complete containerized or systemd PyTorch Kokoro engine for Ubuntu 24.04 bare-metal. | High |
| **Phase 1** | **Off-Site Backups** | Encrypted rsync / rclone replication of `data/backups/` snapshots to remote cloud storage. | Medium |
| **Phase 2** | **Session History Export** | REST endpoint allowing users to download complete audio recordings and timestamped transcripts. | Medium |
| **Phase 2** | **Dynamic STT Switching** | Support runtime switching between multiple Sherpa-ONNX streaming models without restarting port 8088. | Medium |
| **Phase 2** | **Server-Side VAD** | Silero VAD running directly inside the gateway to support bare telephony or mobile audio streams. | High |
| **Phase 3** | **PostgreSQL & Redis** | Externalize storage and ephemeral ticketing to enable multi-worker horizontal gateway scaling. | Major |
| **Phase 3** | **Multi-GPU Cluster** | Distribute STT, LLM, and TTS onto dedicated discrete GPU cards or separate server nodes. | Major |
