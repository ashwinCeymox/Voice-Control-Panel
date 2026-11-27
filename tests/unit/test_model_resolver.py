"""Unit tests for generic LocalAI streaming model YAML -> GGUF resolver and bridge serving path."""

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.core.config import settings
import services.streaming_bridge.calibrate as cal
import services.streaming_bridge.server as bridge_module
from services.streaming_bridge.engines.base import EngineChunkResult, EngineFinalResult, StreamingEngine
from services.streaming_bridge.model_resolver import (
    ModelResolutionError,
    resolve_streaming_model_gguf,
)


def test_resolve_eou_and_scene_yamls(tmp_path):
    """Resolver must generically map LocalAI model YAMLs (eou, scene, nemotron) to their GGUF paths."""
    eou_gguf = tmp_path / "parakeet-cpp" / "realtime_eou_120m-v1-f16.gguf"
    scene_gguf = tmp_path / "parakeet-cpp" / "realtime_scene-f16.gguf"
    eou_gguf.parent.mkdir(parents=True, exist_ok=True)
    eou_gguf.write_bytes(b"gguf")
    scene_gguf.write_bytes(b"gguf")

    (tmp_path / "parakeet-cpp-realtime_eou_120m-v1.yaml").write_text(
        "backend: parakeet-cpp\n"
        "name: parakeet-cpp-realtime_eou_120m-v1\n"
        "parameters:\n"
        "  model: parakeet-cpp/realtime_eou_120m-v1-f16.gguf\n",
        encoding="utf-8",
    )
    (tmp_path / "parakeet-cpp-realtime-scene.yaml").write_text(
        "backend: parakeet-cpp\n"
        "name: parakeet-cpp-realtime-scene\n"
        "parameters:\n"
        "  model: parakeet-cpp/realtime_scene-f16.gguf\n",
        encoding="utf-8",
    )

    assert resolve_streaming_model_gguf("parakeet-cpp-realtime_eou_120m-v1", models_dir=tmp_path) == str(eou_gguf.resolve())
    assert resolve_streaming_model_gguf("parakeet-cpp-realtime-scene", models_dir=tmp_path) == str(scene_gguf.resolve())


def test_resolve_rejects_traversal_and_non_allowlisted(tmp_path, monkeypatch):
    """Resolver must reject path traversal, non-allowlisted models, non-parakeet backends, and missing files."""
    # 1. Name traversal
    with pytest.raises(ModelResolutionError, match="Invalid"):
        resolve_streaming_model_gguf("../escape", models_dir=tmp_path)

    # 2. YAML parameters.model traversal
    (tmp_path / "parakeet-evil.yaml").write_text(
        "backend: parakeet-cpp\n"
        "parameters:\n"
        "  model: ../../etc/passwd\n",
        encoding="utf-8",
    )
    with pytest.raises(ModelResolutionError, match="escapes models dir"):
        resolve_streaming_model_gguf("parakeet-evil", models_dir=tmp_path)

    # 3. No allow-list restriction (even if LOCALAI_ALLOWED_STT_MODELS is set, valid parakeet YAML resolves)
    gguf = tmp_path / "parakeet-cpp" / "ok.gguf"
    gguf.parent.mkdir(parents=True, exist_ok=True)
    gguf.write_bytes(b"gguf")
    (tmp_path / "parakeet-ok.yaml").write_text(
        "backend: parakeet-cpp\nparameters:\n  model: parakeet-cpp/ok.gguf\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "LOCALAI_ALLOWED_STT_MODELS", "other-model")
    assert resolve_streaming_model_gguf("parakeet-ok", models_dir=tmp_path) == str(gguf.resolve())

    # 4. Non-parakeet backend rejected
    monkeypatch.setattr(settings, "LOCALAI_ALLOWED_STT_MODELS", None)
    (tmp_path / "whisper-base.yaml").write_text(
        "backend: whisper\nparameters:\n  model: whisper-base.bin\n",
        encoding="utf-8",
    )
    with pytest.raises(ModelResolutionError, match="not a native parakeet"):
        resolve_streaming_model_gguf("whisper-base", models_dir=tmp_path)

    # 5. Missing GGUF reports both YAML and resolved path
    (tmp_path / "parakeet-missing.yaml").write_text(
        "backend: parakeet-cpp\nparameters:\n  model: parakeet-cpp/missing.gguf\n",
        encoding="utf-8",
    )
    with pytest.raises(ModelResolutionError, match="resolved_path="):
        resolve_streaming_model_gguf("parakeet-missing", models_dir=tmp_path)


def test_server_resolves_model_name_to_gguf_and_caches(tmp_path, monkeypatch):
    """Bridge server must resolve model name -> GGUF path before engine.load and cache per model."""
    models_dir = tmp_path / "models"
    gguf = models_dir / "parakeet-cpp" / "realtime_eou_120m-v1-f16.gguf"
    gguf.parent.mkdir(parents=True, exist_ok=True)
    gguf.write_bytes(b"gguf")
    (models_dir / "parakeet-cpp-realtime_eou_120m-v1.yaml").write_text(
        "backend: parakeet-cpp\nparameters:\n  model: parakeet-cpp/realtime_eou_120m-v1-f16.gguf\n",
        encoding="utf-8",
    )

    class RecordingNativeEngine(StreamingEngine):
        def __init__(self):
            self.load_calls = []
            self._model_path = None
            self.ready = False

        async def load(self, model_path: str, language: str = "en") -> None:
            self.load_calls.append(model_path)
            self._model_path = model_path
            self.ready = True

        async def open_stream(self, language: str = "en") -> str:
            return "s1"

        async def feed(self, stream_id: str, pcm16_bytes: bytes, sample_rate: int = 16000) -> EngineChunkResult:
            return EngineChunkResult(text_delta="ok", accumulated_text="ok", eou=True)

        async def finalize(self, stream_id: str) -> EngineFinalResult:
            return EngineFinalResult(tail_text="", full_text="ok")

        async def close_stream(self, stream_id: str) -> None:
            pass

        async def close(self) -> None:
            pass

        def health(self):
            return {"engine": "ParakeetNativeEngine", "ready": self.ready}

    rec_engine = RecordingNativeEngine()
    monkeypatch.setattr(settings, "LOCALAI_MODELS_PATH", str(models_dir))
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", "test-tok")
    monkeypatch.setattr(cal, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    fp = dict(cal.get_host_fingerprint())
    calib_file = cal.get_calibration_file_path("parakeet-cpp-realtime_eou_120m-v1")
    calib_file.write_text(json.dumps({"model": "parakeet-cpp-realtime_eou_120m-v1", "host": fp, "passed": True, "admitted_streams": 2}))

    bridge_module._resolved_model_cache.clear()
    monkeypatch.setattr(bridge_module, "engine", rec_engine)

    with TestClient(bridge_module.app) as client:
        for _ in range(2):
            with client.websocket_connect("/ws?token=test-tok") as ws:
                ws.send_text(json.dumps({"type": "stream.start", "model": "parakeet-cpp-realtime_eou_120m-v1", "sample_rate": 16000}))
                assert ws.receive_json()["type"] == "stream.ready"
                ws.send_text(json.dumps({"type": "audio.commit"}))
                comp = ws.receive_json()
                assert comp["type"] == "transcript.completed"

    # Loaded with resolved GGUF path (never bare model name), and cached across both streams
    assert rec_engine.load_calls == [str(gguf.resolve())]


def test_server_surfaces_model_load_error_event(tmp_path, monkeypatch):
    """If engine.load fails, bridge must send MODEL_LOAD_FAILED with resolved path and underlying error."""
    models_dir = tmp_path / "models"
    gguf = models_dir / "parakeet-cpp" / "realtime_eou_120m-v1-f16.gguf"
    gguf.parent.mkdir(parents=True, exist_ok=True)
    gguf.write_bytes(b"gguf")
    (models_dir / "parakeet-cpp-realtime_eou_120m-v1.yaml").write_text(
        "backend: parakeet-cpp\nparameters:\n  model: parakeet-cpp/realtime_eou_120m-v1-f16.gguf\n",
        encoding="utf-8",
    )

    class FailingLoadEngine(StreamingEngine):
        async def load(self, model_path: str, language: str = "en") -> None:
            raise RuntimeError(f"parakeet_capi_load failed for '{model_path}': bad magic")

        async def open_stream(self, language: str = "en") -> str:
            return "s1"

        async def feed(self, stream_id: str, pcm16_bytes: bytes, sample_rate: int = 16000) -> EngineChunkResult:
            return EngineChunkResult()

        async def finalize(self, stream_id: str) -> EngineFinalResult:
            return EngineFinalResult()

        async def close_stream(self, stream_id: str) -> None:
            pass

        async def close(self) -> None:
            pass

        def health(self):
            return {"engine": "ParakeetNativeEngine", "ready": False}

    monkeypatch.setattr(settings, "LOCALAI_MODELS_PATH", str(models_dir))
    monkeypatch.setattr(settings, "STREAMING_BRIDGE_INTERNAL_TOKEN", "test-tok")
    monkeypatch.setattr(cal, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    fp = dict(cal.get_host_fingerprint())
    cal.get_calibration_file_path("parakeet-cpp-realtime_eou_120m-v1").write_text(
        json.dumps({"model": "parakeet-cpp-realtime_eou_120m-v1", "host": fp, "passed": True, "admitted_streams": 1})
    )
    bridge_module._resolved_model_cache.clear()
    monkeypatch.setattr(bridge_module, "engine", FailingLoadEngine())

    with TestClient(bridge_module.app) as client:
        with client.websocket_connect("/ws?token=test-tok") as ws:
            ws.send_text(json.dumps({"type": "stream.start", "model": "parakeet-cpp-realtime_eou_120m-v1", "sample_rate": 16000}))
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "MODEL_LOAD_FAILED"
            assert "realtime_eou_120m-v1-f16.gguf" in err["message"]
            assert "bad magic" in err["message"]
