"""Database operations for sessions, turns, and messages."""
import uuid
from typing import List, Optional
import aiosqlite
from app.schemas.chat import ChatMessage
from app.storage.database import get_db


class SessionRepository:
    @staticmethod
    async def get_or_create_session(session_id: Optional[str], owner: str) -> str:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            if session_id:
                cursor = await db.execute("SELECT id FROM sessions WHERE id = ? AND owner = ?", (session_id, owner))
                row = await cursor.fetchone()
                if row:
                    return row["id"]
            new_id = session_id or str(uuid.uuid4())
            await db.execute(
                "INSERT OR IGNORE INTO sessions (id, owner) VALUES (?, ?)",
                (new_id, owner),
            )
            await db.commit()
            return new_id

    @staticmethod
    async def add_message(session_id: str, role: str, content: str):
        async with get_db() as db:
            await db.execute(
                "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), session_id, role, content),
            )
            await db.commit()

    @staticmethod
    async def get_recent_messages(session_id: str, limit: int = 20) -> List[ChatMessage]:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
                (session_id, limit),
            )
            rows = await cursor.fetchall()
            return [ChatMessage(role=row["role"], content=row["content"]) for row in rows]

    @staticmethod
    async def save_turn(
        turn_id: str,
        session_id: str,
        transcript: str,
        response_text: str,
        stt_ms: Optional[int] = None,
        llm_ms: Optional[int] = None,
        tts_ms: Optional[int] = None,
        total_ms: Optional[int] = None,
    ):
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO turns (id, session_id, transcript, response_text, stt_ms, llm_ms, tts_ms, total_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (turn_id, session_id, transcript, response_text, stt_ms, llm_ms, tts_ms, total_ms),
            )
            await db.commit()
