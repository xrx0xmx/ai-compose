import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "control" / "app.py"


def load_module(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_SWITCHER_TOKEN", "test-token")
    monkeypatch.setenv("MODEL_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MODEL_TEMPLATE_DIR", str(tmp_path / "templates"))
    monkeypatch.setenv("MODEL_SWITCHER_DEFAULT", "qwen-fast")

    template_dir = tmp_path / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / "qwen-fast.yml").write_text("model: qwen-fast\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location("control_app_mode_switch", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def auth_headers():
    return {"Authorization": "Bearer test-token"}


def install_fake_runtime(module, monkeypatch, initial_statuses):
    states = {
        name: {"status": status, "health": ("healthy" if status == "running" else None)}
        for name, status in initial_statuses.items()
    }

    def ensure(name):
        states.setdefault(name, {"status": "exited", "health": None})
        return states[name]

    def fake_state_snapshot(name):
        state = ensure(name)
        return {
            "exists": True,
            "status": state["status"],
            "health": state["health"],
        }

    def fake_container_json(name):
        state = ensure(name)
        return {
            "State": {
                "Status": state["status"],
                "Health": {"Status": state["health"]} if state["health"] else None,
            }
        }

    def fake_container_start(name):
        state = ensure(name)
        state["status"] = "running"
        state["health"] = "healthy"

    def fake_container_stop(name):
        state = ensure(name)
        state["status"] = "exited"
        state["health"] = None

    monkeypatch.setattr(module, "state_snapshot", fake_state_snapshot)
    monkeypatch.setattr(module, "container_json", fake_container_json)
    monkeypatch.setattr(module, "container_start", fake_container_start)
    monkeypatch.setattr(module, "container_stop", fake_container_stop)
    monkeypatch.setattr(module, "wait_container_ready", lambda name, timeout: None)
    monkeypatch.setattr(module, "wait_litellm_model", lambda model, timeout: None)
    return states


def test_status_reports_comfy_mode_and_lease(monkeypatch, tmp_path):
    module = load_module(monkeypatch, tmp_path)
    client = TestClient(module.app)
    config_dir = Path(module.CONFIG_DIR)
    config_dir.mkdir(parents=True, exist_ok=True)
    Path(module.ACTIVE_MODEL_FILE).write_text("qwen-fast\n", encoding="utf-8")
    Path(module.ACTIVE_MODE_FILE).write_text("comfy\n", encoding="utf-8")
    Path(module.ACTIVE_COMFY_LEASE_FILE).write_text("2099-01-01T00:00:00+00:00\n", encoding="utf-8")

    install_fake_runtime(
        module,
        monkeypatch,
        {
            "litellm": "exited",
            "comfyui": "running",
            "vllm-fast": "exited",
            "vllm-quality": "exited",
            "vllm-deepseek": "exited",
            "vllm-deepseek32b": "exited",
            "vllm-qwen32b": "exited",
        },
    )

    response = client.get("/status", headers=auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_mode"] == "comfy"
    assert payload["mode"]["active"] == "comfy"
    assert payload["mode"]["lease"]["expired"] is False
    assert payload["comfyui"]["status"] == "running"


def test_mode_switch_accepts_comfy(monkeypatch, tmp_path):
    module = load_module(monkeypatch, tmp_path)
    client = TestClient(module.app)
    config_dir = Path(module.CONFIG_DIR)
    config_dir.mkdir(parents=True, exist_ok=True)
    Path(module.ACTIVE_MODEL_FILE).write_text("qwen-fast\n", encoding="utf-8")
    Path(module.ACTIVE_MODE_FILE).write_text("llm\n", encoding="utf-8")

    states = install_fake_runtime(
        module,
        monkeypatch,
        {
            "litellm": "running",
            "comfyui": "exited",
            "vllm-fast": "running",
            "vllm-quality": "exited",
            "vllm-deepseek": "exited",
            "vllm-deepseek32b": "exited",
            "vllm-qwen32b": "exited",
        },
    )

    response = client.post(
        "/mode/switch",
        json={"mode": "comfy", "ttl_minutes": 30, "wait_for_ready": True},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_mode"] == "comfy"
    assert payload["comfyui"]["status"] == "running"
    assert states["litellm"]["status"] == "exited"
    assert states["vllm-fast"]["status"] == "exited"
    assert Path(module.ACTIVE_MODE_FILE).read_text(encoding="utf-8").strip() == "comfy"
    assert Path(module.ACTIVE_COMFY_LEASE_FILE).exists()


def test_mode_switch_back_to_llm_stops_comfy(monkeypatch, tmp_path):
    module = load_module(monkeypatch, tmp_path)
    client = TestClient(module.app)
    config_dir = Path(module.CONFIG_DIR)
    config_dir.mkdir(parents=True, exist_ok=True)
    Path(module.ACTIVE_MODEL_FILE).write_text("qwen-fast\n", encoding="utf-8")
    Path(module.ACTIVE_MODE_FILE).write_text("comfy\n", encoding="utf-8")
    Path(module.ACTIVE_COMFY_LEASE_FILE).write_text("2099-01-01T00:00:00+00:00\n", encoding="utf-8")

    states = install_fake_runtime(
        module,
        monkeypatch,
        {
            "litellm": "exited",
            "comfyui": "running",
            "vllm-fast": "exited",
            "vllm-quality": "exited",
            "vllm-deepseek": "exited",
            "vllm-deepseek32b": "exited",
            "vllm-qwen32b": "exited",
        },
    )

    response = client.post(
        "/mode/switch",
        json={"mode": "llm", "model": "qwen-fast"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_mode"] == "llm"
    assert payload["active_model"] == "qwen-fast"
    assert states["comfyui"]["status"] == "exited"
    assert states["litellm"]["status"] == "running"
    assert states["vllm-fast"]["status"] == "running"
    assert Path(module.ACTIVE_MODE_FILE).read_text(encoding="utf-8").strip() == "llm"
    assert not Path(module.ACTIVE_COMFY_LEASE_FILE).exists()
