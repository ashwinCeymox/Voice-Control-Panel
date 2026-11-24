import pytest
from app.core.model_matching import is_model_allowed, normalize_model_id


def test_normalize_model_id():
    assert normalize_model_id("llama-3.2-1b-instruct-q8_0") == "llama-3.2-1b-instruct"
    assert normalize_model_id("llama-3.2-1b-instruct:q4_k_m") == "llama-3.2-1b-instruct"
    assert normalize_model_id("llama-3.2-1b-instruct-q8_0.gguf") == "llama-3.2-1b-instruct"
    assert normalize_model_id("qwen3.8-flash-next-ista-q2_0") == "qwen3.8-flash-next-ista"
    assert normalize_model_id("whisper-base:latest") == "whisper-base"
    assert normalize_model_id("whisper-base.bin") == "whisper-base"
    assert normalize_model_id("Kokoro") == "kokoro"


def test_is_model_allowed_quant_variants():
    allowed = ["llama-3.2-1b-instruct", "llama-3.2-3b-instruct"]

    # Exact matches
    assert is_model_allowed("llama-3.2-1b-instruct", allowed) is True
    assert is_model_allowed("llama-3.2-3b-instruct", allowed) is True

    # Quant variants with hyphen, colon, or extension
    assert is_model_allowed("llama-3.2-1b-instruct-q8_0", allowed) is True
    assert is_model_allowed("llama-3.2-1b-instruct:q4_k_m", allowed) is True
    assert is_model_allowed("llama-3.2-1b-instruct-q8_0.gguf", allowed) is True
    assert is_model_allowed("LLAMA-3.2-1B-INSTRUCT-Q8_0", allowed) is True

    # Unallowed models
    assert is_model_allowed("llama-3.2-70b-instruct", allowed) is False
    assert is_model_allowed("mistral-7b-instruct", allowed) is False
    assert is_model_allowed("whisper-base", allowed) is False


def test_is_model_allowed_wildcard_and_empty():
    # Empty or None allows everything
    assert is_model_allowed("any-model-name", None) is True
    assert is_model_allowed("any-model-name", []) is True
    assert is_model_allowed("any-model-name", [""]) is True
    assert is_model_allowed("any-model-name", ["*"]) is True


def test_is_model_allowed_stt():
    allowed_stt = ["whisper-base", "whisper-tiny"]
    assert is_model_allowed("whisper-base", allowed_stt) is True
    assert is_model_allowed("whisper-base:latest", allowed_stt) is True
    assert is_model_allowed("whisper-tiny-en", allowed_stt) is True
    assert is_model_allowed("whisper-large-v3", allowed_stt) is False


def test_check_model_allowed_api_key():
    from app.core.security import ApiKeyRecord, check_model_allowed
    from app.core.errors import ForbiddenError

    # Key with restricted allowlist
    key = ApiKeyRecord(
        id="test-key-id",
        name="test-key",
        scopes=["llm"],
        created_at=12345.0,
        role="developer",
        model_allowlist=["llama-3.2-1b-instruct"],
    )

    # Allowed exact and variants
    check_model_allowed(key, "llama-3.2-1b-instruct")
    check_model_allowed(key, "llama-3.2-1b-instruct-q8_0")
    check_model_allowed(key, "llama-3.2-1b-instruct:q4_k_m")
    check_model_allowed(key, "llama-3.2-1b-instruct-q8_0.gguf")

    # Disallowed raises ForbiddenError
    with pytest.raises(ForbiddenError):
        check_model_allowed(key, "llama-3.2-3b-instruct")

    with pytest.raises(ForbiddenError):
        check_model_allowed(key, "mistral-7b")

    # Admin key bypasses
    admin_key = ApiKeyRecord(
        id="admin-key-id",
        name="admin-key",
        scopes=["admin"],
        created_at=12345.0,
        role="admin",
        model_allowlist=["llama-3.2-1b-instruct"],
    )
    check_model_allowed(admin_key, "mistral-7b")
