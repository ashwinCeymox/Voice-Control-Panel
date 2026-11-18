"""Voice turn orchestration for synchronous turns and cancellation registry."""

import asyncio
import base64
import time
import uuid
from typing import Dict, Optional
from app.core.config import settings
from app.providers.localai_llm import LocalAILLMClient
from app.providers.localai_stt import LocalAISTTClient
from app.providers.localtts_client import LocalTTSClient
from app.schemas.audio import AudioFormat
from app.schemas.chat import ChatCompletionRequest, ChatMessage
from app.schemas.voice import VoiceTurnMetrics, VoiceTurnResponse
from app.storage.repositories import SessionRepository


class VoiceOrchestrator:
    def __init__(self):
        self.stt = LocalAISTTClient()
        self.llm = LocalAILLMClient()
        self.tts = LocalTTSClient()
        # Active cancellation tasks keyed by session_id: {session_id: asyncio.Task}
        self._active_stream_tasks: Dict[str, asyncio.Task] = {}

    async def execute_turn(
        self,
        audio_bytes: bytes,
        session_id: Optional[str] = None,
        owner: str = "default",
        filename: str = "utterance.wav",
        content_type: str = "audio/wav",
        llm_model: Optional[str] = None,
        voice_id: Optional[str] = None,
        tts_model: Optional[str] = None,
    ) -> VoiceTurnResponse:
        """Execute one-shot synchronous voice turn: STT -> History -> LLM -> TTS -> DB."""
        turn_id = f"turn-{uuid.uuid4()}"
        request_id = f"req-{uuid.uuid4()}"[:12]
        sid = await SessionRepository.get_or_create_session(session_id, owner)

        start_total = time.perf_counter()

        # 1. Speech-to-Text
        stt_start = time.perf_counter()
        transcript_resp = await self.stt.transcribe(
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
        )
        stt_ms = int((time.perf_counter() - stt_start) * 1000)
        transcript = transcript_resp.text or "(unintelligible)"

        # Save user message to history
        await SessionRepository.add_message(sid, "user", transcript)

        # 2. History & LLM Completion
        llm_start = time.perf_counter()
        history = await SessionRepository.get_recent_messages(
            sid, limit=settings.MAX_SESSION_MESSAGES
        )
        system_prompt = ChatMessage(
            role="system",
            content="You are a helpful, concise voice AI assistant. Keep responses natural, conversational, and under two sentences.",
        )
        llm_messages = [system_prompt] + history

        chat_resp = await self.llm.complete(
            ChatCompletionRequest(
                model=llm_model or settings.LOCALAI_LLM_MODEL,
                messages=llm_messages,
                stream=False,
            )
        )
        response_text = ""
        if chat_resp.choices:
            response_text = chat_resp.choices[0].message.content.strip()
        llm_ms = int((time.perf_counter() - llm_start) * 1000)

        # Save assistant message to history
        await SessionRepository.add_message(sid, "assistant", response_text)

        # 3. Text-to-Speech
        tts_start = time.perf_counter()
        pcm_bytes = await self.tts.synthesize_http(
            text=response_text,
            voice_id=voice_id or settings.LOCAL_TTS_VOICE_ID,
            model_id=tts_model or settings.LOCAL_TTS_MODEL,
        )
        tts_ms = int((time.perf_counter() - tts_start) * 1000)
        total_ms = int((time.perf_counter() - start_total) * 1000)

        # 4. Save turn to database
        await SessionRepository.save_turn(
            turn_id=turn_id,
            session_id=sid,
            transcript=transcript,
            response_text=response_text,
            stt_ms=stt_ms,
            llm_ms=llm_ms,
            tts_ms=tts_ms,
            total_ms=total_ms,
        )

        audio_b64 = base64.b64encode(pcm_bytes).decode("utf-8")
        metrics = VoiceTurnMetrics(
            stt_ms=stt_ms,
            llm_ms=llm_ms,
            tts_ms=tts_ms,
            total_ms=total_ms,
        )

        return VoiceTurnResponse(
            turn_id=turn_id,
            session_id=sid,
            request_id=request_id,
            transcript=transcript,
            response_text=response_text,
            audio_base64=audio_b64,
            audio_format=AudioFormat(
                sample_rate=24000, sample_format="s16le", channels=1
            ),
            metrics=metrics,
        )

    def register_stream_task(self, session_id: str, task: asyncio.Task):
        self._active_stream_tasks[session_id] = task

    def cancel_stream_task(self, session_id: str):
        task = self._active_stream_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()


orchestrator = VoiceOrchestrator()
