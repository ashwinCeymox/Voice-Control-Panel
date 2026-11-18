# Data Pack: Section F - AI Inference Engines

Collected on: 2026-11-16

## F-01: LocalAI Engine Audit
* Port: `8080`
* Current Host Binding: `127.0.0.1:8080`
* LocalAI Authentication: Supported via `--api-key` and `LOCALAI_API_KEY` in `.env`.
* LocalAI Dangerous Endpoints (Must NEVER be reachable by clients or public internet):
  * `/models/apply` (installs arbitrary models from HuggingFace/gallery)
  * `/models/delete/{name}` (deletes models from disk)
  * `/models/import` (downloads arbitrary remote URIs)
  * `/backend/shutdown` (kills backend runtime processes)
  * `/backend/load` (loads arbitrary backend binaries)
  * `/system` (exposes OS CPU/RAM process details)
  * `/metrics` (exposes engine telemetry)
  * `/api/traces` (exposes LLM prompt/completion history)
  * `/api/p2p/token` (P2P mesh discovery)
* Direct Access Test:
  * From outside: Refused / unreachable (listening strictly on `127.0.0.1`).
  * From Gateway: Reached via internal loopback HTTP requests.

## F-02: local-tts Engine Audit
* Port: `8880`
* Current Host Binding: `127.0.0.1:8880`
* Authentication: None (Internal microservice).
* Endpoints:
  * `GET /v1/models`
  * `POST /v1/text-to-speech/{voice_id}/stream`
* Protection: Bound strictly to `127.0.0.1`.

## F-03: Streaming STT Service Audit
* Port: `8088`
* Current Host Binding: `127.0.0.1:8088`
* Authentication: None (Internal microservice).
* Endpoints:
  * `GET /health`
  * `GET /v1/models`
  * `WS /ws`
* Protection: Bound strictly to `127.0.0.1`.

## F-04: Model Storage
* LocalAI weights: `../LocalAI/models/`
* Streaming STT models: `models/streaming-stt/`
* Write access: Gateway process user (`uid=501`).
