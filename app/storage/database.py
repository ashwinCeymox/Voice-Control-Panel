"""SQLite async database connection and schema initialization using aiosqlite."""
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

"""


def get_db():
    return aiosqlite.connect(settings.APP_DATABASE_PATH)


async def init_db():
    db_path = Path(settings.APP_DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.executescript(SCHEMA_SQL)
        await db.commit()
    logger.info("SQLite database initialized at: %s", db_path.resolve())
