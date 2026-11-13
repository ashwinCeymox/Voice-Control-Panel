# Handoff: Phase P3 Request Model Rules & STTProvider Abstraction (BE-KEYS)

**Status:** COMPLETE  
**Owner:** BE-KEYS  
**Test Suite:** `tests/unit/test_p3_rules_and_provider.py` (3/3 PASSED)  
**Full Regression Suite:** 93/93 PASSED  

---

## 1. Summary of Changes

### Request Model Rules & Allowlist (`P3-RULES`)
- In `app/core/config.py`, added configurations:
  - `LOCALAI_ALLOWED_LLM_MODELS: Optional[str]`
  - `LOCALAI_ALLOWED_STT_MODELS: Optional[str]`
- In `app/api/chat.py` (`POST /v1/chat/completions`):
  - Validates `request.model` against the allowlist.
  - If a disallowed model is requested, immediately rejects with HTTP 400 Bad Request: `Model '<model>' is not in allowed models list`.
  - Callers cannot trigger model loads or changes to the global active agent.
- In `app/api/transcriptions.py` (`POST /v1/audio/transcriptions`):
  - Validates `model` against `LOCALAI_ALLOWED_STT_MODELS`.
  - Rejects unauthorized models with HTTP 400 Bad Request.

### STTProvider Abstraction (`P3-STT`)
- Created `app/providers/stt_base.py` defining abstract base class `STTProvider`.
- Contract:
  ```python
  class STTProvider(ABC):
      @abstractmethod
      async def transcribe(
          self,
          audio_bytes: bytes,
          filename: str = "audio.wav",
          content_type: str = "audio/wav",
          model: Optional[str] = None,
          language: Optional[str] = None,
      ) -> TranscriptResponse:
          pass
  ```
- `LocalAISTTClient` in `app/providers/localai_stt.py` inherits from `STTProvider`, cleanly decoupling the Gateway from LocalAI and enabling direct whisper.cpp or faster-whisper drop-in replacements.

---

## 2. Test Verification Results

```bash
.venv/bin/python -m pytest tests/unit/test_p3_rules_and_provider.py -v
========================= 3 passed in 0.10s =========================

.venv/bin/python -m pytest tests/unit/ tests/contract/ tests/integration/ -v
======================== 93 passed in 8.81s =========================
```
