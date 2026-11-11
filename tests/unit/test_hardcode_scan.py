"""Hardcode scanner regression test per Refurbishment Plan Phase 5.

Verifies that no static hardcoded placeholder model lists or fake fallback models
remain in frontend selectors or backend installed models handlers.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

FORBIDDEN_FRONTEND_PATTERNS = [
    r"['\"]whisper-small['\"]",
    r"['\"]whisper-base['\"]\s*,\s*['\"]whisper-small['\"]",
    r"installedModels\s*=\s*useState<string\[\]>\(\s*\[\s*['\"]whisper",
    r"installedModels\s*=\s*useState<Array<\{[^}]+\}>>\(\s*\[\s*\{\s*id:\s*['\"]kokoro",
    r"value=[\"']af_heart[\"']>Heart \(Female\)",
]


def test_no_hardcoded_model_placeholders_in_frontend():
    frontend_pages = REPO_ROOT / "frontend" / "src" / "pages"
    for tsx_file in frontend_pages.glob("*.tsx"):
        content = tsx_file.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_FRONTEND_PATTERNS:
            match = re.search(pattern, content)
            assert match is None, (
                f"Forbidden hardcoded model pattern found in {tsx_file.name}: {match.group(0)}"
            )


def test_no_hardcoded_installed_models_in_gallery_api():
    gallery_file = REPO_ROOT / "app" / "api" / "gallery.py"
    content = gallery_file.read_text(encoding="utf-8")
    # Verify that list_installed_models calls get_installed_models_data instead of returning static mock
    assert "return await get_installed_models_data()" in content
    assert '"stt": [\n            {"id": "whisper-base"' not in content
