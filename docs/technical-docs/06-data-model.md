# 6. Data Model

## 6.1 Database Storage Architecture

The platform uses an embedded SQLite database located at `data/voice_platform.db`. It is managed through `aiosqlite` with asynchronous connection pooling and explicit performance pragmas:

- **Journal Mode:** Write-Ahead Logging (`PRAGMA journal_mode = WAL;`) allows non-blocking concurrent reads while a write transaction is executing.
- **Busy Timeout:** Configured to `5000` milliseconds (`PRAGMA busy_timeout = 5000;`) to prevent `database is locked` errors during burst traffic.
- **Foreign Keys:** Strictly enforced (`PRAGMA foreign_keys = ON;`) to guarantee cascading cleanup.
- **Automated Checkpoints:** The in-app hygiene loop issues scheduled checkpoints (`PRAGMA wal_checkpoint(PASSIVE);`) every 10 minutes to maintain bounded WAL file sizes.

---

## 6.2 Entity-Relationship Overview

```text
 ┌──────────────┐         1:N          ┌────────────────┐
 │   profiles   │ ───────────────────► │    api_keys    │
 └──────────────┘                      └───────┬────────┘
                                               │ 1:N
                                               ▼
                                       ┌────────────────┐
                                       │  usage_events  │
                                       └────────────────┘

 ┌──────────────┐         1:N          ┌────────────────┐
 │    agents    │ ───────────────────► │    sessions    │
 └──────────────┘                      └───────┬────────┘
                                               │ 1:N (CASCADE)
                                               ▼
                                       ┌────────────────┐
                                       │     turns      │
                                       └────────────────┘
```

---

## 6.3 Confirmed DDL Table Schemas (`app/storage/database.py`)

### 6.3.1 Table: `api_keys` (Authentication & Quotas)
```sql
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE,       -- HMAC-SHA256 hex digest of plain key
    role TEXT NOT NULL,              -- 'admin' | 'caller'
    scopes TEXT NOT NULL,            -- JSON array e.g. ["stt", "llm", "voice"]
    rate_limit INTEGER DEFAULT 60,   -- Max requests per minute
    max_concurrent INTEGER DEFAULT 5,-- Max concurrent active slots
    max_daily_tokens INTEGER DEFAULT NULL,
    max_daily_audio_seconds REAL DEFAULT NULL,
    expires_at REAL,                 -- UNIX timestamp when key expires
    revoked_at REAL,                 -- UNIX timestamp when key was deactivated
    created_at REAL NOT NULL,
    profile_id TEXT,                 -- Foreign key referencing profiles(id)
    prefix TEXT,                     -- Public display prefix (e.g. "va_live_e4f1...")
    status TEXT DEFAULT 'active',    -- 'active' | 'revoked' | 'rotated'
    model_allowlist TEXT,            -- JSON array of allowed model IDs
    ip_allowlist TEXT,               -- JSON array of allowed CIDR blocks
    rotated_from TEXT,               -- Key ID of preceding key during rotation
    rotation_expires_at REAL,        -- End of dual-active grace window
    last_used_at REAL
);
```

### 6.3.2 Table: `sessions` & `turns` (Conversation History)
```sql
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    is_ephemeral INTEGER DEFAULT 0,  -- 1 for playground turns; auto-pruned after 24h
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    transcript TEXT NOT NULL,        -- Transcribed user speech
    response_text TEXT NOT NULL,     -- Assistant reply text
    stt_ms INTEGER,                  -- STT recognition latency (ms)
    llm_ms INTEGER,                  -- LLM first-token latency (ms)
    tts_ms INTEGER,                  -- TTS synthesis latency (ms)
    total_ms INTEGER,                -- End-to-end turn time (ms)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, created_at);
```

### 6.3.3 Table: `agents` (Personality Configurations)
```sql
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    system_prompt TEXT NOT NULL,
    llm_json TEXT NOT NULL,          -- Model, temperature, max_tokens JSON
    stt_json TEXT NOT NULL,          -- STT model and streaming preference JSON
    tts_json TEXT NOT NULL,          -- Voice ID, speed, pitch JSON
    vad_json TEXT NOT NULL,          -- VAD threshold & silence duration JSON
    audio_json TEXT NOT NULL,        -- Audio preprocessing parameters JSON
    is_active INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP
);
```

### 6.3.4 Table: `installed_models` (Model Registry)
```sql
CREATE TABLE IF NOT EXISTS installed_models (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,          -- 'llm' | 'stt' | 'tts'
    provider TEXT NOT NULL,          -- 'localai' | 'local-tts' | 'streaming-stt'
    size_mb INTEGER DEFAULT 0,
    installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 6.3.5 Table: `usage_events` (Granular Usage & Telemetry)
```sql
CREATE TABLE IF NOT EXISTS usage_events (
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
    key_name TEXT DEFAULT NULL,
    profile_name TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_key ON usage_events(key_id, timestamp);
```

### 6.3.6 Table: `profiles` (Application Billing Entities)
```sql
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    contact TEXT,
    notes TEXT,
    disabled_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
```

---

## 6.4 Data Privacy & Retention Governance

1. **In-Memory Audio Processing:** Raw user voice audio is buffered strictly in memory while being converted to PCM frames and dispatched to inference engines. Audio bytes are **never written to persistent disk files**.
2. **Log Sanitization:** Transcripts are excluded from standard logging (`LOG_TRANSCRIPTS=false`). API keys in HTTP headers and tickets in query parameters are scrubbed from reverse proxy logs.
3. **Automated Playground Pruning:** Turns created in the web playgrounds are tagged `is_ephemeral = 1` in `sessions`. The background hygiene manager (`app/services/hygiene_manager.py`) runs an hourly query:
   ```sql
   DELETE FROM sessions WHERE is_ephemeral = 1 AND created_at < datetime('now', '-24 hours');
   ```
   Foreign key cascade deletes all linked `turns` rows automatically.
4. **GDPR / CCPA Erasure:** Calling `DELETE /v1/agents/{id}` cascades to delete all associated conversation sessions and transcripts immediately.

---

## 6.5 Backup, Integrity & Disaster Recovery

- **Atomic Online Backups:** [`scripts/backup-db.sh`](../../scripts/backup-db.sh) runs SQLite's native `.backup` mechanism, producing a consistent snapshot even while transactions are occurring:
  ```bash
  sqlite3 data/voice_platform.db ".backup 'data/backups/app-backup-$(date +%Y%m%d-%H%M%S).db'"
  ```
- **Automated Backup Cron:** Installed at `/etc/cron.d/voice-backup` for nightly execution at 03:00.
- **Retention Protection:** The cleanup policy preserves the **newest 3 backup copies permanently** regardless of age; older backups are quarantined only after 14 days.
- **Safe Recovery:** [`scripts/restore-db.sh <backup_file>`](../../scripts/restore-db.sh) checks integrity (`PRAGMA integrity_check`) before swapping the restored database file.
