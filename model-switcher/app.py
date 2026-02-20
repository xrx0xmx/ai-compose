import json
import os
import secrets
import subprocess
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import httpx
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


COMPOSE_DIR = Path(os.getenv("MODEL_SWITCHER_COMPOSE_DIR", "/opt/ai/compose")).resolve()
COMPOSE_FILE_BASE = Path(
    os.getenv("MODEL_SWITCHER_COMPOSE_FILE_BASE", str(COMPOSE_DIR / "docker-compose.yml"))
)
COMPOSE_FILE_PROD = Path(
    os.getenv("MODEL_SWITCHER_COMPOSE_FILE_PROD", str(COMPOSE_DIR / "docker-compose.prod.yml"))
)
ACTIVE_SYMLINK = COMPOSE_DIR / "litellm-active.yml"
STATE_FILE = Path(os.getenv("MODEL_SWITCHER_STATE_FILE", str(COMPOSE_DIR / ".active-model.json")))
AUDIT_FILE = Path(os.getenv("MODEL_SWITCHER_AUDIT_FILE", str(COMPOSE_DIR / "model-switcher-audit.log")))
LITELLM_MODELS_URL = os.getenv("MODEL_SWITCHER_LITELLM_MODELS_URL", "http://litellm:4000/v1/models")
LITELLM_KEY = os.getenv("MODEL_SWITCHER_LITELLM_KEY", "cambiaLAclave")
ADMIN_TOKEN = os.getenv("MODEL_SWITCHER_ADMIN_TOKEN", "")
HEALTH_TIMEOUT_SECONDS = int(os.getenv("MODEL_SWITCHER_HEALTH_TIMEOUT_SECONDS", "420"))
LITELLM_TIMEOUT_SECONDS = int(os.getenv("MODEL_SWITCHER_LITELLM_TIMEOUT_SECONDS", "180"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("MODEL_SWITCHER_RATE_LIMIT_PER_MINUTE", "5"))


if not STATE_FILE.is_absolute():
    STATE_FILE = (COMPOSE_DIR / STATE_FILE).resolve()
if not AUDIT_FILE.is_absolute():
    AUDIT_FILE = (COMPOSE_DIR / AUDIT_FILE).resolve()


app = FastAPI(title="Model Switcher", version="1.0.0")

SWITCH_LOCK = threading.Lock()
JOBS_LOCK = threading.Lock()
JOBS: Dict[str, "SwitchJob"] = {}
RATE_LIMIT_STATE: Dict[str, deque] = defaultdict(deque)


class ModelCatalogItem(BaseModel):
    id: str
    display_name: str
    profile: str
    service_name: str
    litellm_config: str
    healthy: bool
    available: bool


class JobStep(BaseModel):
    at: str
    step: str
    message: str


class SwitchJob(BaseModel):
    id: str
    status: Literal["accepted", "running", "success", "failed", "rolled_back"]
    step: str
    error: Optional[str]
    requested_by: str
    from_model: Optional[str]
    to_model: str
    started_at: str
    ended_at: Optional[str] = None
    steps: List[JobStep] = Field(default_factory=list)


class SwitchRequest(BaseModel):
    target_model: str


class ModelsResponse(BaseModel):
    models: List[ModelCatalogItem]
    current_model: Optional[str]
    switch_in_progress: bool
    degraded: bool
    reconcile_message: Optional[str]


class ModelsSnapshot(BaseModel):
    models: List[ModelCatalogItem]
    current_model: Optional[str]
    persisted_model: Optional[str]
    healthy_models: List[str]
    degraded: bool
    reconcile_message: Optional[str]


def compose_cmd(*args: str) -> List[str]:
    cmd = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE_BASE),
        "-f",
        str(COMPOSE_FILE_PROD),
    ]
    cmd.extend(args)
    return cmd


def compose_cmd_with_all_profiles(*args: str) -> List[str]:
    profiles_result = run_cmd(compose_cmd("config", "--profiles"), timeout=30, check=False)
    profiles = []
    if profiles_result.returncode == 0:
        profiles = [line.strip() for line in profiles_result.stdout.splitlines() if line.strip()]

    cmd = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE_BASE),
        "-f",
        str(COMPOSE_FILE_PROD),
    ]
    for profile in sorted(set(profiles)):
        cmd.extend(["--profile", profile])
    cmd.extend(args)
    return cmd


def run_cmd(cmd: List[str], *, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        cmd,
        cwd=str(COMPOSE_DIR),
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {stderr}")
    return completed


def labels_to_dict(raw_labels: Any) -> Dict[str, str]:
    if isinstance(raw_labels, dict):
        return {str(k): str(v) for k, v in raw_labels.items()}

    if isinstance(raw_labels, list):
        parsed: Dict[str, str] = {}
        for item in raw_labels:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                parsed[key] = value
        return parsed

    return {}


def get_container_ref(service_name: str, container_name: str) -> Optional[str]:
    if container_name:
        return container_name

    result = run_cmd(compose_cmd("ps", "-q", service_name), check=False)
    value = result.stdout.strip()
    return value if value else None


def inspect_container_status(container_ref: Optional[str]) -> Optional[str]:
    if not container_ref:
        return None

    status = run_cmd(
        [
            "docker",
            "inspect",
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            container_ref,
        ],
        check=False,
    )
    if status.returncode != 0:
        return None
    value = status.stdout.strip()
    return value or None


def discover_models() -> List[ModelCatalogItem]:
    result = run_cmd(compose_cmd_with_all_profiles("config"), timeout=90)
    config = yaml.safe_load(result.stdout) or {}
    services = config.get("services", {}) if isinstance(config, dict) else {}

    discovered: List[ModelCatalogItem] = []
    for service_name, service_conf in services.items():
        labels = labels_to_dict(service_conf.get("labels"))
        model_id = labels.get("ai.model.id")
        display_name = labels.get("ai.model.display_name")
        profile = labels.get("ai.model.profile")
        litellm_config = labels.get("ai.model.litellm_config")

        if not (model_id and display_name and profile and litellm_config):
            continue

        container_name = str(service_conf.get("container_name") or "")
        container_ref = get_container_ref(service_name, container_name)
        status = inspect_container_status(container_ref)

        config_path = Path(litellm_config)
        if not config_path.is_absolute():
            config_path = COMPOSE_DIR / litellm_config

        discovered.append(
            ModelCatalogItem(
                id=model_id,
                display_name=display_name,
                profile=profile,
                service_name=service_name,
                litellm_config=litellm_config,
                healthy=(status == "healthy" or status == "running"),
                available=config_path.exists(),
            )
        )

    discovered.sort(key=lambda item: item.id)
    return discovered


def read_persisted_model() -> Optional[str]:
    if not STATE_FILE.exists():
        return None

    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    model_id = payload.get("active_model") if isinstance(payload, dict) else None
    return str(model_id) if model_id else None


def persist_active_model(model_id: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"active_model": model_id, "updated_at": utc_now()}
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")


def resolve_current_model(models: List[ModelCatalogItem]) -> Optional[str]:
    if not ACTIVE_SYMLINK.exists() and not ACTIVE_SYMLINK.is_symlink():
        return None

    try:
        target = Path(os.path.realpath(ACTIVE_SYMLINK))
        target_name = target.name
    except OSError:
        return None

    for model in models:
        if Path(model.litellm_config).name == target_name:
            return model.id

    return None


def snapshot_models() -> ModelsSnapshot:
    models = discover_models()
    current_model = resolve_current_model(models)
    persisted_model = read_persisted_model()
    healthy_models = [item.id for item in models if item.healthy]

    degraded = False
    reasons: List[str] = []

    if persisted_model and current_model and persisted_model != current_model:
        degraded = True
        reasons.append("persisted_model differs from litellm-active symlink")

    if len(healthy_models) > 1:
        degraded = True
        reasons.append("more than one model backend is healthy")

    if len(healthy_models) == 1 and current_model and healthy_models[0] != current_model:
        degraded = True
        reasons.append("healthy backend differs from litellm-active symlink")

    if len(healthy_models) == 1 and not current_model:
        degraded = True
        reasons.append("healthy backend exists but symlink cannot be resolved")

    reconcile_message = "; ".join(reasons) if reasons else None

    return ModelsSnapshot(
        models=models,
        current_model=current_model,
        persisted_model=persisted_model,
        healthy_models=healthy_models,
        degraded=degraded,
        reconcile_message=reconcile_message,
    )


def append_audit(event: Dict[str, Any]) -> None:
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = dict(event)
    record.setdefault("at", utc_now())
    with AUDIT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True) + "\n")


def swap_litellm_symlink(target_model: ModelCatalogItem) -> None:
    target_path = Path(target_model.litellm_config)
    if not target_path.is_absolute():
        target_path = COMPOSE_DIR / target_model.litellm_config

    if not target_path.exists():
        raise RuntimeError(f"LiteLLM config not found: {target_path}")

    if ACTIVE_SYMLINK.exists() or ACTIVE_SYMLINK.is_symlink():
        ACTIVE_SYMLINK.unlink()

    os.symlink(target_path, ACTIVE_SYMLINK)


def wait_service_healthy(model: ModelCatalogItem, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    last_status = "unknown"

    while time.time() < deadline:
        container_ref = get_container_ref(model.service_name, "")
        status = inspect_container_status(container_ref)
        if status:
            last_status = status
        if status in {"healthy", "running"}:
            return
        if status in {"unhealthy", "exited", "dead"}:
            raise RuntimeError(f"Container for {model.id} is {status}")
        time.sleep(5)

    raise TimeoutError(f"Timeout waiting for {model.id} health, last status={last_status}")


def wait_litellm_model(model_id: str, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    headers = {"Authorization": f"Bearer {LITELLM_KEY}"}

    while time.time() < deadline:
        try:
            response = httpx.get(LITELLM_MODELS_URL, headers=headers, timeout=10.0)
            if response.status_code == 200:
                payload = response.json()
                data = payload.get("data", []) if isinstance(payload, dict) else []
                model_ids = {str(item.get("id")) for item in data if isinstance(item, dict)}
                if model_id in model_ids:
                    return
        except Exception:
            pass
        time.sleep(3)

    raise TimeoutError(f"LiteLLM did not report model {model_id} in {timeout_seconds}s")


def run_switch_sequence(target_model: ModelCatalogItem, catalog: Dict[str, ModelCatalogItem]) -> None:
    service_list = sorted({model.service_name for model in catalog.values()})
    run_cmd(compose_cmd_with_all_profiles("stop", "litellm", *service_list), timeout=240)
    swap_litellm_symlink(target_model)
    run_cmd(compose_cmd_with_all_profiles("up", "-d", target_model.service_name), timeout=240)
    wait_service_healthy(target_model, HEALTH_TIMEOUT_SECONDS)
    run_cmd(compose_cmd_with_all_profiles("up", "-d", "litellm"), timeout=120)
    wait_litellm_model(target_model.id, LITELLM_TIMEOUT_SECONDS)


def ensure_admin(authorization: Optional[str] = Header(default=None)) -> str:
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="MODEL_SWITCHER_ADMIN_TOKEN is not configured")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    if not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid token")

    return token


def enforce_switch_rate_limit(key: str) -> None:
    now = time.time()
    window_start = now - 60

    bucket = RATE_LIMIT_STATE[key]
    while bucket and bucket[0] < window_start:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Too many switch requests")

    bucket.append(now)


def update_job(job_id: str, *, status: Optional[str] = None, step: Optional[str] = None, error: Optional[str] = None, end: bool = False, message: Optional[str] = None) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        if status:
            job.status = status  # type: ignore[assignment]
        if step:
            job.step = step
        if error is not None:
            job.error = error
        if message:
            job.steps.append(JobStep(at=utc_now(), step=job.step, message=message))
        if end:
            job.ended_at = utc_now()


def execute_switch_job(job_id: str, target_model_id: str, requested_by: str, source_ip: str) -> None:
    try:
        try:
            update_job(job_id, status="running", step="discover", message="Discovering model catalog")
            snapshot = snapshot_models()
            catalog = {model.id: model for model in snapshot.models}

            target = catalog.get(target_model_id)
            if target is None:
                raise RuntimeError(f"Target model not found: {target_model_id}")

            previous_model_id = snapshot.current_model
            with JOBS_LOCK:
                JOBS[job_id].from_model = previous_model_id

            update_job(job_id, step="switch", message=f"Switching to {target_model_id}")
            run_switch_sequence(target, catalog)
            persist_active_model(target_model_id)

            update_job(job_id, status="success", step="done", message=f"Model {target_model_id} is active", end=True)
            append_audit(
                {
                    "event": "switch_success",
                    "job_id": job_id,
                    "requested_by": requested_by,
                    "source_ip": source_ip,
                    "from_model": previous_model_id,
                    "to_model": target_model_id,
                    "status": "success",
                }
            )
            return

        except Exception as exc:
            error_message = str(exc)
            update_job(job_id, step="failed", error=error_message, message=error_message)

        try:
            with JOBS_LOCK:
                previous_model_id = JOBS[job_id].from_model

            snapshot = snapshot_models()
            catalog = {model.id: model for model in snapshot.models}

            if previous_model_id and previous_model_id in catalog and previous_model_id != target_model_id:
                update_job(job_id, step="rollback", message=f"Rolling back to {previous_model_id}")
                run_switch_sequence(catalog[previous_model_id], catalog)
                persist_active_model(previous_model_id)

                update_job(job_id, status="rolled_back", step="done", message="Rollback completed", end=True)
                append_audit(
                    {
                        "event": "switch_rolled_back",
                        "job_id": job_id,
                        "requested_by": requested_by,
                        "source_ip": source_ip,
                        "from_model": previous_model_id,
                        "to_model": target_model_id,
                        "status": "rolled_back",
                    }
                )
                return

        except Exception as rollback_exc:
            message = f"{JOBS[job_id].error}; rollback_failed: {rollback_exc}"
            update_job(job_id, error=message, message=f"Rollback failed: {rollback_exc}")

        update_job(job_id, status="failed", step="done", end=True)
        append_audit(
            {
                "event": "switch_failed",
                "job_id": job_id,
                "requested_by": requested_by,
                "source_ip": source_ip,
                "from_model": JOBS[job_id].from_model,
                "to_model": target_model_id,
                "status": "failed",
                "error": JOBS[job_id].error,
            }
        )
    finally:
        if SWITCH_LOCK.locked():
            SWITCH_LOCK.release()


@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/ops/models")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "switch_in_progress": SWITCH_LOCK.locked(),
        "compose_dir": str(COMPOSE_DIR),
        "token_configured": bool(ADMIN_TOKEN),
    }


@app.get("/ops/models", include_in_schema=False)
def ops_models_page() -> FileResponse:
    return FileResponse("/app/static/index.html")


@app.get("/api/models", response_model=ModelsResponse)
def get_models(_: str = Depends(ensure_admin)) -> ModelsResponse:
    try:
        snapshot = snapshot_models()
    except Exception as exc:
        return ModelsResponse(
            models=[],
            current_model=None,
            switch_in_progress=SWITCH_LOCK.locked(),
            degraded=True,
            reconcile_message=f"failed to discover models: {exc}",
        )

    return ModelsResponse(
        models=snapshot.models,
        current_model=snapshot.current_model,
        switch_in_progress=SWITCH_LOCK.locked(),
        degraded=snapshot.degraded,
        reconcile_message=snapshot.reconcile_message,
    )


@app.post("/api/switch")
def switch_model(req: SwitchRequest, request: Request, _: str = Depends(ensure_admin)) -> JSONResponse:
    source_ip = request.client.host if request.client else "unknown"
    enforce_switch_rate_limit(source_ip)

    if SWITCH_LOCK.locked():
        raise HTTPException(status_code=409, detail="switch_in_progress")

    try:
        snapshot = snapshot_models()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to discover models: {exc}")

    catalog = {model.id: model for model in snapshot.models}
    if req.target_model not in catalog:
        raise HTTPException(status_code=400, detail="target_model_not_found")

    if snapshot.degraded:
        raise HTTPException(status_code=409, detail=f"degraded_state: {snapshot.reconcile_message}")

    if snapshot.current_model == req.target_model:
        raise HTTPException(status_code=400, detail="target_model_already_active")

    if not SWITCH_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="switch_in_progress")

    user_hint = request.headers.get("X-Forwarded-User") or request.headers.get("X-User") or source_ip
    job_id = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[job_id] = SwitchJob(
            id=job_id,
            status="accepted",
            step="queued",
            error=None,
            requested_by=user_hint,
            from_model=snapshot.current_model,
            to_model=req.target_model,
            started_at=utc_now(),
        )

    worker = threading.Thread(
        target=execute_switch_job,
        args=(job_id, req.target_model, user_hint, source_ip),
        daemon=True,
    )
    try:
        worker.start()
    except Exception:
        if SWITCH_LOCK.locked():
            SWITCH_LOCK.release()
        raise

    return JSONResponse({"job_id": job_id, "status": "accepted"})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, _: str = Depends(ensure_admin)) -> JSONResponse:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job_not_found")
        payload = job.model_dump()
    return JSONResponse(payload)


@app.on_event("startup")
def startup_check() -> None:
    try:
        snapshot = snapshot_models()
        if snapshot.current_model:
            persist_active_model(snapshot.current_model)
    except Exception as exc:
        append_audit({"event": "startup_warning", "status": "failed", "error": str(exc)})
