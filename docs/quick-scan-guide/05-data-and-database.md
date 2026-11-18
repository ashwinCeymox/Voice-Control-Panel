# 5. Data and Database (4 minutes)

> **TL;DR:** One SQLite database (`data/voice_platform.db`) in WAL mode with busy timeout 5000ms. Tables for sessions, messages, turns, agents, models, API keys, and usage analytics. Raw audio is never saved to disk. Ephemeral playground sessions auto-expire in 24 hours.

---

## 5.1 Storage Locations & Files

| Data Item | Exact Path on Disk | Backup & Retention |
|---|---|---|
| **Primary SQLite Database** | `data/voice_platform.db` | WAL enabled; protected from cleanup |
| **WAL & SHM Index Files** | `data/voice_platform.db-wal`, `*-shm` | Auto-checkpointed; protected |
| **Model Weights (LLM/STT)** | `models/streaming-stt/`, `arch/LocalAI/models/` | Never deleted by automated tools |
| **Installed Models Manifest** | `data/installed_models_manifest.json` | Persistent catalog synchronized across engines |
| **Database Backups** | `data/backups/app-backup-*.db` | Nightly cron; newest 3 copies permanently kept |
| **Quarantine Storage** | `.trash/<run-id>/` | 7-day retention; restorable via `cleanup.py` |
| **WebSocket Tickets** | In-memory `_TICKETS` map | 60-second TTL; pruned by hygiene manager |

---

## 5.2 Confirmed Table Schemas

### 1. `api_keys` (Authentication & Quotas)
```sql
CREATE TABLE api_keys (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE,       -- HMAC-SHA256 hex digest
    role TEXT NOT NULL,              -- 'admin' | 'caller'
    scopes TEXT NOT NULL,            -- JSON array e.g. ["stt", "llm"]
    rate_limit INTEGER DEFAULT 60,   -- Requests per minute
    max_concurrent INTEGER DEFAULT 5,-- Simultaneous active requests
    max_daily_tokens INTEGER,        -- Token budget
    max_daily_audio_seconds REAL,    -- Audio duration budget
    expires_at REAL,                 -- UNIX timestamp (NULL = permanent)
    revoked_at REAL,                 -- UNIX timestamp when deactivated
    created_at REAL NOT NULL,
    profile_id TEXT,                 -- References profiles(id)
    prefix TEXT,                     -- Public display prefix (e.g. "va_live_a1b2...")
    status TEXT DEFAULT 'active',    -- 'active' | 'revoked' | 'rotated'
    model_allowlist TEXT,            -- JSON array of permitted models
    ip_allowlist TEXT,               -- JSON array of allowed CIDRs
    rotated_from TEXT,               -- Previous key ID during rotation
    rotation_expires_at REAL,        -- End of dual-key rotation window
    last_used_at REAL
);
```

### 2. `sessions` & `turns` (Conversation History)
```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    is_ephemeral INTEGER DEFAULT 0,  -- 1 for playground tests (auto-pruned after 24h)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    transcript TEXT NOT NULL,        -- User spoken text
    response_text TEXT NOT NULL,     -- Assistant reply text
    stt_ms INTEGER,                  -- STT inference latency
    llm_ms INTEGER,                  -- LLM first-token latency
    tts_ms INTEGER,                  -- TTS synthesis latency
    total_ms INTEGER,                -- End-to-end turn time
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
```

### 3. `agents` (Custom Assistant Personalities)
```sql
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    system_prompt TEXT NOT NULL,
    llm_json TEXT NOT NULL,          -- Model, temperature, max_tokens
    stt_json TEXT NOT NULL,          -- STT model and streaming preference
    tts_json TEXT NOT NULL,          -- Voice ID, speed, pitch
    vad_json TEXT NOT NULL,          -- VAD threshold & silence duration
    audio_json TEXT NOT NULL,        -- Echo cancellation, noise gate settings
    is_active INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP
);
```

### 4. `usage_events` (Granular Billing & Analytics)
```sql
CREATE TABLE usage_events (
    id TEXT PRIMARY KEY,
    key_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    tokens_prompt INTEGER DEFAULT 0,
    tokens_completion INTEGER DEFAULT 0,
    audio_seconds REAL DEFAULT 0.0,
    latency_ms INTEGER DEFAULT 0,
    status_code INTEGER DEFAULT 200,
    timestamp REAL NOT NULL,
    actor_type TEXT DEFAULT 'key',
    key_name TEXT,
    profile_name TEXT
);
```

---

## 5.3 Privacy & Data Retention Rules

1. **Raw Audio is Ephemeral:** Microphones and audio chunks stream directly to memory buffers. Neither the browser nor the backend writes raw user voice audio to permanent disk storage.
2. **Transcript Logging is Disabled:** `LOG_TRANSCRIPTS=false` in `.env` prevents logging spoken text to stdout, journald, or log files.
3. **Playground Pruning:** Test turns generated in the web playground are flagged `is_ephemeral = 1` and automatically purged from the database after **24 hours** by [`app/services/hygiene_manager.py`](../../app/services/hygiene_manager.py).
4. **GDPR Deletion:** Deleting a session via `DELETE /v1/agents/{id}` cascades and removes all associated turns and messages immediately.

---

## 5.4 Database Maintenance & Backup Operations

- **WAL Checkpointing:** Scheduled in the background every 10 minutes (`PRAGMA wal_checkpoint(PASSIVE);`) to prevent WAL file bloat.
- **Weekly Integrity Check:** Weekly `PRAGMA integrity_check;` verifies database pages.
- **Backup Script:** Run [`scripts/backup-db.sh`](../../scripts/backup-db.sh) creates an atomic SQLite copy using `.backup` command in `data/backups/app-backup-<timestamp>.db`.
- **Restore Script:** Run [`scripts/restore-db.sh <backup_file>`](../../scripts/restore-db.sh) validates integrity before safely swapping the active database file.
