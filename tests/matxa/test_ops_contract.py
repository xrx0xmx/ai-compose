from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_prod_compose_defines_matxa_services() -> None:
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "matxa-backend:" in compose
    assert "matxa-adapter:" in compose
    assert "/opt/ai/matxa-cache:/cache" in compose
    assert '127.0.0.1:${MATXA_ADAPTER_HOST_PORT:-8012}:8002' in compose
    assert "capabilities: [gpu]" in compose


def test_ops_script_wires_matxa_into_make_flow() -> None:
    ops = (ROOT / "scripts" / "ops.sh").read_text(encoding="utf-8")
    assert "matxa-backend" in ops
    assert "matxa-adapter" in ops
    assert "cmd_test_tts" in ops
    assert "compose_webui build admin-panel model-switcher matxa-backend matxa-adapter" in ops


def test_makefile_exposes_tts_smoke_target() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "make test-tts" in makefile
    assert "test-tts: ; @$(OPS_SCRIPT) test-tts" in makefile
