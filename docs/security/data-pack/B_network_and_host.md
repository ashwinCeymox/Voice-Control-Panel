# Data Pack: Section B - Network and Host

Collected on: 2026-11-16
System: macOS 26.6.2 (Darwin 25G83) Apple Silicon

## B-01: Listening Ports, Addresses, and Processes
Observed via `lsof -iTCP -sTCP:LISTEN -nP`:

| PID | Process | User | Listening Address | Service Role |
|---|---|---|---|---|
| 66646 | `local-ai` | `hashhwin047icloud.com` | `127.0.0.1:8080` | LocalAI Inference Engine |
| 66741 | `Python` (`local-tts`) | `hashhwin047icloud.com` | `127.0.0.1:8880` | Kokoro TTS Service |
| 60946 | `python3.1` (`server.py`) | `hashhwin047icloud.com` | `127.0.0.1:8088` | Sherpa-ONNX Streaming STT |
| 66751 | `python3.1` (`uvicorn`) | `hashhwin047icloud.com` | `127.0.0.1:8000` | Voice AI Gateway |
| 66788 | `node` (`vite`) | `hashhwin047icloud.com` | `127.0.0.1:5173` | Frontend Dev Server |
| 66718 | `whisper` | `hashhwin047icloud.com` | `127.0.0.1:57776` | LocalAI internal whisper backend |
| 66732 | `llama-cpp` | `hashhwin047icloud.com` | `127.0.0.1:57794` | LocalAI internal llama.cpp backend |
| 748 | `rapportd` | `hashhwin047icloud.com` | `*:61252` | macOS Continuity (System) |
| 799 | `ControlCenter` | `hashhwin047icloud.com` | `*:7000`, `*:5000` | macOS AirPlay Receiver (System) |

**Finding:** All AI engines and gateway endpoints are currently strictly listening on IPv4 loopback `127.0.0.1`. None are listening on `0.0.0.0` or `*:` or public IP.

## B-02: Bind Address Verification from Launch Commands
* `LocalAI`: `--address 127.0.0.1:8080` (Verified in PID 66646 arguments)
* `local-tts`: `--host 127.0.0.1 --port 8880` (Verified in PID 66741 arguments)
* `Streaming STT`: `--port 8088` (Default host is `127.0.0.1` in `services/streaming_stt/server.py:371`)
* `Voice Gateway`: `--host 127.0.0.1 --port 8000` (Verified in PID 66751 arguments)

## B-03: Firewall Status
* Current Host: macOS pf firewall (`pfctl`).
* Status: Not actively filtering loopback. Standard macOS application firewall.
* Target Cloud (Hetzner): No firewall attached by default; requires configuring Hetzner Cloud Firewall or `ufw` on Linux.

## B-04: Network Interfaces
* Loopback: `127.0.0.1`
* Primary LAN IP: `192.168.1.100` (Private RFC 1918 address on local Wi-Fi router)
* Public WAN IP: Not bound directly on interface; machine sits behind NAT router.

## B-05: Remote Access (SSH)
* Standard macOS Remote Login status: Default port 22.

## B-06: OS & Architecture
* Host: Apple M2 (Apple Silicon), macOS Sequoia 26.6.2.
* Target: Hetzner Linux server (requires separate verification once deployed).

## B-07: User & Group
* Current process user: `uid=501(hashhwin047icloud.com) gid=20(staff)`.
