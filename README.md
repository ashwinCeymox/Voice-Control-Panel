# Local Voice Agent Platform

A production-grade, fully local Voice AI platform featuring a **FastAPI gateway**, **LocalAI** (STT & LLM), **`saghul/local-tts`** (Kokoro TTS), and a **React Web Audio UI** with real-time WebSocket streaming and voice interruption.

---

## Architecture & Multi-Agent Allocation

This project is built using a strict multi-model orchestration policy defined in [`AGENT.md`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/AGENT.md):
- **`GEMINI-3.8-HIGH-flash`**: Scaffolding, FastAPI routes, database CRUD, UI components, provider adapters, and test suites.
- **`SONNET-4.6`**: Core protocol contracts (`docs/api-contract.md`), real-time WebSocket streaming engine (`app/services/voice_orchestrator.py`), and Web Audio API 24kHz PCM ring buffers with interruption handling.

See complete design documentation in the [`docs/`](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/docs/) directory:
- [Architecture Overview](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/docs/architecture.md)
- [API Contract & WebSocket Protocol](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/docs/api-contract.md)
- [LocalAI Setup Guide](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/docs/localai-setup.md)
- [Local TTS Setup Guide](file:///Users/hashhwin047icloud.com/Downloads/test-pro/arch/local-voice-agent/docs/local-tts-setup.md)

---

## Quickstart Guide

### 1. Backend Setup & Running
```bash
# Clone and configure environment
cp .env.example .env

# Run FastAPI gateway
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend Setup & Running
```bash
cd frontend
npm install
npm run dev
```

### 3. Running Tests
```bash
python3 -m pytest
```

---

## API & WebSocket Testing Snippets

### Synchronous Voice Turn (`POST /v1/voice/turn`)
```bash
curl -X POST http://localhost:8000/v1/voice/turn \
  -H "Authorization: Bearer test-token" \
  -F "audio=@tests/fixtures/sample_audio.wav" \
  -F "voice=af_heart"
```

### WebSocket Streaming Connection
Connect via WebSockets for real-time bi-directional audio streaming with interruption support:
```bash
websocat "ws://localhost:8000/v1/ws/voice?token=test-token"
```
