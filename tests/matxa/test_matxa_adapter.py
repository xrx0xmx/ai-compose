import importlib.util
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "matxa-adapter" / "main.py"


def load_module():
    spec = importlib.util.spec_from_file_location("matxa_adapter_main", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_health_reports_backend_url(monkeypatch) -> None:
    monkeypatch.setenv("MATXA_BACKEND_URL", "http://matxa-backend:8000")
    module = load_module()
    client = TestClient(module.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "backend": "http://matxa-backend:8000"}


def test_list_voices_returns_expected_catalog() -> None:
    module = load_module()
    client = TestClient(module.app)

    response = client.get("/v1/audio/voices")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["voices"]) == 8
    assert payload["voices"][0]["id"] == "balear-quim"
    assert any(item["id"] == "central-grau" for item in payload["voices"])


def test_audio_speech_maps_voice_and_speed(monkeypatch) -> None:
    module = load_module()
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return SimpleNamespace(
            status_code=200,
            content=b"RIFF....WAVE",
            headers={"Content-Type": "audio/wav", "Content-Disposition": 'inline; filename="speech.wav"'},
        )

    monkeypatch.setattr(module.requests, "post", fake_post)
    client = TestClient(module.app)

    response = client.post(
        "/v1/audio/speech",
        json={
            "model": "tts-1",
            "input": "Bon dia, com estàs?",
            "voice": "central-grau",
            "response_format": "wav",
            "speed": 1.5,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert captured["url"].endswith("/api/tts")
    assert captured["json"] == {
        "text": "Bon dia, com estàs?",
        "voice": "grau",
        "accent": "central",
        "type": "text",
        "length_scale": 0.6667,
    }


def test_audio_speech_rejects_unknown_voice() -> None:
    module = load_module()
    client = TestClient(module.app)

    response = client.post(
        "/v1/audio/speech",
        json={"input": "Hola", "voice": "unknown-voice", "speed": 1.0},
    )

    assert response.status_code == 400
    assert "Unknown voice" in response.json()["detail"]


def test_audio_speech_rejects_invalid_speed() -> None:
    module = load_module()
    client = TestClient(module.app)

    response = client.post(
        "/v1/audio/speech",
        json={"input": "Hola", "voice": "central-grau", "speed": 5.0},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "speed must be between 0.25 and 4.0"


def test_audio_speech_propagates_backend_error(monkeypatch) -> None:
    module = load_module()

    def fake_post(url, json, timeout):
        return SimpleNamespace(
            status_code=400,
            headers={"Content-Type": "application/json"},
            text="",
            json=lambda: {"detail": "backend rejected request"},
        )

    monkeypatch.setattr(module.requests, "post", fake_post)
    client = TestClient(module.app)

    response = client.post(
        "/v1/audio/speech",
        json={"input": "Hola", "voice": "central-grau", "speed": 1.0},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "backend rejected request"
