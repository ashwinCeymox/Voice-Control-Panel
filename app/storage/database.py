"""SQLite async database connection and schema initialization using aiosqlite."""
from contextlib import asynccontextmanager
from pathlib import Path
import aiosqlite
import logging
from app.core.config import settings

logger = logging.getLogger("voice_agent.database")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    transcript TEXT NOT NULL,
    response_text TEXT NOT NULL,
    stt_ms INTEGER,
    llm_ms INTEGER,
    tts_ms INTEGER,
    total_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, created_at);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    system_prompt TEXT NOT NULL,
    llm_json TEXT NOT NULL,
    stt_json TEXT NOT NULL,
    tts_json TEXT NOT NULL,
    vad_json TEXT NOT NULL,
    audio_json TEXT NOT NULL,
    is_active INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS installed_models (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    provider TEXT NOT NULL,
    size_mb INTEGER DEFAULT 0,
    installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    scopes TEXT NOT NULL,
    rate_limit INTEGER DEFAULT 60,
    max_concurrent INTEGER DEFAULT 5,
    max_daily_tokens INTEGER DEFAULT NULL,
    max_daily_audio_seconds REAL DEFAULT NULL,
    expires_at REAL,
    revoked_at REAL,
    created_at REAL NOT NULL,
    profile_id TEXT,
    prefix TEXT,
    status TEXT DEFAULT 'active',
    model_allowlist TEXT,
    ip_allowlist TEXT,
    rotated_from TEXT,
    rotation_expires_at REAL,
    last_used_at REAL
);

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

CREATE TABLE IF NOT EXISTS dashboard_settings (
    id TEXT PRIMARY KEY,
    base_url TEXT DEFAULT 'http://127.0.0.1:8000',
    default_rate_limit INTEGER DEFAULT 60,
    default_max_concurrent INTEGER DEFAULT 5,
    usage_retention_days INTEGER DEFAULT 30,
    kill_switches_json TEXT DEFAULT '{}',
    cors_origins_json TEXT DEFAULT '["*"]',
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS admin_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    admin_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    details TEXT
);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    timestamp REAL NOT NULL,
    duration_ms REAL NOT NULL,
    tokens_or_seconds REAL NOT NULL,
    status INTEGER NOT NULL,
    FOREIGN KEY(key_id) REFERENCES api_keys(id)
);

CREATE INDEX IF NOT EXISTS idx_usage_key ON usage(key_id, timestamp);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    key_id TEXT,
    profile_id TEXT,
    endpoint TEXT NOT NULL,
    model TEXT,
    status INTEGER NOT NULL,
    latency_ms REAL NOT NULL,
    ttfb_ms REAL,
    input_units REAL DEFAULT 0.0,
    output_units REAL DEFAULT 0.0,
    bytes_in INTEGER DEFAULT 0,
    bytes_out INTEGER DEFAULT 0,
    error_code TEXT
);

CREATE INDEX IF NOT EXISTS idx_usage_events_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_events_key ON usage_events(key_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_events_profile ON usage_events(profile_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_events_model ON usage_events(model, ts);


"""


DEFAULT_INSTALLED_MODELS = [
    ("whisper-base", "Whisper Base (GGUF)", "stt", "localai", 145),
    ("whisper-tiny", "Whisper Tiny (GGUF)", "stt", "localai", 75),
    ("llama-3.2-1b-instruct", "Llama 3.2 1B Instruct", "llm", "localai", 820),
    ("kokoro", "Kokoro", "tts", "local-tts", 350),
]


@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(settings.APP_DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute("PRAGMA busy_timeout = 5000;")
        await db.execute("PRAGMA foreign_keys = ON;")
        yield db


async def init_db():
    db_path = Path(settings.APP_DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with get_db() as db:
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute("PRAGMA busy_timeout = 5000;")
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.executescript(SCHEMA_SQL)
        
        # Safe column migrations on api_keys
        extra_columns = [
            "max_daily_tokens INTEGER DEFAULT NULL",
            "max_daily_audio_seconds REAL DEFAULT NULL",
            "profile_id TEXT DEFAULT NULL",
            "prefix TEXT DEFAULT NULL",
            "status TEXT DEFAULT 'active'",
            "model_allowlist TEXT DEFAULT NULL",
            "ip_allowlist TEXT DEFAULT NULL",
            "rotated_from TEXT DEFAULT NULL",
            "rotation_expires_at REAL DEFAULT NULL",
            "last_used_at REAL DEFAULT NULL",
        ]
        for col_def in extra_columns:
            try:
                await db.execute(f"ALTER TABLE api_keys ADD COLUMN {col_def}")
            except Exception:
                pass

        # Ensure default dashboard settings exist
        await db.execute(
            """
            INSERT OR IGNORE INTO dashboard_settings (id, base_url, default_rate_limit, default_max_concurrent, usage_retention_days, updated_at)
            VALUES ('default', 'http://127.0.0.1:8000', 60, 5, 30, ?)
            """,
            (1700000000.0,),
        )

        for mid, name, cat, prov, sz in DEFAULT_INSTALLED_MODELS:
            await db.execute(
                "INSERT OR IGNORE INTO installed_models (id, name, category, provider, size_mb) VALUES (?, ?, ?, ?, ?)",
                (mid, name, cat, prov, sz),
            )
        await db.commit()
    logger.info("SQLite database initialized in WAL mode at: %s", db_path.resolve())
