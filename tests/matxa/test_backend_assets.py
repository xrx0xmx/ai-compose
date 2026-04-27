from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_backend_dockerfile_pins_upstream_commit() -> None:
    dockerfile = (ROOT / "matxa-backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "MATXA_UPSTREAM_REF=b0084b203100b83ace8dfd2fde09fd18eb875e18" in dockerfile
    assert "MATXA_RUNTIME=cuda" in dockerfile
    assert "0001-enable-cuda-provider.patch" in dockerfile


def test_backend_entrypoint_populates_cache_and_symlinks() -> None:
    entrypoint = (ROOT / "matxa-backend" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "hf download projecte-aina/matxa-tts-cat-multiaccent" in entrypoint
    assert 'ln -sf "${MODEL_FILE}"' in entrypoint
    assert 'exec python3 -u app.py' in entrypoint


def test_backend_patch_enables_runtime_provider_selection() -> None:
    patch = (ROOT / "matxa-backend" / "patches" / "0001-enable-cuda-provider.patch").read_text(
        encoding="utf-8"
    )
    assert "MATXA_EXECUTION_PROVIDER" in patch
    assert "CUDAExecutionProvider" in patch
    assert "logger.info" in patch
