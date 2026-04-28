# TTS Motor Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Añadir un selector de motor TTS (Matxa / Catotron) en la pestaña Text2Speech del admin panel, permitiendo al usuario elegir qué backend sintetiza el audio en tiempo real.

**Architecture:** El matxa-adapter pasa de tener un único backend configurado globalmente (`BACKEND_STYLE`) a soportar dos backends opcionales (`MATXA_BACKEND_URL` para `tts-1`, `CATOTRON_BACKEND_URL` para `tts-catotron`). El endpoint `/v1/models` expone sólo los backends configurados. El admin panel añade un dropdown de motor que se popula dinámicamente, y pasa el `model` elegido en cada petición de síntesis.

**Tech Stack:** Python/FastAPI (matxa-adapter, admin), HTML/JS embebido en admin/app.py, Docker Compose.

---

## File Map

| Archivo | Cambio |
|---------|--------|
| `matxa-adapter/main.py` | Reemplazar BACKEND_STYLE global por routing por-modelo basado en MATXA_BACKEND_URL / CATOTRON_BACKEND_URL |
| `admin/app.py` | Añadir GET /api/tts/models, pasar model en speech request, añadir selector de motor en HTML/JS |
| `docker-compose.local.yml` | Cambiar MATXA_BACKEND_URL+BACKEND_STYLE por CATOTRON_BACKEND_URL |
| `tests/matxa/test_matxa_adapter.py` | Actualizar tests existentes, añadir tests de routing catotron, test de /v1/models |
| `tests/admin/test_admin_tts.py` | Añadir test /api/tts/models, actualizar test speech con model field |

---

## Task 1: Refactorizar matxa-adapter para routing multi-backend

**Files:**
- Modify: `matxa-adapter/main.py`

### Paso 1: Reescribir la sección de configuración y registro de backends

Reemplaza las líneas de configuración y `VOICE_MAP` en `matxa-adapter/main.py` (actualmente líneas 9-36) con:

```python
MATXA_BACKEND_URL = os.getenv("MATXA_BACKEND_URL", "").rstrip("/")
CATOTRON_BACKEND_URL = os.getenv("CATOTRON_BACKEND_URL", "").rstrip("/")
MATXA_REQUEST_TIMEOUT_SECONDS = int(os.getenv("MATXA_REQUEST_TIMEOUT_SECONDS", "120"))
MATXA_HEALTH_TIMEOUT_SECONDS = float(os.getenv("MATXA_HEALTH_TIMEOUT_SECONDS", "1.0"))
DEFAULT_VOICE = os.getenv("MATXA_DEFAULT_VOICE", "central-grau")
MAX_INPUT_LENGTH = int(os.getenv("MATXA_MAX_INPUT_LENGTH", "500"))

VOICE_MAP: Dict[str, Dict[str, str]] = {
    "balear-quim":          {"accent": "balear",          "voice": "quim", "language": "ca-ba", "name": "Quim (Balear)"},
    "balear-olga":          {"accent": "balear",          "voice": "olga", "language": "ca-ba", "name": "Olga (Balear)"},
    "central-grau":         {"accent": "central",         "voice": "grau", "language": "ca-es", "name": "Grau (Central)"},
    "central-elia":         {"accent": "central",         "voice": "elia", "language": "ca-es", "name": "Elia (Central)"},
    "nord-occidental-pere": {"accent": "nord-occidental", "voice": "pere", "language": "ca-nw", "name": "Pere (Nord-occidental)"},
    "nord-occidental-emma": {"accent": "nord-occidental", "voice": "emma", "language": "ca-nw", "name": "Emma (Nord-occidental)"},
    "valencia-lluc":        {"accent": "valencia",        "voice": "lluc", "language": "ca-va", "name": "Lluc (Valencià)"},
    "valencia-gina":        {"accent": "valencia",        "voice": "gina", "language": "ca-va", "name": "Gina (Valencià)"},
}

# Registry: model_id -> {url, style, label}
BACKENDS: Dict[str, Dict[str, str]] = {}
if MATXA_BACKEND_URL:
    BACKENDS["tts-1"] = {"url": MATXA_BACKEND_URL, "style": "matxa", "label": "Matxa TTS"}
if CATOTRON_BACKEND_URL:
    BACKENDS["tts-catotron"] = {"url": CATOTRON_BACKEND_URL, "style": "catotron", "label": "Catotron"}

DEFAULT_MODEL = next(iter(BACKENDS), "tts-1")
```

- [ ] Aplica este cambio en `matxa-adapter/main.py`. Elimina las líneas originales `MATXA_BACKEND_URL = ...`, `BACKEND_STYLE = ...`, `DEFAULT_MODEL = ...` y el bloque `VOICE_MAP` (que ahora tiene `language` pero no `BACKEND_STYLE`). Deja exactamente el bloque de arriba.

### Paso 2: Actualizar probe_backend, health y ready

Reemplaza las funciones `probe_backend`, `health` y `ready` (actualmente ~líneas 100-126):

```python
def probe_backend(url: str) -> None:
    try:
        response = requests.get(f"{url}/health", timeout=MATXA_HEALTH_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail=f"Backend not ready: {exc}") from exc
    # catotron-cpu may not expose /health — treat 404 as "up"
    if response.status_code >= 400 and response.status_code != 404:
        detail = detail_from_response(response)
        raise HTTPException(status_code=503, detail=f"Backend not ready: {detail}")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "backends": {k: v["url"] for k, v in BACKENDS.items()}}


@app.get("/ready")
def ready() -> dict:
    for config in BACKENDS.values():
        probe_backend(config["url"])
    return {"status": "ok", "backends": {k: v["url"] for k, v in BACKENDS.items()}}
```

- [ ] Aplica este cambio. Asegúrate de que `probe_backend` ya no usa `MATXA_BACKEND_URL` directamente.

### Paso 3: Actualizar /v1/models y create_speech

Reemplaza `list_models` y `create_speech` (actualmente ~líneas 130-193):

```python
@app.get("/v1/models")
def list_models() -> dict:
    return {
        "object": "list",
        "data": [
            {"id": model_id, "object": "model", "owned_by": "matxa-adapter", "label": config["label"]}
            for model_id, config in BACKENDS.items()
        ],
    }


@app.post("/v1/audio/speech")
def create_speech(request: OpenAISpeechRequest) -> Response:
    input_text = validate_input(request.input)
    voice = validate_voice(request.voice)
    speed = validate_speed(request.speed)
    validate_response_format(request.response_format)

    model_id = (request.model or DEFAULT_MODEL).strip()
    if model_id not in BACKENDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model '{model_id}'. Available: {', '.join(BACKENDS)}",
        )
    backend = BACKENDS[model_id]

    if backend["style"] == "catotron":
        payload = {
            "text": input_text,
            "voice": voice["voice"],
            "language": voice["language"],
            "type": "text",
            "speech_speed": speed,
        }
    else:
        payload = {
            "text": input_text,
            "voice": voice["voice"],
            "accent": voice["accent"],
            "type": "text",
            "length_scale": round(1.0 / speed, 4),
        }

    try:
        backend_response = requests.post(
            f"{backend['url']}/api/tts",
            json=payload,
            timeout=MATXA_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Backend request failed: {exc}") from exc

    if backend_response.status_code >= 400:
        detail = detail_from_response(backend_response)
        status_code = backend_response.status_code if backend_response.status_code < 500 else 502
        raise HTTPException(status_code=status_code, detail=detail)

    headers = {
        "Content-Disposition": backend_response.headers.get(
            "Content-Disposition", 'inline; filename="speech.wav"'
        )
    }
    media_type = backend_response.headers.get("Content-Type", "audio/wav")
    return Response(content=backend_response.content, media_type=media_type, headers=headers)
```

- [ ] Aplica este cambio. Verifica que `OpenAISpeechRequest` sigue definida igual (no cambia).

---

## Task 2: Tests del adapter refactorizado

**Files:**
- Modify: `tests/matxa/test_matxa_adapter.py`

- [ ] **Paso 1: Escribe los tests failing**

Reemplaza el contenido de `tests/matxa/test_matxa_adapter.py` con:

```python
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
```

- [ ] **Paso 2: Ejecuta los tests para verificar que fallan correctamente**

```bash
pytest tests/matxa/test_matxa_adapter.py -v
```

Deben fallar varios tests porque el adapter aún no está refactorizado.

- [ ] **Paso 3: Aplica los cambios del Task 1** (si no los has aplicado ya)

- [ ] **Paso 4: Ejecuta los tests y verifica que todos pasan**

```bash
pytest tests/matxa/test_matxa_adapter.py -v
```

Resultado esperado: todos PASS.

- [ ] **Paso 5: Commit**

```bash
git add matxa-adapter/main.py tests/matxa/test_matxa_adapter.py
git commit -m "feat(adapter): support per-model multi-backend routing (tts-1 / tts-catotron)"
```

---

## Task 3: Actualizar docker-compose.local.yml

**Files:**
- Modify: `docker-compose.local.yml`

- [ ] En el servicio `matxa-adapter`, reemplaza:

```yaml
    environment:
      - MATXA_BACKEND_URL=http://catotron-cpu:8000
      - BACKEND_STYLE=catotron
      - MATXA_DEFAULT_VOICE=central-grau
      - MATXA_REQUEST_TIMEOUT_SECONDS=120
      - MATXA_HEALTH_TIMEOUT_SECONDS=2.0
```

por:

```yaml
    environment:
      - CATOTRON_BACKEND_URL=http://catotron-cpu:8000
      - MATXA_DEFAULT_VOICE=central-grau
      - MATXA_REQUEST_TIMEOUT_SECONDS=120
      - MATXA_HEALTH_TIMEOUT_SECONDS=2.0
```

- [ ] **Commit**

```bash
git add docker-compose.local.yml
git commit -m "fix(local): use CATOTRON_BACKEND_URL for catotron-cpu service"
```

---

## Task 4: Actualizar admin/app.py — backend y endpoint de modelos

**Files:**
- Modify: `admin/app.py`

### Paso 1: Añadir `model` a TTSSpeechRequest

Busca la clase `TTSSpeechRequest` (alrededor de la línea 73):

```python
class TTSSpeechRequest(BaseModel):
    text: str
    voice: str
```

Reemplázala por:

```python
class TTSSpeechRequest(BaseModel):
    text: str
    voice: str
    model: str = "tts-1"
```

- [ ] Aplica el cambio.

### Paso 2: Añadir GET /api/tts/models y actualizar tts_speech

Busca el bloque `# TTS proxy` (alrededor de la línea 668). Añade el nuevo endpoint después de `tts_voices` y actualiza `tts_speech` para pasar el modelo:

```python
@app.get("/api/tts/models")
def tts_models(user: dict = Depends(get_current_user)) -> dict:
    try:
        r = requests.get(f"{MATXA_ADAPTER_URL}/v1/models", timeout=5)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail=f"Matxa adapter unavailable: {exc}")


@app.post("/api/tts/speech")
def tts_speech(req: TTSSpeechRequest, user: dict = Depends(get_current_user)) -> Response:
    payload = {
        "model": req.model,
        "input": req.text,
        "voice": req.voice,
        "response_format": "wav",
        "speed": 1.0,
    }
    try:
        r = requests.post(
            f"{MATXA_ADAPTER_URL}/v1/audio/speech",
            json=payload,
            timeout=120,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Matxa adapter request failed: {exc}")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise HTTPException(status_code=r.status_code, detail=detail)
    return Response(
        content=r.content,
        media_type="audio/wav",
        headers={"Content-Disposition": 'attachment; filename="speech.wav"'},
    )
```

- [ ] Aplica el cambio. El `tts_speech` original (que tenía `"model": "tts-1"` hardcoded) queda reemplazado. El nuevo `tts_models` se añade entre `tts_voices` y `tts_speech`.

- [ ] **Commit**

```bash
git add admin/app.py
git commit -m "feat(admin): add /api/tts/models endpoint and pass model in speech request"
```

---

## Task 5: Tests del admin actualizados

**Files:**
- Modify: `tests/admin/test_admin_tts.py`

- [ ] **Paso 1: Añade los nuevos tests y actualiza el existente**

Añade al final de `tests/admin/test_admin_tts.py`:

```python
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
```

- [ ] **Paso 2: Ejecuta todos los tests**

```bash
pytest tests/ -v
```

Resultado esperado: todos PASS.

- [ ] **Commit**

```bash
git add tests/admin/test_admin_tts.py
git commit -m "test(admin): add tests for /api/tts/models and model pass-through in speech"
```

---

## Task 6: Añadir selector de motor en el HTML/JS del admin panel

**Files:**
- Modify: `admin/app.py` (sección HTML/JS embebida)

### Paso 1: Añadir el dropdown de motor en el HTML

Busca el bloque HTML de la sección TTS (alrededor de la línea 1079). Dentro de `<div style="max-width:640px...">`, añade el selector de motor **antes** del selector de voz:

```html
          <div class="form-group" style="margin:0">
            <label for="tts-model">Motor</label>
            <select id="tts-model" style="width:100%; background:var(--bg); border:1px solid var(--border); border-radius:6px; color:var(--text); padding:10px 12px; font-size:.95rem;">
              <option value="tts-1">Matxa TTS</option>
            </select>
          </div>
```

Añádelo justo antes de:
```html
          <div class="form-group" style="margin:0">
            <label for="tts-voice">Veu</label>
```

- [ ] Aplica el cambio.

### Paso 2: Actualizar loadTTSVoices para cargar también los modelos

Busca la función `loadTTSVoices` (alrededor de la línea 1584). Reemplaza la función entera con:

```javascript
async function loadTTSVoices() {
  const voiceSel = document.getElementById('tts-voice');
  const modelSel = document.getElementById('tts-model');
  if (!voiceSel || voiceSel.dataset.loaded) return;
  try {
    const [voiceData, modelData] = await Promise.all([
      apiFetch('/api/tts/voices'),
      apiFetch('/api/tts/models'),
    ]);
    voiceSel.innerHTML = voiceData.voices.map(v =>
      `<option value="${v.id}">${v.name}</option>`
    ).join('');
    if (modelData && modelData.data && modelData.data.length > 0) {
      modelSel.innerHTML = modelData.data.map(m =>
        `<option value="${m.id}">${m.label || m.id}</option>`
      ).join('');
    }
    voiceSel.dataset.loaded = '1';
  } catch (_) {
    // keep default options
  }
}
```

- [ ] Aplica el cambio.

### Paso 3: Actualizar generateSpeech para incluir el modelo seleccionado

Busca la función `generateSpeech` (alrededor de la línea 1599). En la línea que construye el body del fetch, añade `model`:

Busca:
```javascript
      body: JSON.stringify({ text, voice }),
```

Reemplaza por:
```javascript
      const model = document.getElementById('tts-model').value;
      body: JSON.stringify({ text, voice, model }),
```

Espera — esto no es sintácticamente válido. La línea completa con contexto es:

```javascript
    const r = await fetch('/api/tts/speech', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice }),
    });
```

Reemplaza ese bloque con:

```javascript
    const model = document.getElementById('tts-model').value;
    const r = await fetch('/api/tts/speech', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice, model }),
    });
```

- [ ] Aplica el cambio.

- [ ] **Commit**

```bash
git add admin/app.py
git commit -m "feat(admin): add TTS motor selector dropdown with dynamic model loading"
```

---

## Task 7: Verificación end-to-end

- [ ] **Ejecuta todos los tests**

```bash
pytest tests/ -v
```

Resultado esperado: todos PASS.

- [ ] **Levanta los servicios locales con TTS**

```bash
make local-up        # Ollama + LiteLLM + etc.
make local-tts-up    # catotron-cpu + matxa-adapter
```

- [ ] **Verifica el adapter directamente**

```bash
# Debe mostrar sólo tts-catotron (sólo CATOTRON_BACKEND_URL está configurado en local)
curl http://localhost:8012/v1/models | jq .

# Debe retornar audio WAV
curl -X POST http://localhost:8012/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-catotron","input":"Hola món","voice":"central-grau"}' \
  --output test.wav && file test.wav
```

- [ ] **Abre el admin panel en el navegador**

Ve a `http://localhost` (o el puerto del admin) → pestaña Text2Speech.

- Verifica que aparece el dropdown "Motor" con "Catotron" (único en local)
- Selecciona una voz, escribe texto y genera audio
- Verifica que el audio se reproduce

- [ ] **En producción (si aplica)**: el adapter sólo expone `tts-1` (Matxa TTS) porque `CATOTRON_BACKEND_URL` no está configurado. El dropdown mostrará sólo "Matxa TTS". Sin cambios en docker-compose.prod.yml necesarios.
