import os
from typing import Dict

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

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

app = FastAPI(title="Matxa OpenAI Adapter", version="1.0.0")


class OpenAISpeechRequest(BaseModel):
    model: str | None = DEFAULT_MODEL
    input: str
    voice: str | None = DEFAULT_VOICE
    response_format: str | None = "wav"
    speed: float | None = 1.0


def voice_catalog() -> list[dict[str, str]]:
    return [{"id": voice_id, "name": meta["name"]} for voice_id, meta in VOICE_MAP.items()]


def detail_from_response(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error") or payload.get("message")
        if detail:
            return str(detail)

    text = response.text.strip()
    if text:
        return text
    return f"Matxa backend returned HTTP {response.status_code}"


def validate_voice(voice_id: str | None) -> Dict[str, str]:
    normalized = (voice_id or DEFAULT_VOICE).strip().lower()
    if normalized not in VOICE_MAP:
        allowed = ", ".join(VOICE_MAP.keys())
        raise HTTPException(status_code=400, detail=f"Unknown voice '{normalized}'. Use one of: {allowed}")
    return VOICE_MAP[normalized]


def validate_speed(speed: float | None) -> float:
    value = 1.0 if speed is None else float(speed)
    if value < 0.25 or value > 4.0:
        raise HTTPException(status_code=400, detail="speed must be between 0.25 and 4.0")
    return value


def validate_response_format(response_format: str | None) -> str:
    normalized = (response_format or "wav").strip().lower()
    if normalized not in {"wav", "wave"}:
        raise HTTPException(status_code=400, detail="Only wav response_format is supported")
    return "wav"


def validate_input(input_text: str) -> str:
    if not input_text or not input_text.strip():
        raise HTTPException(status_code=400, detail="input must not be empty")
    if len(input_text) > MAX_INPUT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"input exceeds maximum length of {MAX_INPUT_LENGTH} characters",
        )
    return input_text


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


@app.get("/v1/audio/voices")
def list_voices() -> dict[str, list[dict[str, str]]]:
    return {"voices": voice_catalog()}


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


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
