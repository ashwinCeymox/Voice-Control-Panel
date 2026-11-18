---
title: "Voice AI Platform: Technical Documentation"
subtitle: "Architecture, Data Model, Backend, Frontend, Security and Operations"
author: "Voice Agent Platform Team"
date: "17 November 2026"
version: "1.0 (Production Verified)"
lang: en-GB
---

# Voice AI Platform: Technical Documentation

## Document Control

| Field | Value |
|---|---|
| **Document title** | Voice AI Platform: Technical Documentation |
| **Version** | 1.0 (Production Verified) |
| **Date** | 17 November 2026 |
| **Status** | Production Verified. All architectural claims, schemas, and routes confirmed against live code |
| **Owner** | Voice Agent Platform Engineering |
| **Audience** | Backend engineers, DevOps / SRE, security auditors, frontend developers, on-call operators |
| **Related Documents** | [Hetzner Bare-Metal Guide](../security/hetzner-baremetal-guide.md), [ADHD Quick-Scan Guide](../quick-scan-guide/README.md), [Cleanup Inventory](../cleanup-inventory.md) |

---

## Revision History

| Version | Date | Author | Summary of Changes |
|---|---|---|---|
| **0.9** | 2026-11-17 | Platform Team | Initial draft compiled from data packs B–G and MVP/UI planning notes |
| **1.0** | 2026-11-17 | Platform Team | Production release: verified all `[Verify]` items, documented 42 routes, confirmed SQLite WAL schemas, added automated cleanup & hygiene loop, closed security audit findings (SEC-01–04, F1–F5, T6) |

---

## Reading Conventions

Every technical assertion in this documentation reflects direct verification against the codebase:

| Tag | Definition | Operational Application |
|---|---|---|
| **[Confirmed]** | Observed directly in code, database schemas, and passing test suites | Verified fact. Trust and build upon it |
| **[Planned]** | Agreed architectural design not yet fully active in the live environment | Upcoming roadmap deliverable |
| **[Architecture Note]** | Platform-specific implementation detail (e.g. macOS Metal vs Linux CUDA) | Operational constraint |

Terminology note:
- **"The Gateway":** The central Python FastAPI application (`app/main.py`) running on port `:8000`.
- **"Engines":** Native backend inference runtimes (LocalAI on `:8080`, local-tts on `:8880`, Sherpa-ONNX on `:8088`).
- **"Caller":** Any external API client holding an issued non-admin API key with scoped permissions.

---

## Table of Contents

1. [01. Introduction](01-introduction.md) — Purpose, product summary, scope, and key capabilities.
2. [02. System Overview](02-system-overview.md) — Logical architecture, component inventory, turn workflows, and audio formats.
3. [03. Technology Stack & Libraries](03-technology-stack-and-libraries.md) — Verified runtime and package versions, dev vs prod comparison.
4. [04. Repository Structure](04-repository-structure.md) — Annotated filesystem hierarchy and endpoint addition checklist.
5. [05. Backend Architecture & API](05-backend.md) — Layered design, HMAC authentication, 42 REST/WS routes, WebSocket protocol, and concurrency limits.
6. [06. Data Model](06-data-model.md) — SQLite schema in WAL mode, table definitions, privacy policies, backup and recovery.
7. [07. Frontend: Control Panel](07-frontend-control-panel.md) — React 18 UI, state machine, browser Web Audio + Silero VAD pipeline, and subpath hosting.
8. [08. Engines in Detail](08-engines-in-detail.md) — LocalAI, local-tts (Kokoro), Sherpa-ONNX streaming STT, endpoints, and isolation boundaries.
9. [09. Security](09-security.md) — Defense-in-depth architecture, closed audit findings (SEC-01–04, F1–F5, T6), nftables isolation, and startup safeguards.
10. [10. Deployment & Operations](10-deployment-and-operations.md) — Hetzner bare-metal topology, automated deploy script, systemd units, daily commands, and scaling.
11. [11. Configuration Reference](11-configuration-reference.md) — Exhaustive environment variable specification and production `.env` template.
12. [12. Testing & Quality Assurance](12-testing-and-quality-assurance.md) — 207 automated tests, auth matrix probe runner, latency benchmarks, and release gate.
13. [13. Known Issues & Roadmap](13-known-issues-and-roadmap.md) — Operating characteristics, architectural limits, and prioritized roadmap.
14. [14. Troubleshooting](14-troubleshooting.md) — Diagnostic table of symptoms, root causes, and verified fixes.
- [Appendix A. Glossary](appendix-a-glossary.md) — Technical terminology definitions.
- [Appendix B. Audit Items Resolved](appendix-b-open-items-confirmed.md) — Confirmed resolutions for all 10 technical audit questions.
- [Appendix C. Building This Document](appendix-c-building-this-document.md) — Pandoc build commands for Word (.docx) and PDF export.
- [Full Unified Document](technical-documentation.md) — Complete documentation in a single printable/exportable file.
