# LocalAI Setup Guide for macOS (Apple Silicon)

This guide walks through setting up [LocalAI](https://github.com/mudler/LocalAI) on an 8 GB Apple Silicon Mac (M1/M2/M3/M4) to power local Speech-to-Text (Whisper) and Large Language Models (LLM) for the Local Voice Agent platform.

---

## 1. Prerequisites

- **Hardware**: Apple Silicon Mac (M1/M2/M3/M4) with at least 8 GB unified memory.
- **Software**: macOS 13+ (Ventura, Sonoma, Sequoia), Homebrew, Docker Desktop (optional, for containerized deployment).

---

## 2. Installation Options

### Option A: Native macOS Binary / Build (Recommended for Apple Silicon Acceleration)
LocalAI can be run natively or compiled to leverage Apple Metal (via llama.cpp backend).

1. **Clone and Build LocalAI**:
   ```bash
   git clone https://github.com/mudler/LocalAI.git
   cd LocalAI
   make localai
   ```

2. **Run LocalAI**:
   ```bash
   BUILD_TYPE=metal ./localai run --models-path ./models
   ```

### Option B: Docker Container on macOS
If running via Docker Desktop, ensure sufficient RAM (at least 6 GB) is allocated to Docker in Docker Desktop settings.

```bash
docker run -d -p 8080:8080 \
  --name localai \
  -v $HOME/localai-models:/models \
  --env DEBUG=true \
  --env MODELS_PATH=/models \
  mudler/localai:latest-cpu
```

---

## 3. Downloading and Configuring Models

### 3.1 Speech-to-Text (STT): Whisper
LocalAI supports Whisper models natively via the whisper backend.

1. Create a model configuration file in your models directory (`$HOME/localai-models/whisper-1.yaml`):
   ```yaml
   name: whisper-1
   backend: whisper
   parameters:
     model: base.en
   ```
2. LocalAI will automatically download `base.en` on first invocation, or you can pre-place weights in the models directory.

### 3.2 Local LLM: Llama-3.2-1B / Qwen2.5-1.5B (Optimized for 8 GB RAM)
For an 8 GB Mac, 1B to 3B parameter models quantized to 4-bit (`Q4_K_M`) or 5-bit offer lightning-fast inference and low memory footprints (~1 GB to 2 GB VRAM/RAM).

1. Create `llama-3.2-1b.yaml` in your models directory:
   ```yaml
   name: llama-3.2-1b
   backend: llama
   parameters:
     model: Llama-3.2-1B-Instruct-Q4_K_M.gguf
     temperature: 0.7
     top_p: 0.9
     max_tokens: 512
   ```
2. Download GGUF weights into models path:
   ```bash
   cd $HOME/localai-models
   curl -L -o Llama-3.2-1B-Instruct-Q4_K_M.gguf https://huggingface.co/unsloth/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf
   ```

---

## 4. Environment Variables & Configuration

Add these settings to your backend `.env` file (see [.env.example](../.env.example)):

```env
LOCALAI_BASE_URL=http://localhost:8080
LOCALAI_STT_MODEL=whisper-1
LOCALAI_LLM_MODEL=llama-3.2-1b
```

---

## 5. Verification Curl Commands

### 5.1 Verify STT Endpoint
```bash
curl http://localhost:8080/v1/audio/transcriptions \
  -H "Authorization: Bearer no-key" \
  -F "model=whisper-1" \
  -F "file=@tests/fixtures/sample_audio.wav"
```

### 5.2 Verify LLM Endpoint (OpenAI Compatible)
```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-3.2-1b",
    "messages": [{"role": "user", "content": "Hello! Are you running locally?"}],
    "temperature": 0.7
  }'
```
