import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
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
ACTIVE_MODE_FILE = os.path.join(CONFIG_DIR, "active.mode")
ACTIVE_COMFY_LEASE_FILE = os.path.join(CONFIG_DIR, "active.mode.lease_until")

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
COMFY_CONTAINER = os.getenv("MODEL_SWITCHER_COMFY_CONTAINER", "comfyui")
MODE_LLM = "llm"
MODE_COMFY = "comfy"
VALID_MODES = {MODE_LLM, MODE_COMFY}
DEFAULT_COMFY_TTL_MINUTES = int(os.getenv("MODEL_SWITCHER_COMFY_DEFAULT_TTL_MINUTES", "45"))
MAX_COMFY_TTL_MINUTES = int(os.getenv("MODEL_SWITCHER_COMFY_MAX_TTL_MINUTES", "90"))
MODE_MONITOR_POLL_SECONDS = int(os.getenv("MODEL_SWITCHER_MODE_POLL_INTERVAL_SECONDS", "5"))
FINAL_SWITCH_STATES = {"success", "failed", "rolled_back"}
UNSET = object()

app = FastAPI(title="AI Model Switcher", version="2.3.0")

SWITCH_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
SWITCH_STATE_LOCK = threading.Lock()
LAST_ERROR: Optional[str] = None
LAST_SWITCH_AT: Optional[str] = None
SWITCH_ID_SEQ = 0
CURRENT_SWITCH: Optional[Dict[str, Any]] = None
MODE_MONITOR_THREAD: Optional[threading.Thread] = None
MODE_MONITOR_LOCK = threading.Lock()


class SwitchRequest(BaseModel):
  model: str
  wait_for_ready: bool = True


class ModeSwitchRequest(BaseModel):
  mode: str
  model: Optional[str] = None
  ttl_minutes: Optional[int] = None
  wait_for_ready: bool = True


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


def _elapsed_ms(switch_state: Dict[str, Any]) -> int:
  started = switch_state.get("_started_monotonic")
  if isinstance(started, (int, float)):
    return int((time.monotonic() - started) * 1000)
  duration = switch_state.get("duration_ms")
  return int(duration) if isinstance(duration, int) else 0


def _public_switch_state(switch_state: Dict[str, Any]) -> Dict[str, Any]:
  duration_ms = switch_state.get("duration_ms")
  if not isinstance(duration_ms, int):
    duration_ms = 0
  if switch_state.get("finished_at") is None:
    duration_ms = _elapsed_ms(switch_state)

  return {
    "id": switch_state.get("id"),
    "state": switch_state.get("state"),
    "from_model": switch_state.get("from_model"),
    "to_model": switch_state.get("to_model"),
    "current_step": switch_state.get("current_step"),
    "state_text": switch_state.get("state_text"),
    "started_at": switch_state.get("started_at"),
    "updated_at": switch_state.get("updated_at"),
    "finished_at": switch_state.get("finished_at"),
    "duration_ms": duration_ms,
    "error": switch_state.get("error"),
    "steps": [dict(item) for item in switch_state.get("steps", [])],
    "ready": bool(switch_state.get("ready", False)),
  }


def current_switch_snapshot() -> Optional[Dict[str, Any]]:
  with SWITCH_STATE_LOCK:
    if CURRENT_SWITCH is None:
      return None
    return _public_switch_state(CURRENT_SWITCH)


def create_switch_state(
  *,
  to_model: str,
  state: str,
  state_text: str,
  from_model: Optional[str] = None,
  current_step: Optional[str] = None,
) -> int:
  global SWITCH_ID_SEQ
  global CURRENT_SWITCH

  now = utc_now()
  with SWITCH_STATE_LOCK:
    SWITCH_ID_SEQ += 1
    switch_id = SWITCH_ID_SEQ
    CURRENT_SWITCH = {
      "id": switch_id,
      "state": state,
      "from_model": from_model,
      "to_model": to_model,
      "current_step": current_step,
      "state_text": state_text,
      "started_at": now,
      "updated_at": now,
      "finished_at": None,
      "duration_ms": 0,
      "error": None,
      "steps": [],
      "ready": False,
      "_started_monotonic": time.monotonic(),
    }

  return switch_id


def update_switch_state(
  switch_id: int,
  *,
  state: Any = UNSET,
  from_model: Any = UNSET,
  to_model: Any = UNSET,
  current_step: Any = UNSET,
  state_text: Any = UNSET,
  error: Any = UNSET,
  ready: Any = UNSET,
  steps: Optional[List[Dict[str, Any]]] = None,
) -> None:
  with SWITCH_STATE_LOCK:
    if CURRENT_SWITCH is None or CURRENT_SWITCH.get("id") != switch_id:
      return

    now = utc_now()

    if state is not UNSET:
      CURRENT_SWITCH["state"] = state
    if from_model is not UNSET:
      CURRENT_SWITCH["from_model"] = from_model
    if to_model is not UNSET:
      CURRENT_SWITCH["to_model"] = to_model
    if current_step is not UNSET:
      CURRENT_SWITCH["current_step"] = current_step
    if state_text is not UNSET:
      CURRENT_SWITCH["state_text"] = state_text
    if error is not UNSET:
      CURRENT_SWITCH["error"] = error
    if ready is not UNSET:
      CURRENT_SWITCH["ready"] = bool(ready)
    if steps is not None:
      CURRENT_SWITCH["steps"] = [dict(item) for item in steps]

    if CURRENT_SWITCH.get("finished_at") is None and CURRENT_SWITCH.get("state") in FINAL_SWITCH_STATES:
      CURRENT_SWITCH["finished_at"] = now
      CURRENT_SWITCH["duration_ms"] = _elapsed_ms(CURRENT_SWITCH)
    elif CURRENT_SWITCH.get("finished_at") is None:
      CURRENT_SWITCH["duration_ms"] = _elapsed_ms(CURRENT_SWITCH)

    CURRENT_SWITCH["updated_at"] = now


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


def parse_utc_iso(value: str) -> Optional[datetime]:
  normalized = value.strip()
  if not normalized:
    return None
  try:
    parsed = datetime.fromisoformat(normalized)
  except ValueError:
    return None
  if parsed.tzinfo is None:
    return parsed.replace(tzinfo=timezone.utc)
  return parsed.astimezone(timezone.utc)


def active_mode() -> str:
  raw = read_optional_text(ACTIVE_MODE_FILE)
  if raw is None:
    return MODE_LLM
  mode = raw.strip().lower()
  return mode if mode in VALID_MODES else MODE_LLM


def set_active_mode(mode: str) -> None:
  if mode not in VALID_MODES:
    raise RuntimeError(f"invalid mode: {mode}")
  os.makedirs(CONFIG_DIR, exist_ok=True)
  write_text(ACTIVE_MODE_FILE, f"{mode}\n")
  if mode != MODE_COMFY:
    remove_file(ACTIVE_COMFY_LEASE_FILE)


def comfy_lease_until() -> Optional[datetime]:
  raw = read_optional_text(ACTIVE_COMFY_LEASE_FILE)
  if raw is None:
    return None
  return parse_utc_iso(raw)


def set_comfy_lease(ttl_minutes: int) -> datetime:
  deadline = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
  write_text(ACTIVE_COMFY_LEASE_FILE, f"{deadline.isoformat()}\n")
  return deadline


def clear_comfy_lease() -> None:
  remove_file(ACTIVE_COMFY_LEASE_FILE)


def comfy_lease_status() -> Dict[str, Any]:
  lease_until = comfy_lease_until()
  if lease_until is None:
    return {
      "expires_at": None,
      "remaining_seconds": None,
      "expired": False,
    }

  now = datetime.now(timezone.utc)
  remaining = int((lease_until - now).total_seconds())
  return {
    "expires_at": lease_until.isoformat(),
    "remaining_seconds": max(remaining, 0),
    "expired": remaining <= 0,
  }


def normalize_comfy_ttl(ttl_minutes: Optional[int]) -> int:
  if ttl_minutes is None:
    return DEFAULT_COMFY_TTL_MINUTES
  if ttl_minutes <= 0:
    raise HTTPException(status_code=400, detail="ttl_minutes must be greater than 0")
  if ttl_minutes > MAX_COMFY_TTL_MINUTES:
    raise HTTPException(
      status_code=400,
      detail=f"ttl_minutes must be <= {MAX_COMFY_TTL_MINUTES}",
    )
  return ttl_minutes


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


def stop_all_model_containers() -> None:
  for meta in MODELS.values():
    container_stop(meta["container"])


def ensure_container_created(name: str, bootstrap_target: str) -> None:
  if container_json(name) is None:
    raise HTTPException(
      status_code=412,
      detail=(
        f"target container is not created: {name}. "
        f"Run make {bootstrap_target} first."
      ),
    )


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

  comfy_info: Dict[str, Any]
  try:
    comfy_info = state_snapshot(COMFY_CONTAINER)
  except RuntimeError as exc:
    comfy_info = {
      "exists": False,
      "status": None,
      "health": None,
      "error": str(exc),
    }

  runtime = runtime_state()
  mode = active_mode()
  lease = comfy_lease_status()

  return {
    "running_models": running_models,
    "active_model": active_model(),
    "active_mode": mode,
    "mode": {
      "active": mode,
      "default": MODE_LLM,
      "lease": lease,
    },
    "active_config": ACTIVE_CONFIG if os.path.exists(ACTIVE_CONFIG) else None,
    "active_mode_file": ACTIVE_MODE_FILE if os.path.exists(ACTIVE_MODE_FILE) else None,
    "active_comfy_lease_file": ACTIVE_COMFY_LEASE_FILE if os.path.exists(ACTIVE_COMFY_LEASE_FILE) else None,
    "containers": containers,
    "litellm": litellm_info,
    "comfyui": comfy_info,
    "switch_in_progress": SWITCH_LOCK.locked(),
    "last_error": runtime["last_error"],
    "last_switch_at": runtime["last_switch_at"],
    "switch": current_switch_snapshot(),
  }


def add_step(
  steps: List[Dict[str, Any]],
  step: str,
  ok: bool,
  detail: str,
  *,
  switch_id: Optional[int] = None,
  state: Optional[str] = None,
  state_text: Optional[str] = None,
  ready: Optional[bool] = None,
  error: Any = UNSET,
) -> None:
  entry = {
    "step": step,
    "at": utc_now(),
    "ok": ok,
    "detail": detail,
  }
  steps.append(entry)

  if switch_id is None:
    return

  switch_error = error
  if switch_error is UNSET and not ok:
    switch_error = detail

  update_kwargs: Dict[str, Any] = {
    "current_step": step,
    "state_text": state_text if state_text is not None else detail,
    "steps": steps,
  }
  if state is not None:
    update_kwargs["state"] = state
  if ready is not None:
    update_kwargs["ready"] = ready
  if switch_error is not UNSET:
    update_kwargs["error"] = switch_error

  update_switch_state(switch_id, **update_kwargs)


def switch_response(
  *,
  switch_id: int,
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
      "switch_id": switch_id,
      "status": status,
      "from_model": from_model,
      "to_model": to_model,
      "steps": steps,
      "duration_ms": int((time.monotonic() - started) * 1000),
      "error": error,
    }
  )
  return payload


def run_switch_pipeline(model: str, switch_id: int, *, raise_http_errors: bool) -> Dict[str, Any]:
  started = time.monotonic()
  steps: List[Dict[str, Any]] = []

  previous_config = read_optional_text(ACTIVE_CONFIG)
  previous_model_value = read_optional_text(ACTIVE_MODEL_FILE)

  before = status_payload()
  running_before = running_models_from_status(before)
  from_model = running_before[0] if running_before else before.get("active_model")
  disruptive_started = False

  update_switch_state(
    switch_id,
    state="running",
    from_model=from_model,
    to_model=model,
    current_step="preflight",
    state_text=f"Iniciando cambio a {model}",
    ready=False,
    error=None,
    steps=steps,
  )

  try:
    target_container = MODELS[model]["container"]
    ensure_container_created(target_container, "prod-bootstrap-models")
    add_step(
      steps,
      "preflight",
      True,
      f"target container exists: {target_container}",
      switch_id=switch_id,
      state="running",
      state_text=f"Preflight OK para {target_container}",
    )

    update_switch_state(
      switch_id,
      current_step="stop_comfy",
      state_text="Deteniendo ComfyUI",
      state="running",
    )
    container_stop(COMFY_CONTAINER)
    add_step(
      steps,
      "stop_comfy",
      True,
      "comfyui stopped",
      switch_id=switch_id,
      state="running",
      state_text="ComfyUI detenido",
    )

    if from_model == model and len(running_before) == 1:
      add_step(
        steps,
        "noop",
        True,
        f"model '{model}' is already active",
        switch_id=switch_id,
        state="success",
        state_text="Modelo disponible",
        ready=True,
        error=None,
      )
      set_runtime_state(clear_error=True)
      set_active_mode(MODE_LLM)
      clear_comfy_lease()
      update_switch_state(
        switch_id,
        state="success",
        current_step="complete",
        state_text="Modelo disponible",
        ready=True,
        error=None,
        steps=steps,
      )
      return switch_response(
        switch_id=switch_id,
        status="success",
        from_model=from_model,
        to_model=model,
        steps=steps,
        started=started,
        error=None,
      )

    update_switch_state(
      switch_id,
      current_step="stop_litellm",
      state_text="Deteniendo LiteLLM",
      state="running",
    )
    container_stop(LITELLM_CONTAINER)
    disruptive_started = True
    add_step(
      steps,
      "stop_litellm",
      True,
      "litellm stopped",
      switch_id=switch_id,
      state="running",
      state_text="LiteLLM detenido",
    )

    update_switch_state(
      switch_id,
      current_step="stop_models",
      state_text="Deteniendo contenedores de modelo",
      state="running",
    )
    stop_all_model_containers()
    add_step(
      steps,
      "stop_models",
      True,
      "all vllm containers stopped",
      switch_id=switch_id,
      state="running",
      state_text="Contenedores de modelo detenidos",
    )

    update_switch_state(
      switch_id,
      current_step="start_target",
      state_text=f"Iniciando {target_container}",
      state="running",
    )
    container_start(target_container)
    add_step(
      steps,
      "start_target",
      True,
      f"started {target_container}",
      switch_id=switch_id,
      state="running",
      state_text=f"{target_container} iniciado",
    )

    update_switch_state(
      switch_id,
      current_step="wait_target",
      state_text=f"Esperando health de {target_container}",
      state="running",
    )
    wait_container_ready(target_container, HEALTH_TIMEOUT_SECONDS)
    add_step(
      steps,
      "wait_target",
      True,
      f"{target_container} is ready",
      switch_id=switch_id,
      state="running",
      state_text=f"{target_container} listo",
    )

    ensure_active_config(model)
    add_step(
      steps,
      "activate_config",
      True,
      f"active config set to {model}",
      switch_id=switch_id,
      state="running",
      state_text=f"Configuracion activa actualizada a {model}",
    )

    update_switch_state(
      switch_id,
      current_step="start_litellm",
      state_text="Iniciando LiteLLM",
      state="running",
    )
    container_start(LITELLM_CONTAINER)
    add_step(
      steps,
      "start_litellm",
      True,
      "litellm started",
      switch_id=switch_id,
      state="running",
      state_text="LiteLLM iniciado",
    )

    litellm_model_name = MODELS[model]["litellm_model"]
    update_switch_state(
      switch_id,
      current_step="verify_litellm",
      state_text=f"Verificando modelo '{litellm_model_name}' en LiteLLM",
      state="running",
    )
    wait_litellm_model(litellm_model_name, LITELLM_VERIFY_TIMEOUT_SECONDS)
    add_step(
      steps,
      "verify_litellm",
      True,
      f"litellm exposes model '{litellm_model_name}'",
      switch_id=switch_id,
      state="success",
      state_text="Modelo disponible",
      ready=True,
      error=None,
    )

    set_runtime_state(clear_error=True)
    set_active_mode(MODE_LLM)
    clear_comfy_lease()
    update_switch_state(
      switch_id,
      state="success",
      from_model=from_model,
      to_model=model,
      current_step="complete",
      state_text="Modelo disponible",
      ready=True,
      error=None,
      steps=steps,
    )
    return switch_response(
      switch_id=switch_id,
      status="success",
      from_model=from_model,
      to_model=model,
      steps=steps,
      started=started,
      error=None,
    )

  except HTTPException as exc:
    if raise_http_errors:
      update_switch_state(
        switch_id,
        state="failed",
        from_model=from_model,
        to_model=model,
        current_step="switch_error",
        state_text=f"Error: {exc.detail}",
        ready=False,
        error=str(exc.detail),
        steps=steps,
      )
      raise

    error_detail = str(exc.detail)
    add_step(
      steps,
      "switch_error",
      False,
      error_detail,
      switch_id=switch_id,
      state="failed",
      state_text="Error en cambio de modelo",
      ready=False,
      error=error_detail,
    )
    set_runtime_state(last_error=error_detail)
    update_switch_state(
      switch_id,
      state="failed",
      from_model=from_model,
      to_model=model,
      current_step="complete",
      state_text="Error en cambio de modelo",
      ready=False,
      error=error_detail,
      steps=steps,
    )
    return switch_response(
      switch_id=switch_id,
      status="failed",
      from_model=from_model,
      to_model=model,
      steps=steps,
      started=started,
      error=error_detail,
    )

  except Exception as exc:
    error_detail = str(exc)
    add_step(
      steps,
      "switch_error",
      False,
      error_detail,
      switch_id=switch_id,
      state="running",
      state_text="Error detectado, iniciando rollback",
      ready=False,
      error=error_detail,
    )

    final_status = "failed"

    if disruptive_started and from_model and from_model in MODELS and from_model != model:
      try:
        update_switch_state(
          switch_id,
          current_step="rollback_restore_config",
          state_text="Rollback: restaurando configuracion activa",
          state="running",
        )
        restore_active_files(previous_config, previous_model_value)
        add_step(
          steps,
          "rollback_restore_config",
          True,
          "active config restored",
          switch_id=switch_id,
          state="running",
          state_text="Rollback: configuracion restaurada",
        )

        update_switch_state(
          switch_id,
          current_step="rollback_stop_models",
          state_text="Rollback: deteniendo modelos",
          state="running",
        )
        stop_all_model_containers()
        add_step(
          steps,
          "rollback_stop_models",
          True,
          "all vllm containers stopped",
          switch_id=switch_id,
          state="running",
          state_text="Rollback: modelos detenidos",
        )

        rollback_container = MODELS[from_model]["container"]
        if container_json(rollback_container) is None:
          raise RuntimeError(f"rollback container missing: {rollback_container}")

        update_switch_state(
          switch_id,
          current_step="rollback_start_previous",
          state_text=f"Rollback: iniciando {rollback_container}",
          state="running",
        )
        container_start(rollback_container)
        wait_container_ready(rollback_container, HEALTH_TIMEOUT_SECONDS)
        add_step(
          steps,
          "rollback_start_previous",
          True,
          f"restored {from_model}",
          switch_id=switch_id,
          state="running",
          state_text=f"Rollback: {from_model} restaurado",
        )

        update_switch_state(
          switch_id,
          current_step="rollback_litellm",
          state_text="Rollback: iniciando LiteLLM",
          state="running",
        )
        container_start(LITELLM_CONTAINER)
        rollback_litellm_model = MODELS[from_model]["litellm_model"]
        wait_litellm_model(rollback_litellm_model, LITELLM_VERIFY_TIMEOUT_SECONDS)
        add_step(
          steps,
          "rollback_litellm",
          True,
          "litellm restored",
          switch_id=switch_id,
          state="running",
          state_text="Rollback completado",
        )
        set_active_mode(MODE_LLM)
        clear_comfy_lease()

        final_status = "rolled_back"
      except Exception as rollback_exc:
        rollback_error = str(rollback_exc)
        add_step(
          steps,
          "rollback_error",
          False,
          rollback_error,
          switch_id=switch_id,
          state="failed",
          state_text="Error durante rollback",
          ready=False,
          error=rollback_error,
        )
        error_detail = f"{error_detail}; rollback failed: {rollback_error}"

    elif disruptive_started:
      try:
        update_switch_state(
          switch_id,
          current_step="restore_config",
          state_text="Restaurando configuracion activa",
          state="running",
        )
        restore_active_files(previous_config, previous_model_value)
        add_step(
          steps,
          "restore_config",
          True,
          "active config restored",
          switch_id=switch_id,
          state="running",
          state_text="Configuracion activa restaurada",
        )
      except Exception as restore_exc:
        add_step(
          steps,
          "restore_config",
          False,
          str(restore_exc),
          switch_id=switch_id,
          state="running",
          state_text="Error restaurando configuracion",
          error=str(restore_exc),
        )
      try:
        update_switch_state(
          switch_id,
          current_step="restore_litellm",
          state_text="Reiniciando LiteLLM",
          state="running",
        )
        container_start(LITELLM_CONTAINER)
        add_step(
          steps,
          "restore_litellm",
          True,
          "litellm restarted",
          switch_id=switch_id,
          state="running",
          state_text="LiteLLM reiniciado",
        )
        set_active_mode(MODE_LLM)
        clear_comfy_lease()
      except Exception as litellm_exc:
        add_step(
          steps,
          "restore_litellm",
          False,
          str(litellm_exc),
          switch_id=switch_id,
          state="failed",
          state_text="Error al reiniciar LiteLLM",
          error=str(litellm_exc),
        )

    set_runtime_state(last_error=error_detail)

    final_text = "Rollback completado; modelo objetivo no disponible"
    if final_status == "failed":
      final_text = "Error en cambio de modelo"

    update_switch_state(
      switch_id,
      state=final_status,
      from_model=from_model,
      to_model=model,
      current_step="complete",
      state_text=final_text,
      ready=False,
      error=error_detail,
      steps=steps,
    )
    return switch_response(
      switch_id=switch_id,
      status=final_status,
      from_model=from_model,
      to_model=model,
      steps=steps,
      started=started,
      error=error_detail,
    )


def run_switch_pipeline_async(model: str, switch_id: int) -> None:
  try:
    run_switch_pipeline(model, switch_id, raise_http_errors=False)
  except Exception as exc:  # pragma: no cover - guardrail
    error_detail = f"unexpected async switch error: {exc}"
    set_runtime_state(last_error=error_detail)
    update_switch_state(
      switch_id,
      state="failed",
      current_step="switch_error",
      state_text="Error inesperado en switch async",
      ready=False,
      error=error_detail,
    )
  finally:
    SWITCH_LOCK.release()


def default_llm_model() -> str:
  if DEFAULT_MODEL in MODELS:
    return DEFAULT_MODEL
  return next(iter(MODELS.keys()))


def resolve_llm_model(requested_model: Optional[str]) -> str:
  if requested_model is not None:
    candidate = requested_model.strip()
    if candidate not in MODELS:
      raise HTTPException(status_code=400, detail="unknown model")
    return candidate
  current = active_model()
  if current and current in MODELS:
    return current
  return default_llm_model()


def run_mode_switch_pipeline(
  req: ModeSwitchRequest,
  switch_id: int,
  *,
  raise_http_errors: bool,
  source: str = "api",
) -> Dict[str, Any]:
  mode = req.mode.strip().lower()
  if mode not in VALID_MODES:
    raise HTTPException(status_code=400, detail="unknown mode")

  if mode == MODE_LLM:
    if req.ttl_minutes is not None:
      raise HTTPException(status_code=400, detail="ttl_minutes is only valid for mode='comfy'")
    target_model = resolve_llm_model(req.model)
    update_switch_state(
      switch_id,
      state_text=f"Cambiando a modo llm ({target_model})",
      to_model=target_model,
      current_step="mode_llm",
    )
    return run_switch_pipeline(target_model, switch_id, raise_http_errors=raise_http_errors)

  if req.model is not None and req.model.strip():
    raise HTTPException(status_code=400, detail="model is only valid for mode='llm'")

  ttl_minutes = normalize_comfy_ttl(req.ttl_minutes)
  started = time.monotonic()
  steps: List[Dict[str, Any]] = []

  before = status_payload()
  running_before = running_models_from_status(before)
  from_model = running_before[0] if running_before else before.get("active_model")
  disruptive_started = False

  update_switch_state(
    switch_id,
    state="running",
    from_model=from_model,
    to_model=f"mode:{MODE_COMFY}",
    current_step="preflight",
    state_text=f"Iniciando modo comfy ({source})",
    ready=False,
    error=None,
    steps=steps,
  )

  try:
    ensure_container_created(COMFY_CONTAINER, "prod-bootstrap-models")
    add_step(
      steps,
      "preflight",
      True,
      f"target container exists: {COMFY_CONTAINER}",
      switch_id=switch_id,
      state="running",
      state_text=f"Preflight OK para {COMFY_CONTAINER}",
    )

    comfy_state = before.get("comfyui", {})
    comfy_running = isinstance(comfy_state, dict) and comfy_state.get("status") == "running"
    if active_mode() == MODE_COMFY and comfy_running and len(running_before) == 0:
      lease_until = set_comfy_lease(ttl_minutes)
      set_active_mode(MODE_COMFY)
      add_step(
        steps,
        "renew_lease",
        True,
        f"comfy lease renewed until {lease_until.isoformat()}",
        switch_id=switch_id,
        state="success",
        state_text="Lease de ComfyUI renovado",
        ready=True,
        error=None,
      )
      set_runtime_state(clear_error=True)
      update_switch_state(
        switch_id,
        state="success",
        from_model=from_model,
        to_model=f"mode:{MODE_COMFY}",
        current_step="complete",
        state_text="ComfyUI disponible",
        ready=True,
        error=None,
        steps=steps,
      )
      return switch_response(
        switch_id=switch_id,
        status="success",
        from_model=from_model,
        to_model=f"mode:{MODE_COMFY}",
        steps=steps,
        started=started,
        error=None,
      )

    update_switch_state(
      switch_id,
      current_step="stop_litellm",
      state_text="Deteniendo LiteLLM",
      state="running",
    )
    container_stop(LITELLM_CONTAINER)
    disruptive_started = True
    add_step(
      steps,
      "stop_litellm",
      True,
      "litellm stopped",
      switch_id=switch_id,
      state="running",
      state_text="LiteLLM detenido",
    )

    update_switch_state(
      switch_id,
      current_step="stop_models",
      state_text="Deteniendo contenedores de modelo",
      state="running",
    )
    stop_all_model_containers()
    add_step(
      steps,
      "stop_models",
      True,
      "all vllm containers stopped",
      switch_id=switch_id,
      state="running",
      state_text="Contenedores de modelo detenidos",
    )

    update_switch_state(
      switch_id,
      current_step="start_comfy",
      state_text=f"Iniciando {COMFY_CONTAINER}",
      state="running",
    )
    container_start(COMFY_CONTAINER)
    add_step(
      steps,
      "start_comfy",
      True,
      f"started {COMFY_CONTAINER}",
      switch_id=switch_id,
      state="running",
      state_text=f"{COMFY_CONTAINER} iniciado",
    )

    update_switch_state(
      switch_id,
      current_step="wait_comfy",
      state_text=f"Esperando health de {COMFY_CONTAINER}",
      state="running",
    )
    wait_container_ready(COMFY_CONTAINER, HEALTH_TIMEOUT_SECONDS)
    add_step(
      steps,
      "wait_comfy",
      True,
      f"{COMFY_CONTAINER} is ready",
      switch_id=switch_id,
      state="running",
      state_text=f"{COMFY_CONTAINER} listo",
    )

    set_active_mode(MODE_COMFY)
    lease_until = set_comfy_lease(ttl_minutes)
    add_step(
      steps,
      "activate_mode",
      True,
      f"active mode set to comfy until {lease_until.isoformat()}",
      switch_id=switch_id,
      state="success",
      state_text=f"ComfyUI activo ({ttl_minutes} min)",
      ready=True,
      error=None,
    )

    set_runtime_state(clear_error=True)
    update_switch_state(
      switch_id,
      state="success",
      from_model=from_model,
      to_model=f"mode:{MODE_COMFY}",
      current_step="complete",
      state_text=f"ComfyUI activo ({ttl_minutes} min)",
      ready=True,
      error=None,
      steps=steps,
    )
    return switch_response(
      switch_id=switch_id,
      status="success",
      from_model=from_model,
      to_model=f"mode:{MODE_COMFY}",
      steps=steps,
      started=started,
      error=None,
    )

  except HTTPException as exc:
    if raise_http_errors:
      update_switch_state(
        switch_id,
        state="failed",
        from_model=from_model,
        to_model=f"mode:{MODE_COMFY}",
        current_step="switch_error",
        state_text=f"Error: {exc.detail}",
        ready=False,
        error=str(exc.detail),
        steps=steps,
      )
      raise

    error_detail = str(exc.detail)
    add_step(
      steps,
      "switch_error",
      False,
      error_detail,
      switch_id=switch_id,
      state="failed",
      state_text="Error en cambio de modo",
      ready=False,
      error=error_detail,
    )
    set_runtime_state(last_error=error_detail)
    update_switch_state(
      switch_id,
      state="failed",
      from_model=from_model,
      to_model=f"mode:{MODE_COMFY}",
      current_step="complete",
      state_text="Error en cambio de modo",
      ready=False,
      error=error_detail,
      steps=steps,
    )
    return switch_response(
      switch_id=switch_id,
      status="failed",
      from_model=from_model,
      to_model=f"mode:{MODE_COMFY}",
      steps=steps,
      started=started,
      error=error_detail,
    )

  except Exception as exc:
    error_detail = str(exc)
    add_step(
      steps,
      "switch_error",
      False,
      error_detail,
      switch_id=switch_id,
      state="running",
      state_text="Error detectado, iniciando rollback",
      ready=False,
      error=error_detail,
    )

    final_status = "failed"

    if disruptive_started:
      rollback_model = from_model if from_model in MODELS else default_llm_model()
      try:
        update_switch_state(
          switch_id,
          current_step="rollback_stop_comfy",
          state_text="Rollback: deteniendo ComfyUI",
          state="running",
        )
        container_stop(COMFY_CONTAINER)
        add_step(
          steps,
          "rollback_stop_comfy",
          True,
          "comfyui stopped",
          switch_id=switch_id,
          state="running",
          state_text="Rollback: ComfyUI detenido",
        )

        update_switch_state(
          switch_id,
          current_step="rollback_stop_models",
          state_text="Rollback: deteniendo modelos",
          state="running",
        )
        stop_all_model_containers()
        add_step(
          steps,
          "rollback_stop_models",
          True,
          "all vllm containers stopped",
          switch_id=switch_id,
          state="running",
          state_text="Rollback: modelos detenidos",
        )

        rollback_container = MODELS[rollback_model]["container"]
        if container_json(rollback_container) is None:
          raise RuntimeError(f"rollback container missing: {rollback_container}")

        update_switch_state(
          switch_id,
          current_step="rollback_start_previous",
          state_text=f"Rollback: iniciando {rollback_container}",
          state="running",
        )
        container_start(rollback_container)
        wait_container_ready(rollback_container, HEALTH_TIMEOUT_SECONDS)
        add_step(
          steps,
          "rollback_start_previous",
          True,
          f"restored {rollback_model}",
          switch_id=switch_id,
          state="running",
          state_text=f"Rollback: {rollback_model} restaurado",
        )

        ensure_active_config(rollback_model)
        add_step(
          steps,
          "rollback_restore_config",
          True,
          f"active config set to {rollback_model}",
          switch_id=switch_id,
          state="running",
          state_text="Rollback: configuracion restaurada",
        )

        update_switch_state(
          switch_id,
          current_step="rollback_litellm",
          state_text="Rollback: iniciando LiteLLM",
          state="running",
        )
        container_start(LITELLM_CONTAINER)
        rollback_litellm_model = MODELS[rollback_model]["litellm_model"]
        wait_litellm_model(rollback_litellm_model, LITELLM_VERIFY_TIMEOUT_SECONDS)
        add_step(
          steps,
          "rollback_litellm",
          True,
          "litellm restored",
          switch_id=switch_id,
          state="running",
          state_text="Rollback completado",
        )
        set_active_mode(MODE_LLM)
        clear_comfy_lease()

        final_status = "rolled_back"
      except Exception as rollback_exc:
        rollback_error = str(rollback_exc)
        add_step(
          steps,
          "rollback_error",
          False,
          rollback_error,
          switch_id=switch_id,
          state="failed",
          state_text="Error durante rollback",
          ready=False,
          error=rollback_error,
        )
        error_detail = f"{error_detail}; rollback failed: {rollback_error}"

    set_runtime_state(last_error=error_detail)

    final_text = "Rollback completado; modo comfy no disponible"
    if final_status == "failed":
      final_text = "Error en cambio de modo"

    update_switch_state(
      switch_id,
      state=final_status,
      from_model=from_model,
      to_model=f"mode:{MODE_COMFY}",
      current_step="complete",
      state_text=final_text,
      ready=False,
      error=error_detail,
      steps=steps,
    )
    return switch_response(
      switch_id=switch_id,
      status=final_status,
      from_model=from_model,
      to_model=f"mode:{MODE_COMFY}",
      steps=steps,
      started=started,
      error=error_detail,
    )


def run_mode_switch_pipeline_async(req_payload: Dict[str, Any], switch_id: int) -> None:
  try:
    req = ModeSwitchRequest(**req_payload)
    run_mode_switch_pipeline(req, switch_id, raise_http_errors=False)
  except Exception as exc:  # pragma: no cover - guardrail
    error_detail = f"unexpected async mode switch error: {exc}"
    set_runtime_state(last_error=error_detail)
    update_switch_state(
      switch_id,
      state="failed",
      current_step="switch_error",
      state_text="Error inesperado en cambio de modo async",
      ready=False,
      error=error_detail,
    )
  finally:
    SWITCH_LOCK.release()


def monitor_comfy_lease() -> None:
  poll_seconds = max(MODE_MONITOR_POLL_SECONDS, 1)
  while True:
    time.sleep(poll_seconds)
    if active_mode() != MODE_COMFY:
      continue

    lease = comfy_lease_status()
    if not lease.get("expired"):
      continue

    if not SWITCH_LOCK.acquire(blocking=False):
      continue

    target_model = default_llm_model()
    switch_id = create_switch_state(
      to_model=target_model,
      from_model=None,
      state="queued",
      state_text=f"Lease comfy expirado; devolviendo a {target_model}",
      current_step="lease_expired",
    )

    try:
      req = ModeSwitchRequest(mode=MODE_LLM, model=target_model, wait_for_ready=True)
      run_mode_switch_pipeline(req, switch_id, raise_http_errors=False, source="lease_expired")
    except Exception as exc:  # pragma: no cover - guardrail
      error_detail = f"lease monitor recovery failed: {exc}"
      set_runtime_state(last_error=error_detail)
      update_switch_state(
        switch_id,
        state="failed",
        current_step="lease_expired_error",
        state_text="Error al recuperar modo llm tras expirar lease",
        ready=False,
        error=error_detail,
      )
    finally:
      SWITCH_LOCK.release()


@app.on_event("startup")
def startup_init_mode() -> None:
  global MODE_MONITOR_THREAD

  os.makedirs(CONFIG_DIR, exist_ok=True)
  if read_optional_text(ACTIVE_MODE_FILE) is None:
    set_active_mode(MODE_LLM)
  elif active_mode() != MODE_COMFY:
    set_active_mode(MODE_LLM)

  if active_mode() != MODE_COMFY:
    clear_comfy_lease()

  with MODE_MONITOR_LOCK:
    if MODE_MONITOR_THREAD is None or not MODE_MONITOR_THREAD.is_alive():
      MODE_MONITOR_THREAD = threading.Thread(
        target=monitor_comfy_lease,
        name="comfy-lease-monitor",
        daemon=True,
      )
      MODE_MONITOR_THREAD.start()


@app.get("/health")
def health() -> Dict[str, str]:
  return {"status": "ok"}


@app.get("/admin", response_class=HTMLResponse)
def admin() -> HTMLResponse:
  return HTMLResponse(
    content="""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Model Switcher Admin</title>
  <style>
    :root {
      --bg: #10151e;
      --panel: #171f2d;
      --panel-alt: #1f293b;
      --text: #edf2f7;
      --muted: #94a3b8;
      --ok: #16a34a;
      --warn: #eab308;
      --danger: #dc2626;
      --accent: #0ea5e9;
      --border: #334155;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(160deg, var(--bg), #0b111a);
      color: var(--text);
      min-height: 100vh;
      padding: 24px;
    }
    .layout {
      max-width: 980px;
      margin: 0 auto;
      display: grid;
      gap: 16px;
    }
    .card {
      background: linear-gradient(180deg, var(--panel), var(--panel-alt));
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 20px;
    }
    .hint {
      color: var(--muted);
      margin: 0;
      font-size: 14px;
    }
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
      align-items: center;
    }
    input, select, button {
      border-radius: 8px;
      border: 1px solid var(--border);
      background: #0f172a;
      color: var(--text);
      padding: 9px 10px;
      font-size: 14px;
    }
    input, select { min-width: 160px; }
    button {
      cursor: pointer;
      background: #0f172a;
    }
    button.primary { border-color: var(--accent); }
    button.warn { border-color: var(--warn); }
    button.danger { border-color: var(--danger); }
    pre {
      background: #0b1220;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      font-size: 12px;
      overflow: auto;
      white-space: pre-wrap;
      margin: 0;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      color: var(--muted);
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      display: inline-block;
      background: var(--warn);
    }
    .dot.ok { background: var(--ok); }
    .dot.danger { background: var(--danger); }
  </style>
</head>
<body>
  <main class="layout">
    <section class="card">
      <h1>Model Switcher Admin</h1>
      <p class="hint">Canal de control always-on para cambiar entre modo LLM y ComfyUI.</p>
      <div class="row">
        <label for="token">Bearer token:</label>
        <input id="token" type="password" placeholder="MODEL_SWITCHER_TOKEN">
        <button id="saveToken">Guardar token</button>
        <span class="badge"><span id="statusDot" class="dot"></span><span id="statusText">sin estado</span></span>
      </div>
    </section>

    <section class="card">
      <div class="row">
        <button id="refresh">Refrescar estado</button>
        <button id="modeComfy" class="warn">Activar Comfy (45m)</button>
        <select id="llmModel">
          <option value="qwen-fast">qwen-fast</option>
          <option value="qwen-quality">qwen-quality</option>
          <option value="deepseek">deepseek</option>
          <option value="qwen-max">qwen-max</option>
        </select>
        <button id="modeLlm" class="primary">Volver a LLM</button>
        <button id="release" class="danger">Preemption LLM</button>
      </div>
      <p class="hint">`Preemption LLM` fuerza la salida de comfy y restaura el modo llm por prioridad operativa.</p>
    </section>

    <section class="card">
      <pre id="output">Cargando...</pre>
    </section>
  </main>

  <script>
    const tokenInput = document.getElementById("token");
    const outputEl = document.getElementById("output");
    const statusText = document.getElementById("statusText");
    const statusDot = document.getElementById("statusDot");

    const LS_KEY = "model_switcher_token";
    tokenInput.value = window.localStorage.getItem(LS_KEY) || "";

    function setStatus(mode, healthy) {
      statusText.textContent = mode ? `modo=${mode}` : "sin estado";
      statusDot.className = "dot";
      if (healthy === true) statusDot.classList.add("ok");
      if (healthy === false) statusDot.classList.add("danger");
    }

    function getHeaders() {
      const token = tokenInput.value.trim();
      if (!token) throw new Error("Token requerido");
      return {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json",
      };
    }

    async function api(method, path, body) {
      const headers = getHeaders();
      const response = await fetch(path, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
      });
      const text = await response.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { raw: text }; }
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}\\n${JSON.stringify(data, null, 2)}`);
      }
      return data;
    }

    async function refresh() {
      try {
        const modePayload = await api("GET", "/mode");
        const activeMode = modePayload?.mode?.active || "unknown";
        const healthy = modePayload?.switch_in_progress ? null : true;
        setStatus(activeMode, healthy);
        outputEl.textContent = JSON.stringify(modePayload, null, 2);
      } catch (err) {
        setStatus(null, false);
        outputEl.textContent = String(err);
      }
    }

    async function switchComfy() {
      const payload = await api("POST", "/mode/switch", { mode: "comfy", ttl_minutes: 45, wait_for_ready: true });
      outputEl.textContent = JSON.stringify(payload, null, 2);
      await refresh();
    }

    async function switchLlm() {
      const model = document.getElementById("llmModel").value;
      const payload = await api("POST", "/mode/switch", { mode: "llm", model, wait_for_ready: true });
      outputEl.textContent = JSON.stringify(payload, null, 2);
      await refresh();
    }

    async function releaseLlm() {
      const payload = await api("POST", "/mode/release", {});
      outputEl.textContent = JSON.stringify(payload, null, 2);
      await refresh();
    }

    document.getElementById("saveToken").addEventListener("click", () => {
      window.localStorage.setItem(LS_KEY, tokenInput.value.trim());
      refresh();
    });
    document.getElementById("refresh").addEventListener("click", () => refresh());
    document.getElementById("modeComfy").addEventListener("click", () => switchComfy().catch((e) => { outputEl.textContent = String(e); }));
    document.getElementById("modeLlm").addEventListener("click", () => switchLlm().catch((e) => { outputEl.textContent = String(e); }));
    document.getElementById("release").addEventListener("click", () => releaseLlm().catch((e) => { outputEl.textContent = String(e); }));

    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>"""
  )


@app.get("/healthz/ready")
def ready() -> Dict[str, str]:
  payload = status_payload()
  running = running_models_from_status(payload)
  active = payload.get("active_model")

  if payload.get("active_mode") != MODE_LLM:
    raise HTTPException(status_code=503, detail="llm mode is not active")
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


@app.get("/mode", dependencies=[Depends(require_token)])
def mode_status() -> Dict[str, Any]:
  payload = status_payload()
  return {
    "mode": payload.get("mode"),
    "running_models": payload.get("running_models"),
    "litellm": payload.get("litellm"),
    "comfyui": payload.get("comfyui"),
    "switch_in_progress": payload.get("switch_in_progress"),
    "switch": payload.get("switch"),
  }


@app.post("/mode/switch", dependencies=[Depends(require_token)])
def mode_switch(req: ModeSwitchRequest) -> Any:
  mode = req.mode.strip().lower()
  if mode not in VALID_MODES:
    raise HTTPException(status_code=400, detail="unknown mode")

  target_model: Optional[str] = None
  if mode == MODE_LLM:
    if req.ttl_minutes is not None:
      raise HTTPException(status_code=400, detail="ttl_minutes is only valid for mode='comfy'")
    target_model = resolve_llm_model(req.model)
  else:
    if req.model is not None and req.model.strip():
      raise HTTPException(status_code=400, detail="model is only valid for mode='llm'")
    normalize_comfy_ttl(req.ttl_minutes)

  to_model = target_model if target_model is not None else f"mode:{mode}"

  if req.wait_for_ready:
    if not SWITCH_LOCK.acquire(blocking=False):
      raise HTTPException(status_code=409, detail="switch_in_progress")

    switch_id = create_switch_state(
      to_model=to_model,
      from_model=None,
      state="running",
      state_text=f"Iniciando cambio de modo a {mode}",
      current_step="accepted",
    )

    try:
      return run_mode_switch_pipeline(req, switch_id, raise_http_errors=True)
    finally:
      SWITCH_LOCK.release()

  if not SWITCH_LOCK.acquire(blocking=False):
    current = current_switch_snapshot()
    in_progress_id = current.get("id") if current else None
    in_progress_model = current.get("to_model") if current else to_model
    in_progress_text = current.get("state_text") if current else "Cambio de modo en curso"
    return JSONResponse(
      status_code=202,
      content={
        "status": "in_progress",
        "switch_id": in_progress_id,
        "to_model": in_progress_model,
        "state_text": in_progress_text,
        "poll_endpoint": "/status",
      },
    )

  switch_id = create_switch_state(
    to_model=to_model,
    from_model=None,
    state="queued",
    state_text=f"Cambio de modo aceptado a {mode}",
    current_step="queued",
  )

  worker = threading.Thread(
    target=run_mode_switch_pipeline_async,
    args=(req.dict(), switch_id),
    daemon=True,
  )
  worker.start()

  return JSONResponse(
    status_code=202,
    content={
      "status": "accepted",
      "switch_id": switch_id,
      "to_model": to_model,
      "state_text": f"Cambio de modo aceptado a {mode}",
      "poll_endpoint": "/status",
    },
  )


@app.post("/mode/release", dependencies=[Depends(require_token)])
def mode_release() -> Dict[str, Any]:
  if not SWITCH_LOCK.acquire(blocking=False):
    raise HTTPException(status_code=409, detail="switch_in_progress")

  target_model = default_llm_model()
  switch_id = create_switch_state(
    to_model=target_model,
    from_model=None,
    state="running",
    state_text=f"Preemption a modo llm ({target_model})",
    current_step="accepted",
  )

  try:
    req = ModeSwitchRequest(mode=MODE_LLM, model=target_model, wait_for_ready=True)
    return run_mode_switch_pipeline(req, switch_id, raise_http_errors=True, source="manual_release")
  finally:
    SWITCH_LOCK.release()


@app.post("/switch", dependencies=[Depends(require_token)])
def switch(req: SwitchRequest) -> Any:
  model = req.model.strip()
  if model not in MODELS:
    raise HTTPException(status_code=400, detail="unknown model")

  if req.wait_for_ready:
    if not SWITCH_LOCK.acquire(blocking=False):
      raise HTTPException(status_code=409, detail="switch_in_progress")

    switch_id = create_switch_state(
      to_model=model,
      from_model=None,
      state="running",
      state_text=f"Iniciando cambio a {model}",
      current_step="accepted",
    )

    try:
      return run_switch_pipeline(model, switch_id, raise_http_errors=True)
    finally:
      SWITCH_LOCK.release()

  if not SWITCH_LOCK.acquire(blocking=False):
    current = current_switch_snapshot()
    in_progress_id = current.get("id") if current else None
    in_progress_model = current.get("to_model") if current else model
    in_progress_text = current.get("state_text") if current else "Cambio de modelo en curso"
    return JSONResponse(
      status_code=202,
      content={
        "status": "in_progress",
        "switch_id": in_progress_id,
        "to_model": in_progress_model,
        "state_text": in_progress_text,
        "poll_endpoint": "/status",
      },
    )

  switch_id = create_switch_state(
    to_model=model,
    from_model=None,
    state="queued",
    state_text=f"Cambio aceptado a {model}",
    current_step="queued",
  )

  worker = threading.Thread(target=run_switch_pipeline_async, args=(model, switch_id), daemon=True)
  worker.start()

  return JSONResponse(
    status_code=202,
    content={
      "status": "accepted",
      "switch_id": switch_id,
      "to_model": model,
      "state_text": f"Cambio aceptado a {model}",
      "poll_endpoint": "/status",
    },
  )


@app.post("/stop", dependencies=[Depends(require_token)])
def stop() -> Dict[str, Any]:
  stop_all_model_containers()
  container_stop(COMFY_CONTAINER)
  set_active_mode(MODE_LLM)
  clear_comfy_lease()
  return status_payload()
