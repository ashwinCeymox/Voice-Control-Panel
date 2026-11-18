# Third-Party Notices and Licenses

This project incorporates, downloads, or interfaces with open-source software, models, and assets governed by third-party licenses. A summary of these components and their respective licenses is detailed below.

---

## 1. AI Models and Weights

### Silero VAD
- **Author**: Silero Team
- **Source**: [snakers4/silero-vad](https://github.com/snakers4/silero-vad)
- **License**: MIT
- **Usage**: Real-time client-side voice activity detection in browser audio pipelines.

### Sherpa-ONNX Zipformer
- **Author**: Next-gen Kaldi / k2-fsa Team
- **Source**: [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
- **Model**: `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17`
- **License**: Apache-2.0
- **Usage**: Server-side real-time streaming speech-to-text.

### Kokoro TTS
- **Author**: hexgrad
- **Source**: [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)
- **License**: Apache-2.0
- **Usage**: Speech synthesis voice weights for local-tts MLX server.

### Whisper STT
- **Author**: OpenAI / Georgi Gerganov (whisper.cpp)
- **Source**: [ggerganov/whisper.cpp](https://github.com/ggerganov/whisper.cpp)
- **License**: MIT
- **Usage**: Non-streaming batch speech-to-text fallback.

### Llama 3.2 1B Instruct
- **Author**: Meta Platforms, Inc.
- **Source**: [meta-llama/Llama-3.2-1B-Instruct](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct)
- **License**: Llama 3.2 Community License Agreement
- **Usage**: Local LLM conversational turn generation.

---

## 2. Server Engines and Execution Runtimes

### LocalAI
- **Author**: Ettore Di Giacinto (mudler) and contributors
- **Source**: [mudler/LocalAI](https://github.com/mudler/LocalAI)
- **License**: MIT
- **Usage**: Local orchestration of Whisper STT and Llama LLM inference.

### local-tts
- **Author**: Saúl Ibarra Corretgé (saghul) and contributors
- **Source**: [saghul/local-tts](https://github.com/saghul/local-tts)
- **License**: MIT
- **Usage**: Apple MLX-accelerated text-to-speech HTTP and WebSocket server.

### Sherpa-ONNX Engine
- **Author**: Next-gen Kaldi Team (k2-fsa)
- **Source**: [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
- **License**: Apache-2.0
- **Usage**: High-throughput streaming ASR engine.

### ONNX Runtime / ONNX Runtime Web
- **Author**: Microsoft Corporation
- **Source**: [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime)
- **License**: MIT
- **Usage**: WebAssembly-accelerated execution of Silero VAD within the client browser.

---

## 3. Core Software Libraries

| Library | Ecosystem | License | Primary Purpose |
|---|---|---|---|
| **FastAPI** | Python | MIT | Gateway REST and WebSocket orchestration |
| **Starlette** | Python | BSD-3-Clause | Underlying ASGI web server framework |
| **Uvicorn** | Python | BSD-3-Clause | ASGI server runner |
| **Argon2-cffi** | Python | MIT | Memory-hard cryptographic hashing for admin authentication |
| **Aiosqlite** | Python | MIT | Asynchronous SQLite database interface |
| **Httpx** | Python | BSD-3-Clause | Async HTTP client for internal engine routing |
| **React** | Node.js | MIT | Frontend reactive user interface framework |
| **Vite** | Node.js | MIT | Frontend build tooling and module bundling |
| **Lucide React** | Node.js | ISC | Frontend SVG icon suite |
| **Tailwind CSS** | Node.js | MIT | Utility-first CSS presentation system |
| **@ricky0123/vad-web** | Node.js | MIT | Client browser audio stream frame segmentation |
