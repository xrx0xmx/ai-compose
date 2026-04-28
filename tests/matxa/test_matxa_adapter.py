import importlib.util
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "matxa-adapter" / "main.py"


def load_module(monkeypatch, *, matxa_url="", catotron_url=""):
    monkeypatch.setenv("MATXA_BACKEND_URL", matxa_url)
    monkeypatch.setenv("CATOTRON_BACKEND_URL", catotron_url)
    spec = importlib.util.spec_from_file_location("matxa_adapter_main", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_health_lists_configured_backends(monkeypatch) -> None:
    module = load_module(monkeypatch, matxa_url="http://matxa:8000")
    client = TestClient(module.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "backends": {"tts-1": "http://matxa:8000"}}


def test_health_lists_catotron_backend(monkeypatch) -> None:
    module = load_module(monkeypatch, catotron_url="http://catotron:8000")
    client = TestClient(module.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["backends"] == {"tts-catotron": "http://catotron:8000"}


def test_ready_probes_all_backends(monkeypatch) -> None:
    probed = []
    module = load_module(monkeypatch, matxa_url="http://matxa:8000", catotron_url="http://catotron:8000")

    def fake_get(url, timeout):
        probed.append(url)
        return SimpleNamespace(status_code=200, text="", json=lambda: {})

    monkeypatch.setattr(module.requests, "get", fake_get)
    client = TestClient(module.app)

    response = client.get("/ready")

    assert response.status_code == 200
    assert any("matxa" in u for u in probed)
    assert any("catotron" in u for u in probed)


def test_ready_returns_503_when_backend_is_down(monkeypatch) -> None:
    module = load_module(monkeypatch, matxa_url="http://matxa:8000")

    def fake_get(url, timeout):
        raise module.requests.RequestException("connection refused")

    monkeypatch.setattr(module.requests, "get", fake_get)
    client = TestClient(module.app)

    response = client.get("/ready")

    assert response.status_code == 503
    assert "Backend not ready" in response.json()["detail"]


def test_list_voices_returns_expected_catalog(monkeypatch) -> None:
    module = load_module(monkeypatch, matxa_url="http://matxa:8000")
    client = TestClient(module.app)

    response = client.get("/v1/audio/voices")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["voices"]) == 8
    assert payload["voices"][0]["id"] == "balear-quim"
    assert any(item["id"] == "central-grau" for item in payload["voices"])


def test_list_models_returns_only_configured_backends(monkeypatch) -> None:
    module = load_module(monkeypatch, matxa_url="http://matxa:8000")
    client = TestClient(module.app)

    response = client.get("/v1/models")

    assert response.status_code == 200
    ids = [m["id"] for m in response.json()["data"]]
    assert ids == ["tts-1"]
    assert "tts-catotron" not in ids


def test_list_models_returns_both_when_both_configured(monkeypatch) -> None:
    module = load_module(monkeypatch, matxa_url="http://matxa:8000", catotron_url="http://catotron:8000")
    client = TestClient(module.app)

    response = client.get("/v1/models")

    assert response.status_code == 200
    ids = [m["id"] for m in response.json()["data"]]
    assert "tts-1" in ids
    assert "tts-catotron" in ids


def test_audio_speech_matxa_model_uses_matxa_payload(monkeypatch) -> None:
    module = load_module(monkeypatch, matxa_url="http://matxa:8000")
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(
            status_code=200,
            content=b"RIFF....WAVE",
            headers={"Content-Type": "audio/wav", "Content-Disposition": 'inline; filename="speech.wav"'},
        )

    monkeypatch.setattr(module.requests, "post", fake_post)
    client = TestClient(module.app)

    response = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "Bon dia", "voice": "central-grau", "speed": 1.5},
    )

    assert response.status_code == 200
    assert captured["url"] == "http://matxa:8000/api/tts"
    assert captured["json"] == {
        "text": "Bon dia",
        "voice": "grau",
        "accent": "central",
        "type": "text",
        "length_scale": 0.6667,
    }


def test_audio_speech_catotron_model_uses_catotron_payload(monkeypatch) -> None:
    module = load_module(monkeypatch, catotron_url="http://catotron:8000")
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(
            status_code=200,
            content=b"RIFF....WAVE",
            headers={"Content-Type": "audio/wav", "Content-Disposition": 'inline; filename="speech.wav"'},
        )

    monkeypatch.setattr(module.requests, "post", fake_post)
    client = TestClient(module.app)

    response = client.post(
        "/v1/audio/speech",
        json={"model": "tts-catotron", "input": "Bon dia", "voice": "central-grau", "speed": 2.0},
    )

    assert response.status_code == 200
    assert captured["url"] == "http://catotron:8000/api/tts"
    assert captured["json"] == {
        "text": "Bon dia",
        "voice": "grau",
        "language": "ca-es",
        "type": "text",
        "speech_speed": 2.0,
    }


def test_audio_speech_rejects_unknown_model(monkeypatch) -> None:
    module = load_module(monkeypatch, matxa_url="http://matxa:8000")
    client = TestClient(module.app)

    response = client.post(
        "/v1/audio/speech",
        json={"model": "tts-unknown", "input": "Hola", "voice": "central-grau"},
    )

    assert response.status_code == 400
    assert "Unknown model" in response.json()["detail"]


def test_audio_speech_rejects_unknown_voice(monkeypatch) -> None:
    module = load_module(monkeypatch, matxa_url="http://matxa:8000")
    client = TestClient(module.app)

    response = client.post(
        "/v1/audio/speech",
        json={"input": "Hola", "voice": "unknown-voice", "speed": 1.0},
    )

    assert response.status_code == 400
    assert "Unknown voice" in response.json()["detail"]


def test_audio_speech_rejects_invalid_speed(monkeypatch) -> None:
    module = load_module(monkeypatch, matxa_url="http://matxa:8000")
    client = TestClient(module.app)

    response = client.post(
        "/v1/audio/speech",
        json={"input": "Hola", "voice": "central-grau", "speed": 5.0},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "speed must be between 0.25 and 4.0"


def test_audio_speech_propagates_backend_error(monkeypatch) -> None:
    module = load_module(monkeypatch, matxa_url="http://matxa:8000")

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
