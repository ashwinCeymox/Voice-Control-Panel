---
title: "Voice AI Platform: Quick-Scan Guide"
subtitle: "The ADHD-friendly edition: short chunks, summaries first, checklists everywhere"
version: "1.0 (Production Verified)"
date: "17 November 2026"
lang: en-GB
---

# Voice AI Platform: Quick-Scan Guide

> **TL;DR:** This is a self-hosted, production-hardened voice assistant platform. You talk, it listens, thinks, and talks back. A Python FastAPI gateway orchestrates the pipeline. A React control panel serves management and playgrounds. Three native engines (LocalAI, local-tts, Sherpa-ONNX) run locally on bare metal without Docker to maximize GPU VRAM.

---

## How to Use This Guide

- **You do NOT need to read this top to bottom.**
- Every section begins with a **TL;DR box** in bold. Read only the box if you are in a rush.
- Use the **"I want to..."** jump table below to navigate directly to what you need.
- Stuck or seeing errors? Go straight to [13. Troubleshooting](13-troubleshooting.md).
- Want the entire guide in a single printable/exportable file? Read [quick-scan-guide.md](quick-scan-guide.md).

**Reading time:** Whole guide ~35 minutes. Each section is bite-sized (2 to 5 minutes).

---

## What the Confidence Tags Mean

| Tag | Meaning | Action |
|---|---|---|
| **[Confirmed]** | Verified against live code, database schemas, and passing test suites | Safe to trust and rely on |
| **[Planned]** | Designed architecture or planned roadmap item | Treat as upcoming enhancement |
| **[Architecture Note]** | Key constraint or platform difference (e.g. macOS Metal vs Linux NVIDIA) | Keep in mind during deployment |

---

## Jump Table ("I Want To...")

| I want to... | Section | Reading Time |
|---|---|---|
| Understand the system architecture and audio flow | [01. The Big Picture](01-the-big-picture.md) | 3 mins |
| See verified libraries, frameworks, and versions | [02. Libraries & Tools](02-libraries-and-tools.md) | 3 mins |
| Find a file, inspect the layout, or add an endpoint | [03. Where Things Live](03-where-things-live.md) | 2 mins |
| Call the API, check auth scopes, or see WebSocket events | [04. Backend & API](04-backend-and-api.md) | 5 mins |
| Inspect SQLite schema, keys, backups, and privacy | [05. Data & Database](05-data-and-database.md) | 4 mins |
| Work on the React control panel and VAD audio pipeline | [06. Frontend Control Panel](06-frontend-control-panel.md) | 4 mins |
| Configure LocalAI, Kokoro TTS, or Sherpa streaming STT | [07. The Engines](07-the-engines.md) | 3 mins |
| Review security rules, auth matrix, and nftables | [08. Security](08-security.md) | 5 mins |
| Deploy to Hetzner bare metal with systemd & Caddy | [09. Deploy to Hetzner](09-deploy-to-hetzner.md) | 5 mins |
| Look up an environment variable or default setting | [10. Settings Cheat Sheet](10-settings-cheat-sheet.md) | 2 mins |
| Run test suites, auth matrix probes, and benchmarks | [11. Testing](11-testing.md) | 2 mins |
| Check known issues and prioritised roadmap items | [12. Known Issues & Roadmap](12-known-issues-and-roadmap.md) | 2 mins |
| Fix an error, broken audio, or startup crash right now | [13. Troubleshooting](13-troubleshooting.md) | 2 mins |
| See resolved audit questions and confirmed schemas | [Appendix A. Audit Items Resolved](appendix-a-open-items-resolved.md) | 3 mins |
| Check audio and voice terms in one sentence each | [Appendix B. Glossary](appendix-b-glossary.md) | 1 min |
| Export this guide to Word (.docx) or PDF via Pandoc | [Appendix C. Build This Guide](appendix-c-build-guide.md) | 1 min |
