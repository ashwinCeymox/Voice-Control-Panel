# local-tts on Linux: Installation Plan

Source: https://github.com/saghul/local-tts

local-tts is a local TTS server with an ElevenLabs-compatible API. On Linux it uses the PyTorch/ONNX backends (Kokoro runs via the official `kokoro` PyTorch package). The backend is chosen automatically from `sys.platform`.

---

## 1. Prerequisites

| Requirement | Notes |
| --- | --- |
| Python | >= 3.10 and < 3.14 |
| uv | Package manager used by the project |
| git | To clone the repo |
| Disk space | Enough for model weights (cached in `~/.cache/huggingface/hub/`) |
| Internet | Needed on first run to download models from Hugging Face |
| Audio output + `ffplay` or `sox` | Only if you want to play audio locally |
| GPU | Not required for Kokoro (82M params). The extra is named `cuda`, but CPU use is untested per the README |

Check Python:

```bash
python3 --version
```

---

## 2. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# restart your shell, or:
source ~/.bashrc
uv --version
```

---

## 3. Clone the repository

```bash
git clone https://github.com/saghul/local-tts.git
cd local-tts
```

---

## 4. Install dependencies (Linux extra)

```bash
uv sync --extra cuda
```

Notes:
- uv creates a virtual environment automatically.
- Use `--extra cuda` on Linux (not `--extra mlx`, which is macOS Apple Silicon only).
- For development, add `--group dev`.

---

## 5. (Optional) Install system packages

Only needed for playback or for non-English Kokoro voices later (listed as a TODO in the README):

```bash
# Debian/Ubuntu
sudo apt install ffmpeg sox espeak-ng
```

---

## 6. Start the server

Default (all four engines available, lazy-loaded on first request):

```bash
uv run local-tts server
```

Kokoro only, bound to localhost:

```bash
uv run local-tts server \
  --host 127.0.0.1 \
  --port 8880 \
  --disable-pocket --disable-kitten --disable-qwen
```

Useful flags:

| Flag | Purpose |
| --- | --- |
| `--host` | Bind address (default `0.0.0.0`, reachable from the network) |
| `--port` | Port (default `8880`) |
| `--preload` | Load models at startup instead of on first request |
| `--disable-kokoro/pocket/kitten/qwen` | Skip an engine entirely |

The first run downloads model weights, so expect a delay.

---

## 7. Test it

Client (second terminal, plays through the default audio device):

```bash
uv run local-tts client -t "Hello, world!"
```

Or via curl (raw 24 kHz 16-bit mono PCM):

```bash
curl -X POST http://localhost:8880/v1/text-to-speech/af_heart/stream \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, world!", "model_id": "kokoro"}' \
  --output audio.pcm

ffplay -f s16le -ar 24000 -ac 1 audio.pcm
```

List voices and models:

```bash
curl http://localhost:8880/v1/voices?model_id=kokoro
curl http://localhost:8880/v1/models
```

---

## 8. (Optional) Run as a systemd service

Create `/etc/systemd/system/local-tts.service` (adjust `User`, paths, and the `uv` location):

```ini
[Unit]
Description=local-tts server
After=network.target

[Service]
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/local-tts
ExecStart=/home/YOUR_USER/.local/bin/uv run local-tts server --host 127.0.0.1 --port 8880 --disable-pocket --disable-kitten --disable-qwen
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now local-tts
sudo systemctl status local-tts
```

---

## 9. Troubleshooting

| Problem | Likely cause / fix |
| --- | --- |
| Python version error | Needs 3.10 to 3.13; let uv install one with `uv python install 3.12` |
| Slow first request | Model download or lazy load; use `--preload` |
| No audio from client | Check the default audio device; or use curl + `ffplay` |
| Another app gets a 404 | local-tts exposes the ElevenLabs-style API (`/v1/text-to-speech/{voice_id}/stream`), not the OpenAI-style `/v1/audio/speech` |
| Port already in use | Change with `--port` |

---

## Summary of the install pattern

```
install uv -> git clone -> uv sync --extra cuda -> uv run local-tts server -> test with client/curl
```
