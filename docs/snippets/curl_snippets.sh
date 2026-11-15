#!/usr/bin/env bash
# Ready-to-run curl snippets for local voice gateway

BASE_URL="{{BASE_URL}}"
API_KEY="{{API_KEY}}"

# 1. LLM Chat Completions (Streaming)
curl -N -X POST "${BASE_URL}/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "{{MODEL}}",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'

# 2. STT Batch Transcription
curl -X POST "${BASE_URL}/v1/audio/transcriptions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -F "file=@audio.wav" \
  -F "model={{MODEL}}"

# 3. Text-to-Speech HTTP Stream (24 kHz PCM)
curl -X POST "${BASE_URL}/v1/text-to-speech/{{VOICE}}/stream" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello world from offline speech synthesis.",
    "model_id": "{{MODEL}}"
  }' \
  --output output.pcm
