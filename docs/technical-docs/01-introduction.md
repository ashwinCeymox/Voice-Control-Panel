# 1. Introduction

## 1.1 Purpose

This document provides a comprehensive, end-to-end technical reference for the Voice AI Platform: architecture, data structures, public and internal interfaces, security boundaries, testing frameworks, and production deployment procedures on dedicated bare-metal infrastructure.

It is designed to serve as the definitive single source of truth for engineering onboarding, operational maintenance, SRE incident response, and independent security audits.

---

## 1.2 Product Summary

The Voice AI Platform is a **high-performance, self-hosted, multi-engine voice intelligence system** paired with an administrative React control panel.

The core gateway chains three separate AI modalities into a unified conversational voice turn:

1. **Speech-to-Text (STT):** Ingests raw user vocal audio and produces accurate normalized text transcripts. Supports both streaming real-time interim recognition (via Sherpa-ONNX Zipformer) and batch file transcription (via LocalAI Whisper).
2. **Large Language Model (LLM):** Processes transcribed speech alongside dynamic system instructions and conversation history to stream synthesized response tokens (via LocalAI llama.cpp).
3. **Text-to-Speech (TTS):** Chunks streaming LLM text into phrases and synthesizes human-like speech output at 24 kHz (via local-tts Kokoro).

The gateway exposes these capabilities individually (OpenAI-compatible `/v1/chat/completions` and `/v1/audio/transcriptions`, ElevenLabs-style `/v1/text-to-speech/{id}/stream`) and combined into an integrated turn pipeline (`POST /v1/voice/turn` and `WS /v1/voice/stream`).

A responsive React control panel serves interactive playgrounds, voice assistant interaction, custom agent configuration, unified model registry management, and key administration.

---

## 1.3 Scope Boundaries

| In Scope | Out of Scope |
|---|---|
| **FastAPI Gateway:** Routing, authentication, concurrency semaphores, quotas, and turn orchestration | **Third-party cloud dependencies:** No external dependencies on OpenAI, ElevenLabs, or Anthropic |
| **Native Inference Engines:** LocalAI, local-tts, and Sherpa-ONNX streaming STT | **Commercial payment processing:** No credit card billing or Stripe integrations |
| **Data Storage:** SQLite database with Write-Ahead Logging (WAL) and automated checkpoints | **Native mobile apps:** The client interface is a responsive PWA/web application |
| **Security Controls:** HMAC-SHA256 key hashing, nftables loopback isolation, Caddy TLS, Fail2ban | **Multi-tenant cloud SaaS:** Designed for dedicated enterprise hosting on bare metal |
| **Deployment:** Automated bare-metal deployment on Hetzner with NVIDIA GPU acceleration | **Containerization overhead:** Runs bare metal (no Docker) to ensure direct GPU VRAM access |

---

## 1.4 Key Capabilities

- **Real-Time Voice Assistant:** Full-duplex voice conversation with low first-packet audio latency (~350–550 ms), live partial transcription display, and responsive barge-in (interruption) handling.
- **Batch Voice Turns:** Multi-modal single-turn HTTP API accepting audio recordings and returning both textual and acoustic replies.
- **Independent Playgrounds:** Specialized testing pages for prompt engineering, audio transcription, and voice synthesis.
- **Custom Agent Personalities:** Save, edit, and duplicate specialized agent personas bundling system prompts, model selections, voice profiles, and VAD thresholds.
- **Persistent Model Manifest:** Dynamically synchronized JSON manifest (`data/installed_models_manifest.json`) unifying models across STT, LLM, and TTS providers.
- **API Key Lifecycle & Governance:** Dual-role security model (`admin` and `caller`), 6 granular permission scopes, per-key rate limits, daily token/audio quotas, graceful dual-key rotation, and instant revocation.
- **Telemetry & Observability:** Thread-safe Prometheus `/metrics` exposition, granular usage event capture, per-turn latency breakdowns (`stt_ms`, `llm_ms`, `tts_ms`), and live hardware resource gauges (RAM and VRAM).
