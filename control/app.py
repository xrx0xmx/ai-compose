import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

TOKEN = os.getenv("MODEL_SWITCHER_TOKEN", "")
DOCKER_PROXY_URL = os.getenv("DOCKER_PROXY_URL", "http://docker-socket-proxy:2375")
CONFIG_DIR = os.getenv("MODEL_CONFIG_DIR", "/config")
TEMPLATE_DIR = os.getenv("MODEL_TEMPLATE_DIR", "/opt/model-configs")
DEFAULT_MODEL = os.getenv("MODEL_SWITCHER_DEFAULT", "qwen-fast")

DOCKER_TIMEOUT_SECONDS = int(os.getenv("MODEL_SWITCHER_DOCKER_TIMEOUT_SECONDS", "30"))
HEALTH_TIMEOUT_SECONDS = int(os.getenv("MODEL_SWITCHER_HEALTH_TIMEOUT_SECONDS", "480"))
POLL_INTERVAL_SECONDS = float(os.getenv("MODEL_SWITCHER_POLL_INTERVAL_SECONDS", "2"))
LITELLM_MODELS_URL = os.getenv("MODEL_SWITCHER_LITELLM_MODELS_URL", "http://litellm:4000/v1/models")
LITELLM_KEY = os.getenv("MODEL_SWITCHER_LITELLM_KEY", os.getenv("LITELLM_KEY", "cambiaLAclave"))
LITELLM_VERIFY_TIMEOUT_SECONDS = int(os.getenv("MODEL_SWITCHER_LITELLM_VERIFY_TIMEOUT_SECONDS", "90"))

ACTIVE_CONFIG = os.path.join(CONFIG_DIR, "active.yml")
ACTIVE_MODEL_FILE = os.path.join(CONFIG_DIR, "active.model")

MODELS: Dict[str, Dict[str, str]] = {
  "qwen-fast": {
    "container": "vllm-fast",
    "template": "qwen-fast.yml",
    "litellm_model": "qwen-fast",
  },
  "qwen-quality": {
    "container": "vllm-quality",
    "template": "qwen-quality.yml",
    "litellm_model": "qwen-quality",
  },
  "deepseek": {
    "container": "vllm-deepseek",
    "template": "deepseek.yml",
    "litellm_model": "deepseek-r1",
  },
  "qwen-max": {
    "container": "vllm-qwen32b",
    "template": "qwen-max.yml",
    "litellm_model": "qwen-max",
  },
}

LITELLM_CONTAINER = "litellm"

app = FastAPI(title="AI Model Switcher", version="2.1.0")

SWITCH_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
LAST_ERROR: Optional[str] = None
LAST_SWITCH_AT: Optional[str] = None


class SwitchRequest(BaseModel):
  model: str


def utc_now() -> str:
  return datetime.now(timezone.utc).isoformat()


def require_token(authorization: Optional[str] = Header(default=None)) -> None:
  if not TOKEN:
    raise HTTPException(status_code=500, detail="MODEL_SWITCHER_TOKEN not set")
  if not authorization or not authorization.startswith("Bearer "):
    raise HTTPException(status_code=401, detail="Missing bearer token")
  supplied = authorization.split(" ", 1)[1].strip()
  if supplied != TOKEN:
    raise HTTPException(status_code=403, detail="Invalid token")


def set_runtime_state(*, last_error: Optional[str] = None, clear_error: bool = False) -> None:
  global LAST_ERROR
  global LAST_SWITCH_AT
  with STATE_LOCK:
    if clear_error:
      LAST_ERROR = None
    elif last_error is not None:
      LAST_ERROR = last_error
    LAST_SWITCH_AT = utc_now()


def runtime_state() -> Dict[str, Optional[str]]:
  with STATE_LOCK:
    return {
      "last_error": LAST_ERROR,
      "last_switch_at": LAST_SWITCH_AT,
    }


def docker_request(method: str, path: str, *, timeout: Optional[float] = None) -> requests.Response:
  url = f"{DOCKER_PROXY_URL}{path}"
  effective_timeout = timeout if timeout is not None else DOCKER_TIMEOUT_SECONDS
  try:
    return requests.request(method, url, timeout=effective_timeout)
  except requests.RequestException as exc:
    raise RuntimeError(str(exc)) from exc


def container_json(name: str) -> Optional[Dict[str, Any]]:
  resp = docker_request("GET", f"/containers/{name}/json")
  if resp.status_code == 404:
    return None
  if resp.status_code >= 400:
    raise RuntimeError(f"docker error: {resp.text}")
  return resp.json()


def container_start(name: str) -> None:
  resp = docker_request("POST", f"/containers/{name}/start")
  if resp.status_code in (204, 304):
    return
  if resp.status_code == 404:
    raise RuntimeError(f"container not found: {name}")
  raise RuntimeError(f"docker error: {resp.text}")


def container_stop(name: str) -> None:
  resp = docker_request("POST", f"/containers/{name}/stop")
  if resp.status_code in (204, 304):
    return
  if resp.status_code == 404:
    return
  raise RuntimeError(f"docker error: {resp.text}")


def read_optional_text(path: str) -> Optional[str]:
  try:
    with open(path, "r", encoding="utf-8") as handle:
      return handle.read()
  except FileNotFoundError:
    return None


def write_text(path: str, content: str) -> None:
  with open(path, "w", encoding="utf-8") as handle:
    handle.write(content)


def remove_file(path: str) -> None:
  try:
    os.remove(path)
  except FileNotFoundError:
    return


def active_model() -> Optional[str]:
  value = read_optional_text(ACTIVE_MODEL_FILE)
  if value is None:
    return None
  current = value.strip()
  return current if current in MODELS else None


def ensure_active_config(model: str) -> None:
  os.makedirs(CONFIG_DIR, exist_ok=True)
  template_name = MODELS[model]["template"]
  template_path = os.path.join(TEMPLATE_DIR, template_name)
  if not os.path.isfile(template_path):
    raise RuntimeError(f"template not found: {template_name}")

  with open(template_path, "r", encoding="utf-8") as src:
    content = src.read()

  write_text(ACTIVE_CONFIG, content)
  write_text(ACTIVE_MODEL_FILE, model)


def restore_active_files(previous_config: Optional[str], previous_model: Optional[str]) -> None:
  os.makedirs(CONFIG_DIR, exist_ok=True)

  if previous_config is None:
    remove_file(ACTIVE_CONFIG)
  else:
    write_text(ACTIVE_CONFIG, previous_config)

  if previous_model is None:
    remove_file(ACTIVE_MODEL_FILE)
  else:
    write_text(ACTIVE_MODEL_FILE, previous_model)


def state_snapshot(name: str) -> Dict[str, Any]:
  info = container_json(name)
  if info is None:
    return {
      "exists": False,
      "status": None,
      "health": None,
    }

  state = info.get("State", {})
  status = state.get("Status")
  health = None
  health_info = state.get("Health")
  if isinstance(health_info, dict):
    health = health_info.get("Status")

  return {
    "exists": True,
    "status": status,
    "health": health,
  }


def wait_container_ready(name: str, timeout_seconds: int) -> None:
  deadline = time.monotonic() + timeout_seconds
  last_status = "unknown"
  last_health = "unknown"

  while time.monotonic() < deadline:
    info = container_json(name)
    if info is None:
      raise RuntimeError(f"container not found while waiting: {name}")

    state = info.get("State", {})
    status = state.get("Status")
    health_info = state.get("Health") or {}
    health = health_info.get("Status") if isinstance(health_info, dict) else None

    if status:
      last_status = status
    if health:
      last_health = health

    if health is not None:
      if health == "healthy":
        return
      if health == "unhealthy":
        raise RuntimeError(f"container unhealthy: {name}")
    elif status == "running":
      return

    if status in {"exited", "dead"}:
      raise RuntimeError(f"container not running: {name} ({status})")

    time.sleep(POLL_INTERVAL_SECONDS)

  raise RuntimeError(
    f"timeout waiting healthy for {name} (status={last_status}, health={last_health})"
  )


def wait_litellm_model(model: str, timeout_seconds: int) -> None:
  headers = {"Authorization": f"Bearer {LITELLM_KEY}"}
  deadline = time.monotonic() + timeout_seconds

  while time.monotonic() < deadline:
    try:
      response = requests.get(
        LITELLM_MODELS_URL,
        headers=headers,
        timeout=DOCKER_TIMEOUT_SECONDS,
      )
    except requests.RequestException:
      time.sleep(POLL_INTERVAL_SECONDS)
      continue

    if response.status_code in {401, 403}:
      raise RuntimeError("litellm auth failed while verifying model list")

    if response.status_code == 200:
      payload = response.json()
      data = payload.get("data", []) if isinstance(payload, dict) else []
      model_ids = {str(item.get("id")) for item in data if isinstance(item, dict)}
      if model in model_ids:
        return

    time.sleep(POLL_INTERVAL_SECONDS)

  raise RuntimeError(f"litellm did not expose model '{model}' in time")


def running_models_from_status(status: Dict[str, Any]) -> List[str]:
  value = status.get("running_models", [])
  if isinstance(value, list):
    return [str(item) for item in value]
  return []


def status_payload() -> Dict[str, Any]:
  running_models: List[str] = []
  containers: Dict[str, Any] = {}

  for model_id, meta in MODELS.items():
    try:
      snapshot = state_snapshot(meta["container"])
    except RuntimeError as exc:
      containers[model_id] = {
        "exists": False,
        "status": None,
        "health": None,
        "error": str(exc),
      }
      continue

    containers[model_id] = snapshot
    if snapshot.get("status") == "running":
      running_models.append(model_id)

  litellm_info: Dict[str, Any]
  try:
    litellm_info = state_snapshot(LITELLM_CONTAINER)
  except RuntimeError as exc:
    litellm_info = {
      "exists": False,
      "status": None,
      "health": None,
      "error": str(exc),
    }

  runtime = runtime_state()

  return {
    "running_models": running_models,
    "active_model": active_model(),
    "active_config": ACTIVE_CONFIG if os.path.exists(ACTIVE_CONFIG) else None,
    "containers": containers,
    "litellm": litellm_info,
    "switch_in_progress": SWITCH_LOCK.locked(),
    "last_error": runtime["last_error"],
    "last_switch_at": runtime["last_switch_at"],
  }


def add_step(steps: List[Dict[str, Any]], step: str, ok: bool, detail: str) -> None:
  steps.append(
    {
      "step": step,
      "at": utc_now(),
      "ok": ok,
      "detail": detail,
    }
  )


def switch_response(
  *,
  status: str,
  from_model: Optional[str],
  to_model: str,
  steps: List[Dict[str, Any]],
  started: float,
  error: Optional[str] = None,
) -> Dict[str, Any]:
  payload = status_payload()
  payload.update(
    {
      "status": status,
      "from_model": from_model,
      "to_model": to_model,
      "steps": steps,
      "duration_ms": int((time.monotonic() - started) * 1000),
      "error": error,
    }
  )
  return payload


@app.get("/health")
def health() -> Dict[str, str]:
  return {"status": "ok"}


@app.get("/healthz/ready")
def ready() -> Dict[str, str]:
  payload = status_payload()
  running = running_models_from_status(payload)
  active = payload.get("active_model")

  if len(running) != 1:
    raise HTTPException(status_code=503, detail="expected exactly one running model")
  if not active:
    raise HTTPException(status_code=503, detail="no active model configured")
  if running[0] != active:
    raise HTTPException(status_code=503, detail="active model does not match running model")

  return {"status": "ready", "active_model": active}


@app.get("/models", dependencies=[Depends(require_token)])
def models() -> Dict[str, Any]:
  return {
    "models": [
      {
        "id": model_id,
        "container": meta["container"],
        "template": meta["template"],
        "litellm_model": meta["litellm_model"],
      }
      for model_id, meta in MODELS.items()
    ]
  }


@app.get("/status", dependencies=[Depends(require_token)])
def status() -> Dict[str, Any]:
  return status_payload()


@app.post("/switch", dependencies=[Depends(require_token)])
def switch(req: SwitchRequest) -> Dict[str, Any]:
  model = req.model.strip()
  if model not in MODELS:
    raise HTTPException(status_code=400, detail="unknown model")

  if not SWITCH_LOCK.acquire(blocking=False):
    raise HTTPException(status_code=409, detail="switch_in_progress")

  started = time.monotonic()
  steps: List[Dict[str, Any]] = []

  previous_config = read_optional_text(ACTIVE_CONFIG)
  previous_model_value = read_optional_text(ACTIVE_MODEL_FILE)

  before = status_payload()
  running_before = running_models_from_status(before)
  from_model = running_before[0] if running_before else before.get("active_model")
  disruptive_started = False

  try:
    target_container = MODELS[model]["container"]
    if container_json(target_container) is None:
      raise HTTPException(
        status_code=412,
        detail=(
          f"target container is not created: {target_container}. "
          "Run make prod-bootstrap-models first."
        ),
      )
    add_step(steps, "preflight", True, f"target container exists: {target_container}")

    if from_model == model and len(running_before) == 1:
      add_step(steps, "noop", True, f"model '{model}' is already active")
      set_runtime_state(clear_error=True)
      return switch_response(
        status="success",
        from_model=from_model,
        to_model=model,
        steps=steps,
        started=started,
        error=None,
      )

    container_stop(LITELLM_CONTAINER)
    disruptive_started = True
    add_step(steps, "stop_litellm", True, "litellm stopped")

    for meta in MODELS.values():
      container_stop(meta["container"])
    add_step(steps, "stop_models", True, "all vllm containers stopped")

    container_start(target_container)
    add_step(steps, "start_target", True, f"started {target_container}")

    wait_container_ready(target_container, HEALTH_TIMEOUT_SECONDS)
    add_step(steps, "wait_target", True, f"{target_container} is ready")

    ensure_active_config(model)
    add_step(steps, "activate_config", True, f"active config set to {model}")

    container_start(LITELLM_CONTAINER)
    add_step(steps, "start_litellm", True, "litellm started")

    litellm_model_name = MODELS[model]["litellm_model"]
    wait_litellm_model(litellm_model_name, LITELLM_VERIFY_TIMEOUT_SECONDS)
    add_step(steps, "verify_litellm", True, f"litellm exposes model '{litellm_model_name}'")

    set_runtime_state(clear_error=True)
    return switch_response(
      status="success",
      from_model=from_model,
      to_model=model,
      steps=steps,
      started=started,
      error=None,
    )

  except HTTPException:
    raise

  except Exception as exc:
    error_detail = str(exc)
    add_step(steps, "switch_error", False, error_detail)

    final_status = "failed"

    if disruptive_started and from_model and from_model in MODELS and from_model != model:
      try:
        restore_active_files(previous_config, previous_model_value)
        add_step(steps, "rollback_restore_config", True, "active config restored")

        for meta in MODELS.values():
          container_stop(meta["container"])
        add_step(steps, "rollback_stop_models", True, "all vllm containers stopped")

        rollback_container = MODELS[from_model]["container"]
        if container_json(rollback_container) is None:
          raise RuntimeError(f"rollback container missing: {rollback_container}")

        container_start(rollback_container)
        wait_container_ready(rollback_container, HEALTH_TIMEOUT_SECONDS)
        add_step(steps, "rollback_start_previous", True, f"restored {from_model}")

        container_start(LITELLM_CONTAINER)
        rollback_litellm_model = MODELS[from_model]["litellm_model"]
        wait_litellm_model(rollback_litellm_model, LITELLM_VERIFY_TIMEOUT_SECONDS)
        add_step(steps, "rollback_litellm", True, "litellm restored")

        final_status = "rolled_back"
      except Exception as rollback_exc:
        rollback_error = str(rollback_exc)
        add_step(steps, "rollback_error", False, rollback_error)
        error_detail = f"{error_detail}; rollback failed: {rollback_error}"
    elif disruptive_started:
      try:
        restore_active_files(previous_config, previous_model_value)
        add_step(steps, "restore_config", True, "active config restored")
      except Exception as restore_exc:
        add_step(steps, "restore_config", False, str(restore_exc))
      try:
        container_start(LITELLM_CONTAINER)
        add_step(steps, "restore_litellm", True, "litellm restarted")
      except Exception as litellm_exc:
        add_step(steps, "restore_litellm", False, str(litellm_exc))

    set_runtime_state(last_error=error_detail)
    return switch_response(
      status=final_status,
      from_model=from_model,
      to_model=model,
      steps=steps,
      started=started,
      error=error_detail,
    )

  finally:
    SWITCH_LOCK.release()


@app.post("/stop", dependencies=[Depends(require_token)])
def stop() -> Dict[str, Any]:
  for meta in MODELS.values():
    container_stop(meta["container"])
  return status_payload()
