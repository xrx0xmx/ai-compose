import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "admin" / "app.py"


def load_module(monkeypatch):
    monkeypatch.setenv("ADMIN_JWT_SECRET", "test-secret")
    monkeypatch.setenv("MATXA_ADAPTER_URL", "http://matxa-adapter:8002")
    monkeypatch.setenv("WEBUI_DB_PATH", "/nonexistent.db")
    spec = importlib.util.spec_from_file_location("admin_app", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_token(module):
    return module.create_jwt({"email": "admin@test.com", "name": "Admin", "role": "admin"})


def test_tts_voices_proxies_adapter(monkeypatch):
    module = load_module(monkeypatch)
    client = TestClient(module.app)
    token = make_token(module)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"voices": [{"id": "central-grau", "name": "Grau (Central)"}]}
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        r = client.get("/api/tts/voices", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert r.json()["voices"][0]["id"] == "central-grau"


def test_tts_voices_requires_auth(monkeypatch):
    module = load_module(monkeypatch)
    client = TestClient(module.app)

    r = client.get("/api/tts/voices")
    assert r.status_code == 401


def test_tts_voices_503_when_adapter_down(monkeypatch):
    import requests as req_lib
    module = load_module(monkeypatch)
    client = TestClient(module.app)
    token = make_token(module)

    with patch("requests.get", side_effect=req_lib.ConnectionError("down")):
        r = client.get("/api/tts/voices", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 503


def test_tts_speech_returns_wav(monkeypatch):
    module = load_module(monkeypatch)
    client = TestClient(module.app)
    token = make_token(module)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"RIFF....fake-wav-bytes"
    mock_resp.headers = {"Content-Type": "audio/wav"}

    with patch("requests.post", return_value=mock_resp):
        r = client.post(
            "/api/tts/speech",
            json={"text": "Hola, això és una prova.", "voice": "central-grau"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == b"RIFF....fake-wav-bytes"


def test_tts_speech_requires_auth(monkeypatch):
    module = load_module(monkeypatch)
    client = TestClient(module.app)

    r = client.post("/api/tts/speech", json={"text": "test", "voice": "central-grau"})
    assert r.status_code == 401


def test_tts_speech_passes_adapter_error(monkeypatch):
    module = load_module(monkeypatch)
    client = TestClient(module.app)
    token = make_token(module)

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {"detail": "Unknown voice 'bad-voice'"}

    with patch("requests.post", return_value=mock_resp):
        r = client.post(
            "/api/tts/speech",
            json={"text": "test", "voice": "bad-voice"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r.status_code == 400
    assert "Unknown voice" in r.json()["detail"]


def test_tts_models_proxies_adapter(monkeypatch):
    module = load_module(monkeypatch)
    client = TestClient(module.app)
    token = make_token(module)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "object": "list",
        "data": [
            {"id": "tts-1", "object": "model", "owned_by": "matxa-adapter", "label": "Matxa TTS"},
            {"id": "tts-catotron", "object": "model", "owned_by": "matxa-adapter", "label": "Catotron"},
        ],
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        r = client.get("/api/tts/models", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["data"]]
    assert "tts-1" in ids
    assert "tts-catotron" in ids


def test_tts_models_requires_auth(monkeypatch):
    module = load_module(monkeypatch)
    client = TestClient(module.app)

    r = client.get("/api/tts/models")
    assert r.status_code == 401


def test_tts_speech_passes_model_to_adapter(monkeypatch):
    module = load_module(monkeypatch)
    client = TestClient(module.app)
    token = make_token(module)

    captured = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        mock = MagicMock()
        mock.status_code = 200
        mock.content = b"RIFF....fake"
        mock.headers = {"Content-Type": "audio/wav"}
        return mock

    with patch("requests.post", side_effect=fake_post):
        r = client.post(
            "/api/tts/speech",
            json={"text": "Hola", "voice": "central-grau", "model": "tts-catotron"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r.status_code == 200
    assert captured["json"]["model"] == "tts-catotron"
