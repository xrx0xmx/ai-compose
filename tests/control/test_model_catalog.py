import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "control" / "app.py"


def load_module(monkeypatch):
    monkeypatch.setenv("MODEL_SWITCHER_TOKEN", "test-token")
    spec = importlib.util.spec_from_file_location("control_app", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_deepseek_r1_32b_awq_is_registered(monkeypatch):
    module = load_module(monkeypatch)

    assert "deepseek-r1-32b-awq" in module.MODELS
    assert module.MODELS["deepseek-r1-32b-awq"]["container"] == "vllm-deepseek32b"
    assert module.MODELS["deepseek-r1-32b-awq"]["template"] == "deepseek-r1-32b-awq.yml"
    assert module.MODELS["deepseek-r1-32b-awq"]["litellm_model"] == "deepseek-r1-32b-awq"
