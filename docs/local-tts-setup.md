# Local TTS Setup Guide (`saghul/local-tts`)

This guide covers setting up and running [`saghul/local-tts`](https://github.com/saghul/local-tts), a lightweight, high-performance local Text-to-Speech service powered by the Kokoro TTS engine.

---

## 1. Prerequisites & Repository Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/saghul/local-tts.git
   cd local-tts
   ```

2. **Python Virtual Environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   ```

---

## 2. Troubleshooting the Known Pip / Misaki / spaCy Auto-Download Issue

When installing `local-tts` or its dependencies (specifically `misaki` and `spaCy`), automated tokenizers or language models may trigger network fetches or missing data packages (`en_core_web_sm`).

> [!WARNING]
> **Offline / Sandboxed Environment Notice**: If running in an air-gapped or sandboxed environment without PyPI access, ensure pre-bundled wheels or offline pip caches are used.

To install dependencies cleanly:
```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

---

## 3. Supported Voices & Configuration

Kokoro supports multiple high-quality voices categorized by gender and accent:

- **American Female**: `af_heart` (Default, highly expressive), `af_bella`, `af_nicole`, `af_sarah`
- **American Male**: `am_adam`, `am_michael`
- **British Female**: `bf_emma`, `bf_isabella`
- **British Male**: `bm_george`, `bm_lewis`

### Output Format Contract
- **Sample Rate**: 24,000 Hz (24 kHz)
- **Encoding**: 16-bit signed integer (`PCM_16`)
- **Channels**: Mono (1 channel)
- **Container / Framing**: Raw PCM byte stream or WebSocket binary chunks.

---

## 4. Running the Service

Start the `local-tts` server (typically listens on port `8880` or configured via CLI):
```bash
python main.py --port 8880
```

---

## 5. Verification Examples

### 5.1 HTTP Curl Verification
```bash
curl -X POST http://localhost:8880/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kokoro",
    "input": "Hello from the local voice agent platform!",
    "voice": "af_heart",
    "response_format": "pcm"
  }' --output test_output.raw
```
To play the raw 24kHz PCM file via `ffplay`:
```bash
ffplay -f s16le -ar 24000 -ac 1 test_output.raw
```

### 5.2 WebSocket / Websocat Verification
```bash
websocat ws://localhost:8880/v1/audio/speech/ws
```
