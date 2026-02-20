import os
from typing import Any, Dict, List, Optional

import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

TOKEN = os.getenv("MODEL_SWITCHER_TOKEN", "")
DOCKER_PROXY_URL = os.getenv("DOCKER_PROXY_URL", "http://docker-socket-proxy:2375")
CONFIG_DIR = os.getenv("MODEL_CONFIG_DIR", "/config")
TEMPLATE_DIR = os.getenv("MODEL_TEMPLATE_DIR", "/opt/model-configs")
DEFAULT_MODEL = os.getenv("MODEL_SWITCHER_DEFAULT", "qwen-fast")

ACTIVE_CONFIG = os.path.join(CONFIG_DIR, "active.yml")
ACTIVE_MODEL_FILE = os.path.join(CONFIG_DIR, "active.model")

MODELS: Dict[str, Dict[str, str]] = {
  "qwen-fast": {
    "container": "vllm-fast",
    "template": "qwen-fast.yml",
  },
  "qwen-quality": {
    "container": "vllm-quality",
    "template": "qwen-quality.yml",
  },
  "deepseek": {
    "container": "vllm-deepseek",
    "template": "deepseek.yml",
  },
  "qwen-max": {
    "container": "vllm-qwen32b",
    "template": "qwen-max.yml",
  },
}

LITELLM_CONTAINER = "litellm"

app = FastAPI(title="AI Model Switcher", version="2.0.0")


def require_token(authorization: Optional[str] = Header(default=None)) -> None:
  if not TOKEN:
    raise HTTPException(status_code=500, detail="MODEL_SWITCHER_TOKEN not set")
  if not authorization or not authorization.startswith("Bearer "):
    raise HTTPException(status_code=401, detail="Missing bearer token")
  supplied = authorization.split(" ", 1)[1].strip()
  if supplied != TOKEN:
    raise HTTPException(status_code=403, detail="Invalid token")


def docker_request(method: str, path: str) -> requests.Response:
  url = f"{DOCKER_PROXY_URL}{path}"
  try:
    return requests.request(method, url, timeout=5)
  except requests.RequestException as exc:
    raise HTTPException(status_code=502, detail=str(exc)) from exc


def container_json(name: str) -> Optional[Dict[str, Any]]:
  resp = docker_request("GET", f"/containers/{name}/json")
  if resp.status_code == 404:
    return None
  if resp.status_code >= 400:
    raise HTTPException(status_code=502, detail=f"docker error: {resp.text}")
  return resp.json()


def container_start(name: str) -> None:
  resp = docker_request("POST", f"/containers/{name}/start")
  if resp.status_code in (204, 304):
    return
  if resp.status_code == 404:
    raise HTTPException(status_code=500, detail=f"container not found: {name}")
  raise HTTPException(status_code=502, detail=f"docker error: {resp.text}")


def container_stop(name: str) -> None:
  resp = docker_request("POST", f"/containers/{name}/stop")
  if resp.status_code in (204, 304):
    return
  if resp.status_code == 404:
    return
  raise HTTPException(status_code=502, detail=f"docker error: {resp.text}")


def container_restart(name: str) -> None:
  resp = docker_request("POST", f"/containers/{name}/restart")
  if resp.status_code in (204, 304):
    return
  if resp.status_code == 404:
    return
  raise HTTPException(status_code=502, detail=f"docker error: {resp.text}")


def active_model() -> Optional[str]:
  try:
    with open(ACTIVE_MODEL_FILE, "r", encoding="utf-8") as handle:
      value = handle.read().strip()
      return value if value in MODELS else None
  except FileNotFoundError:
    return None


def ensure_active_config(model: str) -> None:
  os.makedirs(CONFIG_DIR, exist_ok=True)
  template_name = MODELS[model]["template"]
  template_path = os.path.join(TEMPLATE_DIR, template_name)
  if not os.path.isfile(template_path):
    raise HTTPException(status_code=500, detail=f"template not found: {template_name}")
  with open(template_path, "r", encoding="utf-8") as src:
    content = src.read()
  with open(ACTIVE_CONFIG, "w", encoding="utf-8") as dst:
    dst.write(content)
  with open(ACTIVE_MODEL_FILE, "w", encoding="utf-8") as dst:
    dst.write(model)


def status_payload() -> Dict[str, Any]:
  running_models: List[str] = []
  containers: Dict[str, Any] = {}

  for model_id, meta in MODELS.items():
    info = container_json(meta["container"])
    if not info:
      containers[model_id] = {"exists": False}
      continue
    state = info.get("State", {})
    status = state.get("Status")
    health = state.get("Health", {}).get("Status")
    containers[model_id] = {
      "exists": True,
      "status": status,
      "health": health,
    }
    if status == "running":
      running_models.append(model_id)

  return {
    "running_models": running_models,
    "active_model": active_model(),
    "active_config": ACTIVE_CONFIG if os.path.exists(ACTIVE_CONFIG) else None,
    "containers": containers,
  }


class SwitchRequest(BaseModel):
  model: str


@app.get("/health")
def health() -> Dict[str, str]:
  return {"status": "ok"}


@app.get("/models", dependencies=[Depends(require_token)])
def models() -> Dict[str, Any]:
  return {
    "models": [
      {"id": model_id, "container": meta["container"], "template": meta["template"]}
      for model_id, meta in MODELS.items()
    ]
  }


@app.get("/status", dependencies=[Depends(require_token)])
def status() -> Dict[str, Any]:
  return status_payload()


@app.post("/switch", dependencies=[Depends(require_token)])
def switch(req: SwitchRequest) -> Dict[str, Any]:
  model = req.model
  if model not in MODELS:
    raise HTTPException(status_code=400, detail="unknown model")

  ensure_active_config(model)

  for other_id, meta in MODELS.items():
    if other_id == model:
      continue
    container_stop(meta["container"])

  container_start(MODELS[model]["container"])
  container_restart(LITELLM_CONTAINER)

  return status_payload()


@app.post("/stop", dependencies=[Depends(require_token)])
def stop() -> Dict[str, Any]:
  for meta in MODELS.values():
    container_stop(meta["container"])
  return status_payload()
