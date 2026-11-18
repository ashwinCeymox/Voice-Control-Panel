# 3. Technology Stack and Libraries

## 3.1 Backend Runtimes & Frameworks **[Confirmed]**

The gateway is built on Python 3.12, leveraging asynchronous I/O and modern static typing:

| Package / Tool | Version | Purpose & Rationale |
|---|---|---|
| **Python** | `3.12.11` | Primary runtime; includes standard library `tomllib`, performance enhancements, improved exception groups |
| **FastAPI** | `0.162.2` | High-performance async ASGI web framework with dependency injection and WebSocket routing |
| **Pydantic** | `2.14.3` | Schema validation and fast serialization backed by compiled Rust core |
| **Uvicorn** | `0.54.0` | Production ASGI web server running the gateway application |
| **aiosqlite** | `0.20.0` | Async SQLite database wrapper with thread isolation and WAL mode execution |
| **httpx** | `0.28.1` | Async HTTP/1.1 and HTTP/2 client for internal loopback communication with LocalAI and local-tts |
| **pytest** | `9.2.0` | Test runner powering the 207-test automated verification suite |
| **pytest-asyncio** | `1.4.1` | Native async test execution fixture support |
| **anyio** | `4.16.0` | Concurrency abstraction layer for asynchronous networking |

---

## 3.2 Native AI Inference Engines **[Confirmed]**

| Engine | Upstream Core | Acceleration / Backend | Ports | Host Isolation |
|---|---|---|---|---|
| **LocalAI** | `llama.cpp` + `whisper.cpp` | Apple Silicon Metal (macOS) / NVIDIA CUDA 12+ (Linux) | `:8080` | Internal `LOCALAI_API_KEY` + nftables |
| **local-tts** | Kokoro TTS (82M params) | Apple MLX (`local-tts` on Mac) / PyTorch CUDA (Linux) | `:8880` | Loopback socket + nftables |
| **Streaming STT** | Sherpa-ONNX | Zipformer streaming acoustic model (`sherpa-onnx` v1.10+) | `:8088` | Loopback socket + nftables |

> **Cross-Platform Compatibility Note:** On macOS development machines, local-tts executes natively using Apple's MLX Metal GPU framework. On Hetzner Linux bare metal, local-tts operates via the PyTorch CUDA backend. Because the gateway communicates with local-tts over standardized HTTP/WebSocket protocols, zero backend code changes are required when switching host platforms.

---

## 3.3 Frontend Stack & UI Libraries **[Confirmed]**

| Package | Version | Purpose |
|---|---|---|
| **React** | `18.3.1` | Core reactive UI library |
| **TypeScript** | `5.6.3` | Strict type checking, interfaces, and API contract safety |
| **Vite** | `5.4.11` | Build tooling, dev server, and subpath asset bundling (`/voicecontrolpanel/`) |
| **TailwindCSS** | `3.4.15` | Utility-first styling framework powering dark-mode control panel |
| **Lucide React** | `1.16.0` | Control panel icon library |
| **@ricky0123/vad-web** | `0.0.31` | Silero Voice Activity Detection running client-side in WebAssembly |
| **onnxruntime-web** | `1.31.1` | WebAssembly inference engine running ONNX VAD weights directly in the browser |
| **PostCSS / Autoprefixer** | `8.4.49` / `10.4.20` | CSS parsing and cross-browser vendor prefixing |

---

## 3.4 Production Infrastructure & Host System **[Confirmed]**

| Tool / Technology | System Role |
|---|---|
| **Caddy 2** | Edge reverse proxy, automatic Let's Encrypt / ZeroSSL TLS, HTTP basic authentication, log scrubbing |
| **Systemd** | Host process supervisor managing 5 service units and timers with strict security sandboxing |
| **UFW (Uncomplicated Firewall)** | Host network perimeter allowing only incoming ports 22 (SSH), 80 (HTTP), and 443 (HTTPS) |
| **nftables** | Kernel packet filtering dropping any loopback traffic to engine ports (8080, 8880, 8088) originating from non-`voiceagent` UIDs |
| **Fail2ban** | Automated intrusion prevention monitoring Caddy authentication failure logs |

---

## 3.5 Development vs Production Environment Parity

| Environment Parameter | Development Environment | Production Target (Hetzner Bare Metal) |
|---|---|---|
| **Host Hardware** | Apple Silicon M2 Mac (16 GB Unified Memory) | Dedicated Bare Metal (Intel Xeon/AMD EPYC, 64 GB+ RAM, NVIDIA GPU) |
| **Operating System** | macOS 15.x Darwin | Ubuntu 24.04 LTS (x86_64 Linux kernel 6.8+) |
| **GPU Acceleration** | Apple Metal Performance Shaders (MPS / MLX) | NVIDIA Tensor Core CUDA 12.x / cuDNN |
| **Process Control** | Shell scripts (`scripts/start-native-engines.sh`) | Systemd isolated service units (`deploy/systemd/*.service`) |
| **Gateway Process Model** | Uvicorn running in dev reload mode | Single Uvicorn worker (maintains memory ticket state & semaphores) |
| **Frontend Serving** | Vite HMR Dev Server on `127.0.0.1:5173` | Static compiled assets served by Caddy at `/voicecontrolpanel/` |
| **TLS / SSL** | Plain HTTP over localhost loopback | Automatic HTTPS certificates via Let's Encrypt |
| **System User / UID** | Local developer login (`uid 501`) | Dedicated unprivileged system user `voiceagent` (DAC `chmod 700/600`) |
| **Containerization** | None (Native bare-metal processes) | None (Native bare metal to eliminate virtualization and preserve direct VRAM) |
