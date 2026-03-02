"""
Admin Panel — Puerto 80
Autenticación via Open WebUI SQLite (bcrypt), sesión JWT, proxy al model-switcher.
"""
import os
import sqlite3
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import bcrypt
import jwt
import requests
from fastapi import FastAPI, HTTPException, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WEBUI_DB_PATH = os.environ.get("WEBUI_DB_PATH", "/webui-data/webui.db")
JWT_SECRET = os.environ.get("ADMIN_JWT_SECRET", "change-this-secret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 8

SWITCHER_URL = os.environ.get("MODEL_SWITCHER_URL", "http://model-switcher:9000")
SWITCHER_TOKEN = os.environ.get("MODEL_SWITCHER_TOKEN", "change_me")
DOCKER_PROXY_URL = os.environ.get("DOCKER_PROXY_URL", "http://docker-socket-proxy:2375")
COMFYUI_INTERNAL_URL = os.environ.get("COMFYUI_INTERNAL_URL", "http://comfyui:8188")
LITELLM_URL = os.environ.get("LITELLM_URL", "http://litellm:4000")
LITELLM_KEY = os.environ.get("LITELLM_KEY", "")

ALLOWED_CONTAINERS = [
    "comfyui",
    "vllm-fast",
    "vllm-quality",
    "vllm-deepseek",
    "vllm-qwen32b",
    "litellm",
    "model-switcher",
    "open-webui",
]

MODEL_INFO = {
    "qwen-fast":    {"label": "Qwen 2.5 7B",   "vram": "~13 GB (55%)", "container": "vllm-fast"},
    "qwen-quality": {"label": "Qwen 2.5 14B",  "vram": "~20 GB (85%)", "container": "vllm-quality"},
    "deepseek":     {"label": "DeepSeek-R1 14B","vram": "~21 GB (95%)", "container": "vllm-deepseek"},
    "qwen-max":     {"label": "Qwen 2.5 32B",  "vram": "~21 GB (95%)", "container": "vllm-qwen32b"},
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("admin-panel")

app = FastAPI(title="Admin Panel", docs_url=None, redoc_url=None)
bearer_scheme = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str
    password: str


def verify_webui_credentials(email: str, password: str) -> Optional[dict]:
    """Returns user dict if credentials are valid and user is admin, else None."""
    try:
        conn = sqlite3.connect(WEBUI_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Open WebUI stores password in the `auth` table, not in `user`
        cur.execute("""
            SELECT u.id, u.name, u.email, u.role, a.password
            FROM user u
            JOIN auth a ON a.id = u.id
            WHERE u.email = ?
        """, (email,))
        row = cur.fetchone()
        conn.close()
    except Exception as e:
        logger.error("DB error: %s", e)
        return None

    if row is None:
        return None

    stored_hash = row["password"]
    try:
        if not bcrypt.checkpw(password.encode(), stored_hash.encode()):
            return None
    except Exception:
        return None

    if row["role"] != "admin":
        return None

    return {"id": row["id"], "name": row["name"], "email": row["email"], "role": row["role"]}


def create_jwt(user: dict) -> str:
    payload = {
        "sub": user["email"],
        "name": user["name"],
        "role": user["role"],
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(tz=timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="No token provided")
    payload = decode_jwt(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


# ---------------------------------------------------------------------------
# Switcher proxy helpers
# ---------------------------------------------------------------------------

def switcher_headers():
    return {"Authorization": f"Bearer {SWITCHER_TOKEN}"}


def switcher_get(path: str, timeout: int = 10) -> dict:
    r = requests.get(f"{SWITCHER_URL}{path}", headers=switcher_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def switcher_post(path: str, body: dict, timeout: int = 120) -> dict:
    r = requests.post(
        f"{SWITCHER_URL}{path}",
        headers=switcher_headers(),
        json=body,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def parse_db_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:  # likely milliseconds
            ts = ts / 1000.0
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        numeric = float(text)
        return parse_db_timestamp(numeric)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


URL_RE = re.compile(r"https?://[^\s'\"<>]+")
HOST_PORT_RE = re.compile(r"(?:(?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9][A-Za-z0-9\.-]*):\d{2,5}")
QUOTED_HOST_RE = re.compile(r"host='[^']+'")
PORT_RE = re.compile(r"port=\d{2,5}")


def sanitize_public_error(message: Any) -> str:
    text = str(message or "").strip()
    if not text:
        return "error"
    text = URL_RE.sub("[hidden-url]", text)
    text = HOST_PORT_RE.sub("[hidden-host]", text)
    text = QUOTED_HOST_RE.sub("host='[hidden-host]'", text)
    text = PORT_RE.sub("port=[hidden-port]", text)
    return text


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table,),
    )
    return cur.fetchone() is not None


def table_columns(conn: sqlite3.Connection, table: str) -> Set[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {str(row[1]) for row in rows}


def fetch_webui_data(days: int = 14) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    days = max(3, min(days, 60))
    now = utc_now()
    since_24h = now - timedelta(hours=24)
    day_buckets: Dict[str, int] = {}
    for offset in range(days - 1, -1, -1):
        key = (now - timedelta(days=offset)).date().isoformat()
        day_buckets[key] = 0

    overview: Dict[str, Any] = {
        "users_total": 0,
        "users_active_24h": None,
        "chats_total": 0,
        "chats_open": 0,
        "chats_24h": 0,
        "messages_total": None,
        "messages_24h": None,
        "source": {
            "kind": "openwebui_sqlite",
            "ok": True,
            "error": None,
        },
    }

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(WEBUI_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        with conn:
            if table_exists(conn, "user"):
                overview["users_total"] = int(conn.execute("SELECT COUNT(*) FROM user").fetchone()[0])
                user_cols = table_columns(conn, "user")
                user_ts_col = next(
                    (col for col in ("last_active_at", "updated_at", "created_at", "last_login_at") if col in user_cols),
                    None,
                )
                if user_ts_col:
                    active_count = 0
                    rows = conn.execute(f'SELECT "{user_ts_col}" AS ts FROM user').fetchall()
                    for row in rows:
                        ts = parse_db_timestamp(row["ts"])
                        if ts and ts >= since_24h:
                            active_count += 1
                    overview["users_active_24h"] = active_count

            if table_exists(conn, "chat"):
                chat_cols = table_columns(conn, "chat")
                overview["chats_total"] = int(conn.execute("SELECT COUNT(*) FROM chat").fetchone()[0])
                if "archived" in chat_cols:
                    rows = conn.execute('SELECT "archived" AS archived FROM chat').fetchall()
                    overview["chats_open"] = sum(1 for row in rows if not parse_boolish(row["archived"]))
                else:
                    overview["chats_open"] = overview["chats_total"]

                chat_ts_col = next((col for col in ("updated_at", "created_at") if col in chat_cols), None)
                if chat_ts_col:
                    rows = conn.execute(f'SELECT "{chat_ts_col}" AS ts FROM chat').fetchall()
                    for row in rows:
                        ts = parse_db_timestamp(row["ts"])
                        if not ts:
                            continue
                        if ts >= since_24h:
                            overview["chats_24h"] += 1
                        bucket = ts.date().isoformat()
                        if bucket in day_buckets:
                            day_buckets[bucket] += 1

            if table_exists(conn, "message"):
                msg_cols = table_columns(conn, "message")
                overview["messages_total"] = int(conn.execute("SELECT COUNT(*) FROM message").fetchone()[0])
                msg_ts_col = next((col for col in ("updated_at", "created_at") if col in msg_cols), None)
                if msg_ts_col:
                    count_24h = 0
                    rows = conn.execute(f'SELECT "{msg_ts_col}" AS ts FROM message').fetchall()
                    for row in rows:
                        ts = parse_db_timestamp(row["ts"])
                        if ts and ts >= since_24h:
                            count_24h += 1
                    overview["messages_24h"] = count_24h
    except Exception as exc:
        overview["source"]["ok"] = False
        overview["source"]["error"] = sanitize_public_error(exc)
        logger.warning("Open WebUI metrics unavailable: %s", exc)
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

    series = [{"date": date_key, "chats": value} for date_key, value in day_buckets.items()]
    return overview, series


def fetch_litellm_metrics() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "tokens_total": None,
        "tokens_24h": None,
        "requests_total": None,
        "source": {
            "kind": "litellm_metrics",
            "ok": False,
            "error": "metrics unavailable",
        },
    }
    headers: Dict[str, str] = {}
    if LITELLM_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_KEY}"

    try:
        resp = requests.get(f"{LITELLM_URL}/metrics", headers=headers, timeout=4)
        resp.raise_for_status()
        metrics_text = resp.text
    except Exception as exc:
        result["source"]["error"] = sanitize_public_error(exc)
        return result

    totals: Dict[str, float] = {
        "tokens": 0.0,
        "requests": 0.0,
        "in_tokens": 0.0,
        "out_tokens": 0.0,
    }
    found: Dict[str, bool] = {key: False for key in totals}
    for line in metrics_text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        metric_name = parts[0].split("{", 1)[0]
        try:
            value = float(parts[1])
        except ValueError:
            continue
        if metric_name in {"litellm_tokens_total", "litellm_total_tokens"}:
            totals["tokens"] += value
            found["tokens"] = True
        elif metric_name in {"litellm_input_tokens_total"}:
            totals["in_tokens"] += value
            found["in_tokens"] = True
        elif metric_name in {"litellm_output_tokens_total"}:
            totals["out_tokens"] += value
            found["out_tokens"] = True
        elif metric_name in {"litellm_requests_total", "litellm_total_requests"}:
            totals["requests"] += value
            found["requests"] = True

    if found["tokens"]:
        result["tokens_total"] = int(totals["tokens"])
    elif found["in_tokens"] or found["out_tokens"]:
        result["tokens_total"] = int(totals["in_tokens"] + totals["out_tokens"])

    if found["requests"]:
        result["requests_total"] = int(totals["requests"])

    if result["tokens_total"] is None and result["requests_total"] is None:
        result["source"]["ok"] = False
        result["source"]["error"] = "no token/request counters found in /metrics"
    else:
        result["source"]["ok"] = True
        result["source"]["error"] = None
    return result


def resolve_allowed_containers() -> Set[str]:
    allowed = set(ALLOWED_CONTAINERS)
    try:
        payload = switcher_get("/models")
        for item in payload.get("models", []):
            container_name = str(item.get("container") or "").strip()
            if container_name:
                allowed.add(container_name)
    except Exception:
        pass
    return allowed


def fetch_container_logs(container_name: str, tail: int) -> Dict[str, str]:
    r = requests.get(
        f"{DOCKER_PROXY_URL}/containers/json",
        params={"all": "true", "filters": json.dumps({"name": [container_name]})},
        timeout=10,
    )
    r.raise_for_status()
    containers = r.json()
    if not containers:
        return {"logs": f"(contenedor '{container_name}' no encontrado)\n", "container": container_name}

    container_id = containers[0]["Id"]
    log_r = requests.get(
        f"{DOCKER_PROXY_URL}/containers/{container_id}/logs",
        params={"stdout": "true", "stderr": "true", "tail": str(tail), "timestamps": "true"},
        timeout=20,
        stream=True,
    )
    log_r.raise_for_status()

    raw = log_r.content
    lines: List[str] = []
    i = 0
    while i < len(raw):
        if i + 8 > len(raw):
            break
        size = int.from_bytes(raw[i + 4 : i + 8], "big")
        i += 8
        if size > 0 and i + size <= len(raw):
            lines.append(raw[i : i + size].decode("utf-8", errors="replace"))
        i += size

    if not lines:
        lines = [raw.decode("utf-8", errors="replace")]

    return {"logs": "".join(lines), "container": container_name}


def choose_default_log_container(status_payload: Dict[str, Any], models_payload: Dict[str, Any]) -> str:
    mode = (status_payload.get("mode") or {}).get("active") or status_payload.get("active_mode")
    if mode == "comfy":
        return "comfyui"
    active_model = status_payload.get("active_model")
    if active_model:
        for model in models_payload.get("models", []):
            if model.get("id") == active_model and model.get("container"):
                return str(model["container"])
    return "litellm"


def build_ai_models_payload() -> Dict[str, Any]:
    status_payload = switcher_get("/status")
    models_payload = switcher_get("/models")

    runtime_containers = status_payload.get("containers") or {}
    running_ids = set(status_payload.get("running_models") or [])
    active_model = status_payload.get("active_model")
    active_mode = status_payload.get("active_mode")

    models: List[Dict[str, Any]] = []
    for model in models_payload.get("models", []):
        model_id = model.get("id")
        runtime = runtime_containers.get(model_id, {})
        models.append(
            {
                **model,
                "is_active": active_mode == "llm" and model_id == active_model,
                "is_running": model_id in running_ids,
                "runtime": runtime,
            }
        )

    return {
        "active_mode": active_mode,
        "active_model": active_model,
        "switch_in_progress": bool(status_payload.get("switch_in_progress")),
        "running_models": list(status_payload.get("running_models") or []),
        "mode": status_payload.get("mode"),
        "comfyui": status_payload.get("comfyui"),
        "switch": status_payload.get("switch"),
        "models": models,
    }


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/auth/login")
def login(req: LoginRequest, response: Response):
    user = verify_webui_credentials(req.email, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Credenciales inválidas o usuario sin permisos de admin")
    token = create_jwt(user)
    # Set cookie so the ComfyUI proxy can validate auth on browser navigation
    response.set_cookie(
        key="admin_jwt",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=JWT_EXPIRE_HOURS * 3600,
    )
    return {"token": token, "name": user["name"], "email": user["email"]}


@app.get("/auth/me")
def me(user: dict = Depends(get_current_user)):
    return {"name": user["name"], "email": user["sub"], "role": user["role"]}


# ---------------------------------------------------------------------------
# API proxy endpoints
# ---------------------------------------------------------------------------

@app.get("/api/status")
def api_status(user: dict = Depends(get_current_user)):
    try:
        return switcher_get("/mode")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/status/full")
def api_status_full(user: dict = Depends(get_current_user)):
    try:
        return switcher_get("/status")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/models")
def api_models(user: dict = Depends(get_current_user)):
    try:
        return switcher_get("/models")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/ai/models")
def api_ai_models(user: dict = Depends(get_current_user)):
    try:
        return build_ai_models_payload()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/data/overview")
def api_data_overview(user: dict = Depends(get_current_user)):
    webui_overview, _ = fetch_webui_data(days=14)
    litellm = fetch_litellm_metrics()
    return {
        "anonymous": True,
        "captured_at": utc_now().isoformat(),
        "metrics": {
            "users_total": webui_overview.get("users_total"),
            "users_active_24h": webui_overview.get("users_active_24h"),
            "chats_total": webui_overview.get("chats_total"),
            "chats_open": webui_overview.get("chats_open"),
            "chats_24h": webui_overview.get("chats_24h"),
            "messages_total": webui_overview.get("messages_total"),
            "messages_24h": webui_overview.get("messages_24h"),
            "tokens_total": litellm.get("tokens_total"),
            "tokens_24h": litellm.get("tokens_24h"),
            "requests_total": litellm.get("requests_total"),
        },
        "sources": {
            "openwebui": webui_overview.get("source"),
            "litellm": litellm.get("source"),
        },
    }


@app.get("/api/data/timeseries")
def api_data_timeseries(days: int = 14, user: dict = Depends(get_current_user)):
    _, chats_series = fetch_webui_data(days=days)
    return {
        "anonymous": True,
        "captured_at": utc_now().isoformat(),
        "series": {
            "chats": chats_series,
        },
    }


class ModeSwitchBody(BaseModel):
    mode: str
    model: Optional[str] = None
    ttl_minutes: Optional[int] = None
    wait_for_ready: bool = False


@app.post("/api/mode/switch")
def api_mode_switch(body: ModeSwitchBody, user: dict = Depends(get_current_user)):
    payload = {"mode": body.mode, "wait_for_ready": body.wait_for_ready}
    if body.model:
        payload["model"] = body.model
    if body.ttl_minutes is not None:
        payload["ttl_minutes"] = body.ttl_minutes
    try:
        return switcher_post("/mode/switch", payload, timeout=300)
    except requests.HTTPError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/mode/release")
def api_mode_release(user: dict = Depends(get_current_user)):
    try:
        return switcher_post("/mode/release", {}, timeout=300)
    except requests.HTTPError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/logs")
def api_logs_default(container: Optional[str] = None, tail: int = 200, user: dict = Depends(get_current_user)):
    try:
        status_payload = switcher_get("/status")
        models_payload = switcher_get("/models")
        selected = container or choose_default_log_container(status_payload, models_payload)
        allowed = resolve_allowed_containers()
        if selected not in allowed:
            raise HTTPException(status_code=400, detail=f"Contenedor no permitido: {selected}")
        return fetch_container_logs(selected, tail)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/logs/{container_name}")
def api_logs(container_name: str, tail: int = 200, user: dict = Depends(get_current_user)):
    allowed = resolve_allowed_containers()
    if container_name not in allowed:
        raise HTTPException(status_code=400, detail=f"Contenedor no permitido: {container_name}")
    try:
        return fetch_container_logs(container_name, tail)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ---------------------------------------------------------------------------
# SPA HTML
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin Panel — AI Server</title>
<style>
  :root {
    --bg: #0b1119;
    --surface: #121a26;
    --surface2: #1a2433;
    --border: #30363d;
    --text: #e6edf3;
    --text2: #8b949e;
    --accent: #58a6ff;
    --accent-hover: #79c0ff;
    --green: #3fb950;
    --red: #f85149;
    --orange: #d29922;
    --yellow: #e3b341;
    --purple: #bc8cff;
    --radius: 8px;
    --nav-w: 220px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background:
      radial-gradient(1200px 520px at 20% -10%, rgba(88,166,255,.14), rgba(11,17,25,0)),
      radial-gradient(1000px 520px at 90% -20%, rgba(63,185,80,.12), rgba(11,17,25,0)),
      var(--bg);
    color: var(--text);
    font-family: "Space Grotesk", "Avenir Next", "Segoe UI", sans-serif;
    min-height: 100vh;
  }

  /* ── Login ── */
  #login-screen {
    display: flex; align-items: center; justify-content: center; min-height: 100vh;
  }
  .login-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 40px; width: 360px;
  }
  .login-card h1 { font-size: 1.4rem; margin-bottom: 4px; }
  .login-card p { color: var(--text2); font-size: .85rem; margin-bottom: 28px; }
  .form-group { margin-bottom: 16px; }
  .form-group label { display: block; font-size: .8rem; color: var(--text2); margin-bottom: 6px; text-transform: uppercase; letter-spacing: .05em; }
  .form-group input {
    width: 100%; background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); padding: 10px 12px; font-size: .95rem;
  }
  .form-group input:focus { outline: none; border-color: var(--accent); }
  .btn {
    display: inline-flex; align-items: center; gap: 6px; padding: 9px 18px;
    border-radius: 6px; border: none; cursor: pointer; font-size: .9rem; font-weight: 500;
    transition: opacity .15s;
  }
  .btn:hover { opacity: .85; }
  .btn:disabled { opacity: .45; cursor: not-allowed; }
  .btn-primary { background: var(--accent); color: #000; }
  .btn-danger  { background: var(--red); color: #fff; }
  .btn-success { background: var(--green); color: #000; }
  .btn-ghost   { background: var(--surface2); color: var(--text); border: 1px solid var(--border); }
  .btn-full    { width: 100%; justify-content: center; }
  .error-msg   { color: var(--red); font-size: .85rem; margin-top: 12px; text-align: center; min-height: 20px; }

  /* ── App Shell ── */
  #app { display: none; min-height: 100vh; flex-direction: column; }
  header {
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 0 24px; height: 56px; display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; z-index: 10;
  }
  .header-left { display: flex; align-items: center; gap: 12px; }
  .header-logo { font-size: 1.1rem; font-weight: 700; letter-spacing: -.01em; }
  .header-logo span { color: var(--accent); }
  .system-badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: var(--surface2); border: 1px solid var(--border);
    border-radius: 20px; padding: 4px 12px; font-size: .8rem;
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--text2); }
  .dot.green { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot.red { background: var(--red); box-shadow: 0 0 6px var(--red); }
  .dot.orange { background: var(--orange); box-shadow: 0 0 6px var(--orange); animation: pulse .8s ease-in-out infinite alternate; }
  @keyframes pulse { from { opacity: .6; } to { opacity: 1; } }
  .header-right { display: flex; align-items: center; gap: 12px; }
  .user-name { font-size: .85rem; color: var(--text2); }
  .lang-select {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); padding: 6px 10px; font-size: .8rem;
  }

  .shell { display: flex; flex: 1; }

  /* ── Sidebar ── */
  nav {
    width: var(--nav-w); background: var(--surface); border-right: 1px solid var(--border);
    padding: 16px 0; flex-shrink: 0; position: sticky; top: 56px; height: calc(100vh - 56px); overflow-y: auto;
  }
  .nav-section { padding: 0 12px; margin-bottom: 8px; }
  .nav-section-label { font-size: .7rem; text-transform: uppercase; letter-spacing: .08em; color: var(--text2); padding: 4px 8px; }
  .nav-item {
    display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-radius: 6px;
    cursor: pointer; font-size: .9rem; color: var(--text2); transition: all .15s;
  }
  .nav-item:hover { background: var(--surface2); color: var(--text); }
  .nav-item.active { background: rgba(88,166,255,.12); color: var(--accent); font-weight: 500; }
  .nav-icon { font-size: 1.1rem; width: 20px; text-align: center; }

  /* ── Main ── */
  main { flex: 1; padding: 28px; max-width: 900px; }
  .section { display: none; }
  .section.active { display: block; }
  h2 { font-size: 1.2rem; font-weight: 600; margin-bottom: 20px; }
  .section-note { color: var(--text2); font-size: .86rem; margin: -8px 0 18px; }
  .stack { display: grid; gap: 16px; }
  .split-grid { display: grid; gap: 16px; grid-template-columns: 1.2fr .8fr; }

  /* ── Cards grid ── */
  .cards { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); margin-bottom: 24px; }
  .card {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 18px;
  }
  .card-label { font-size: .75rem; text-transform: uppercase; letter-spacing: .07em; color: var(--text2); margin-bottom: 8px; }
  .card-value { font-size: 1.5rem; font-weight: 700; }
  .card-sub { font-size: .8rem; color: var(--text2); margin-top: 4px; }
  .card.highlight { border-color: var(--accent); background: rgba(88,166,255,.06); }

  /* ── Model cards ── */
  .model-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }
  .model-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 18px; display: flex; flex-direction: column; gap: 10px;
  }
  .model-card.active-model { border-color: var(--green); background: rgba(63,185,80,.06); }
  .model-card.comfy-active { border-color: var(--purple); background: rgba(188,140,255,.08); }
  .model-card-name { font-weight: 600; font-size: 1rem; }
  .model-card-meta { font-size: .8rem; color: var(--text2); }
  .model-actions { display: flex; gap: 8px; flex-wrap: wrap; }
  .status-chip {
    display: inline-flex; align-items: center; gap: 5px;
    border-radius: 20px; padding: 3px 10px; font-size: .75rem; font-weight: 500;
  }
  .chip-running { background: rgba(63,185,80,.15); color: var(--green); }
  .chip-stopped { background: rgba(139,148,158,.12); color: var(--text2); }
  .chip-loading { background: rgba(210,153,34,.15); color: var(--orange); }
  .chip-comfy   { background: rgba(188,140,255,.15); color: var(--purple); }
  .chip-error   { background: rgba(248,81,73,.15); color: var(--red); }

  /* ── ComfyUI section ── */
  .comfy-box {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 24px; margin-bottom: 20px;
  }
  .comfy-status-row { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
  .comfy-status-label { font-size: 1rem; color: var(--text2); }
  .comfy-controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
  .ttl-select {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); padding: 8px 12px; font-size: .9rem;
  }
  .comfy-link {
    display: inline-flex; align-items: center; gap: 6px;
    color: var(--accent); text-decoration: none; font-size: .9rem;
    background: rgba(88,166,255,.1); border: 1px solid rgba(88,166,255,.3);
    border-radius: 6px; padding: 8px 14px;
  }
  .comfy-link:hover { background: rgba(88,166,255,.2); }
  .model-return-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .model-select {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); padding: 8px 12px; font-size: .9rem;
  }

  /* ── Logs section ── */
  .logs-toolbar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }
  .container-select {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); padding: 8px 12px; font-size: .9rem;
  }
  .auto-label { font-size: .85rem; color: var(--text2); display: flex; align-items: center; gap: 6px; }
  .log-box {
    background: #010409; border: 1px solid var(--border); border-radius: var(--radius);
    padding: 16px; height: 360px; overflow-y: auto; font-family: "IBM Plex Mono", "Menlo", "Monaco", "Courier New", monospace;
    font-size: .78rem; line-height: 1.55; white-space: pre-wrap; word-break: break-all;
  }
  .log-err  { color: #f85149; }
  .log-warn { color: #e3b341; }
  .log-info { color: #8b949e; }

  .chart-grid { display: grid; gap: 12px; }
  .chart-row { display: grid; grid-template-columns: 94px 1fr 50px; align-items: center; gap: 10px; }
  .chart-date { color: var(--text2); font-size: .76rem; }
  .bar-track { width: 100%; background: rgba(139,148,158,.18); border-radius: 999px; height: 8px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, #3fb950, #58a6ff); }
  .chart-val { text-align: right; font-size: .82rem; color: var(--text2); }
  .source-list { display: grid; gap: 8px; margin-top: 10px; }
  .source-item { font-size: .8rem; color: var(--text2); }

  /* ── Toast ── */
  #toast {
    position: fixed; bottom: 24px; right: 24px; z-index: 999;
    background: var(--surface2); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 12px 20px; font-size: .9rem; display: none;
    animation: fadeIn .2s ease;
  }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
  #toast.ok   { border-color: var(--green); color: var(--green); }
  #toast.err  { border-color: var(--red); color: var(--red); }
  #toast.info { border-color: var(--accent); color: var(--accent); }

  /* ── Switch progress ── */
  .switch-progress {
    background: var(--surface); border: 1px solid var(--orange); border-radius: var(--radius);
    padding: 16px; margin-bottom: 20px; display: none;
  }
  .switch-progress.visible { display: block; }
  .progress-title { font-size: .9rem; color: var(--orange); margin-bottom: 10px; font-weight: 600; }
  .steps-list { list-style: none; }
  .step-item { font-size: .8rem; color: var(--text2); padding: 2px 0; display: flex; align-items: center; gap: 6px; }
  .step-ok   { color: var(--green); }
  .step-fail { color: var(--red); }
  .step-cur  { color: var(--orange); }

  /* ── Responsive ── */
  @media (max-width: 700px) {
    nav { width: 60px; }
    .nav-section-label, .nav-item span { display: none; }
    .nav-item { justify-content: center; }
    main { padding: 16px; }
    .split-grid { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>

<!-- ═══════════════════════════════ LOGIN ═══════════════════════════════ -->
<div id="login-screen">
  <div class="login-card">
    <h1 id="login-title">🛡️ Admin Panel</h1>
    <p id="login-subtitle">Usa tus credenciales de Open WebUI (solo admins)</p>
    <div class="form-group">
      <label id="login-email-label">Email</label>
      <input type="email" id="login-email" placeholder="admin@ejemplo.com" autocomplete="email">
    </div>
    <div class="form-group">
      <label id="login-pass-label">Contraseña</label>
      <input type="password" id="login-password" placeholder="••••••••" autocomplete="current-password">
    </div>
    <button class="btn btn-primary btn-full" id="login-btn" onclick="doLogin()">Entrar</button>
    <div class="error-msg" id="login-error"></div>
  </div>
</div>

<!-- ═══════════════════════════════ APP ═══════════════════════════════ -->
<div id="app">
  <header>
    <div class="header-left">
      <div class="header-logo">AI <span>Server</span></div>
      <div class="system-badge">
        <div class="dot" id="sys-dot"></div>
        <span id="sys-label">cargando…</span>
      </div>
    </div>
    <div class="header-right">
      <select id="lang-select" class="lang-select" aria-label="Idioma">
        <option value="es">ES</option>
        <option value="ca">CA</option>
        <option value="en">EN</option>
      </select>
      <span class="user-name" id="user-name"></span>
      <button class="btn btn-ghost" id="logout-btn" onclick="doLogout()" style="padding:6px 12px;font-size:.8rem">Salir</button>
    </div>
  </header>

  <div class="shell">
    <nav>
      <div class="nav-section">
        <div class="nav-section-label" id="nav-panel-label">Panel</div>
        <div class="nav-item active" onclick="showSection('estado', this)">
          <span class="nav-icon">📊</span><span id="nav-estado">Estado</span>
        </div>
        <div class="nav-item" onclick="showSection('modelos', this)">
          <span class="nav-icon">🧠</span><span id="nav-modelos">Modelos IA</span>
        </div>
        <div class="nav-item" onclick="showSection('data', this)">
          <span class="nav-icon">📈</span><span id="nav-data">Data</span>
        </div>
      </div>
    </nav>

    <main>
      <!-- ── Estado ── -->
      <div class="section active" id="sec-estado">
        <h2 id="estado-title">Estado del sistema</h2>
        <div class="section-note" id="estado-note">Vista operativa en tiempo real con estado, progreso de switch y logs.</div>
        <div class="cards" id="status-cards">
          <div class="card"><div class="card-label" id="card-mode-label">Modo</div><div class="card-value" id="card-mode">—</div></div>
          <div class="card"><div class="card-label" id="card-model-label">Modelo activo</div><div class="card-value" id="card-model">—</div></div>
          <div class="card"><div class="card-label" id="card-comfy-label">ComfyUI</div><div class="card-value" id="card-comfy">—</div></div>
          <div class="card"><div class="card-label" id="card-ttl-label">Tiempo restante</div><div class="card-value" id="card-ttl">—</div></div>
        </div>

        <div class="switch-progress" id="switch-progress">
          <div class="progress-title" id="switch-progress-title">Cambiando modo…</div>
          <ul class="steps-list" id="switch-steps"></ul>
        </div>

        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">
          <button class="btn btn-ghost" id="refresh-btn" onclick="refreshAll()">↻ Refrescar</button>
        </div>
        <div style="font-size:.75rem;color:var(--text2);margin-bottom:16px" id="last-updated"></div>

        <div class="stack">
          <div class="card">
            <div class="card-label" id="logs-live-label">Logs en vivo</div>
            <div class="logs-toolbar">
              <select class="container-select" id="log-container-select"></select>
              <button class="btn btn-ghost" id="load-logs-btn" onclick="fetchLogs()">↻ Cargar logs</button>
              <label class="auto-label">
                <input type="checkbox" id="log-auto" onchange="toggleAutoLogs()">
                <span id="logs-auto-label">Auto (10s)</span>
              </label>
            </div>
            <div class="log-box" id="log-box">Selecciona un contenedor y pulsa "Cargar logs"</div>
          </div>
        </div>
      </div>

      <!-- ── Modelos IA ── -->
      <div class="section" id="sec-modelos">
        <h2 id="modelos-title">Modelos IA</h2>
        <div class="section-note" id="modelos-note">Control unificado de modelos LLM y sesión temporal de ComfyUI.</div>
        <div class="model-grid" id="model-grid"></div>
      </div>

      <!-- ── Data ── -->
      <div class="section" id="sec-data">
        <h2 id="data-title">Data (anónima)</h2>
        <div class="section-note" id="data-note">Solo métricas agregadas del sistema. No se muestran prompts ni contenido de usuario.</div>
        <div class="cards">
          <div class="card"><div class="card-label" id="data-tokens-label">Tokens totales</div><div class="card-value" id="data-tokens">—</div><div class="card-sub" id="data-tokens-sub"></div></div>
          <div class="card"><div class="card-label" id="data-chats-open-label">Chats abiertos</div><div class="card-value" id="data-chats-open">—</div><div class="card-sub" id="data-chats-sub"></div></div>
          <div class="card"><div class="card-label" id="data-users-label">Usuarios</div><div class="card-value" id="data-users">—</div><div class="card-sub" id="data-users-sub"></div></div>
          <div class="card"><div class="card-label" id="data-messages-label">Mensajes</div><div class="card-value" id="data-messages">—</div><div class="card-sub" id="data-msg-sub"></div></div>
        </div>
        <div class="split-grid">
          <div class="card">
            <div class="card-label" id="data-chats-series-label">Actividad de chats (14d)</div>
            <div class="chart-grid" id="chat-series"></div>
          </div>
          <div class="card">
            <div class="card-label" id="data-sources-label">Fuentes de datos</div>
            <div class="source-list" id="data-sources"></div>
          </div>
        </div>
      </div>
    </main>
  </div>
</div>

<div id="toast"></div>

<script>
const MODEL_INFO = {
  'qwen-fast':    {label:'Qwen 2.5 7B',     vram:'~13 GB (55%)'},
  'qwen-quality': {label:'Qwen 2.5 14B',    vram:'~20 GB (85%)'},
  'deepseek':     {label:'DeepSeek-R1 14B', vram:'~21 GB (95%)'},
  'qwen-max':     {label:'Qwen 2.5 32B',    vram:'~21 GB (95%)'},
};

const I18N = {
  es: {
    title: 'Admin Panel — AI Server',
    login_title: '🛡️ Admin Panel',
    login_subtitle: 'Usa tus credenciales de Open WebUI (solo admins)',
    email: 'Email',
    password: 'Contraseña',
    login_button: 'Entrar',
    login_loading: 'Entrando…',
    logout: 'Salir',
    panel: 'Panel',
    loading: 'cargando…',
    nav_estado: 'Estado',
    nav_modelos: 'Modelos IA',
    nav_data: 'Data',
    estado_title: 'Estado del sistema',
    estado_note: 'Vista operativa en tiempo real con estado, progreso de switch y logs.',
    mode: 'Modo',
    active_model: 'Modelo activo',
    comfyui: 'ComfyUI',
    ttl_remaining: 'Tiempo restante',
    refresh: '↻ Refrescar',
    logs_live: 'Logs en vivo',
    load_logs: '↻ Cargar logs',
    logs_auto: 'Auto (10s)',
    logs_placeholder: 'Selecciona un contenedor y pulsa "Cargar logs"',
    modelos_title: 'Modelos IA',
    modelos_note: 'Control unificado de modelos LLM y sesión temporal de ComfyUI.',
    data_title: 'Data (anónima)',
    data_note: 'Solo métricas agregadas del sistema. No se muestran prompts ni contenido de usuario.',
    tokens_total: 'Tokens totales',
    chats_open: 'Chats abiertos',
    users: 'Usuarios',
    messages: 'Mensajes',
    chats_activity: 'Actividad de chats (14d)',
    data_sources: 'Fuentes de datos',
    session_expired: 'Sesión expirada',
    auth_error: 'Error de autenticación',
    no_connection: 'Sin conexión',
    switching: 'Cambiando…',
    comfy_active_badge: 'ComfyUI activo',
    llm_badge: 'LLM · {model}',
    no_model_badge: 'Sin modelo',
    comfy_active_short: '▶ Activo',
    comfy_inactive_short: '⏹ Inactivo',
    updated_at: 'Actualizado: {time}',
    model_dynamic: 'dinámico',
    repo: 'repo',
    model_active_chip: '● Activo',
    model_error_chip: '● Error',
    model_running_chip: '● Ejecutando',
    model_prepared_chip: '○ Preparado',
    model_stopped_chip: '○ Detenido',
    model_activate: 'Activar',
    model_is_active: '✓ Activo',
    open_ui: '🌐 Abrir UI',
    no_models: 'No hay modelos disponibles.',
    llm_none: 'No hay modelos LLM registrados.',
    comfy_card_title: 'ComfyUI',
    comfy_card_subtitle: 'Generación de imagen (sesión temporal)',
    comfy_activate: '🎨 Activar ComfyUI',
    comfy_switch_wait: 'Esperando transición de modo…',
    comfy_open: '🔗 Abrir ComfyUI → {url}',
    comfy_available_until: 'Disponible hasta: {time}',
    comfy_return_to_llm: 'Volver a LLM:',
    comfy_return: '✅ Volver a LLM',
    comfy_preempt: '⚡ Preemption urgente',
    comfy_preempt_title: 'Fuerza el retorno inmediato a LLM',
    data_not_available: 'N/D',
    data_24h_na: '24h: no disponible',
    data_24h_value: '24h: {count}',
    data_total_24h: 'Total: {total} · 24h: {last24h}',
    data_active_24h: 'Activos 24h: {count}',
    data_msg_24h_req: '24h: {last24h} · Requests: {req}',
    data_no_series: 'Sin datos de serie temporal.',
    source_ok: 'ok',
    source_degraded: 'degradado',
    source_check_logs: 'revisa logs',
    logs_loading: 'Cargando…',
    logs_empty: '(sin output)',
    logs_error: 'Error: {error}',
    mode_not_available: '—',
    ttl_minutes: '{minutes} min',
    ttl_none: '—',
    unknown: '—',
    toast_models_load_error: 'No se pudo cargar modelos IA: {error}',
    toast_data_load_error: 'No se pudo cargar Data: {error}',
    toast_switch_start: 'Iniciando cambio a {model}…',
    toast_switch_started: 'Cambio iniciado → {model}',
    toast_activate_comfy: 'Activando ComfyUI ({ttl} min)…',
    toast_activate_comfy_started: 'ComfyUI activándose…',
    toast_deactivate_comfy: 'Volviendo a LLM ({model})…',
    toast_deactivate_comfy_started: 'Retornando a LLM…',
    toast_preempt: 'Preemption: forzando retorno a LLM…',
    toast_preempt_started: 'Preemption iniciado',
    ttl_control_unavailable: 'Control TTL no disponible',
    return_selector_unavailable: 'Selector de modelo no disponible',
  },
  ca: {
    title: 'Panell Admin — AI Server',
    login_title: '🛡️ Panell Admin',
    login_subtitle: 'Utilitza les teves credencials d’Open WebUI (només admins)',
    email: 'Correu',
    password: 'Contrasenya',
    login_button: 'Entrar',
    login_loading: 'Entrant…',
    logout: 'Sortir',
    panel: 'Panell',
    loading: 'carregant…',
    nav_estado: 'Estat',
    nav_modelos: 'Models IA',
    nav_data: 'Dades',
    estado_title: 'Estat del sistema',
    estado_note: 'Vista operativa en temps real amb estat, progrés de canvi i logs.',
    mode: 'Mode',
    active_model: 'Model actiu',
    comfyui: 'ComfyUI',
    ttl_remaining: 'Temps restant',
    refresh: '↻ Refrescar',
    logs_live: 'Logs en viu',
    load_logs: '↻ Carregar logs',
    logs_auto: 'Auto (10s)',
    logs_placeholder: 'Selecciona un contenidor i prem "Carregar logs"',
    modelos_title: 'Models IA',
    modelos_note: 'Control unificat de models LLM i sessió temporal de ComfyUI.',
    data_title: 'Dades (anònimes)',
    data_note: 'Només mètriques agregades del sistema. No es mostren prompts ni contingut d’usuari.',
    tokens_total: 'Tokens totals',
    chats_open: 'Xats oberts',
    users: 'Usuaris',
    messages: 'Missatges',
    chats_activity: 'Activitat de xats (14d)',
    data_sources: 'Fonts de dades',
    session_expired: 'Sessió caducada',
    auth_error: 'Error d’autenticació',
    no_connection: 'Sense connexió',
    switching: 'Canviant…',
    comfy_active_badge: 'ComfyUI actiu',
    llm_badge: 'LLM · {model}',
    no_model_badge: 'Sense model',
    comfy_active_short: '▶ Actiu',
    comfy_inactive_short: '⏹ Inactiu',
    updated_at: 'Actualitzat: {time}',
    model_dynamic: 'dinàmic',
    repo: 'repo',
    model_active_chip: '● Actiu',
    model_error_chip: '● Error',
    model_running_chip: '● Executant',
    model_prepared_chip: '○ Preparat',
    model_stopped_chip: '○ Aturat',
    model_activate: 'Activar',
    model_is_active: '✓ Actiu',
    open_ui: '🌐 Obrir UI',
    no_models: 'No hi ha models disponibles.',
    llm_none: 'No hi ha models LLM registrats.',
    comfy_card_title: 'ComfyUI',
    comfy_card_subtitle: 'Generació d’imatge (sessió temporal)',
    comfy_activate: '🎨 Activar ComfyUI',
    comfy_switch_wait: 'Esperant transició de mode…',
    comfy_open: '🔗 Obrir ComfyUI → {url}',
    comfy_available_until: 'Disponible fins a: {time}',
    comfy_return_to_llm: 'Tornar a LLM:',
    comfy_return: '✅ Tornar a LLM',
    comfy_preempt: '⚡ Preemption urgent',
    comfy_preempt_title: 'Força el retorn immediat a LLM',
    data_not_available: 'N/D',
    data_24h_na: '24h: no disponible',
    data_24h_value: '24h: {count}',
    data_total_24h: 'Total: {total} · 24h: {last24h}',
    data_active_24h: 'Actius 24h: {count}',
    data_msg_24h_req: '24h: {last24h} · Requests: {req}',
    data_no_series: 'Sense dades de sèrie temporal.',
    source_ok: 'ok',
    source_degraded: 'degradat',
    source_check_logs: 'revisa logs',
    logs_loading: 'Carregant…',
    logs_empty: '(sense sortida)',
    logs_error: 'Error: {error}',
    mode_not_available: '—',
    ttl_minutes: '{minutes} min',
    ttl_none: '—',
    unknown: '—',
    toast_models_load_error: 'No s’han pogut carregar els models IA: {error}',
    toast_data_load_error: 'No s’han pogut carregar les dades: {error}',
    toast_switch_start: 'Iniciant canvi a {model}…',
    toast_switch_started: 'Canvi iniciat → {model}',
    toast_activate_comfy: 'Activant ComfyUI ({ttl} min)…',
    toast_activate_comfy_started: 'ComfyUI activant-se…',
    toast_deactivate_comfy: 'Tornant a LLM ({model})…',
    toast_deactivate_comfy_started: 'Tornant a LLM…',
    toast_preempt: 'Preemption: forçant retorn a LLM…',
    toast_preempt_started: 'Preemption iniciat',
    ttl_control_unavailable: 'Control TTL no disponible',
    return_selector_unavailable: 'Selector de model no disponible',
  },
  en: {
    title: 'Admin Panel — AI Server',
    login_title: '🛡️ Admin Panel',
    login_subtitle: 'Use your Open WebUI credentials (admins only)',
    email: 'Email',
    password: 'Password',
    login_button: 'Sign in',
    login_loading: 'Signing in…',
    logout: 'Logout',
    panel: 'Panel',
    loading: 'loading…',
    nav_estado: 'Status',
    nav_modelos: 'AI Models',
    nav_data: 'Data',
    estado_title: 'System status',
    estado_note: 'Real-time operations view with status, switch progress, and logs.',
    mode: 'Mode',
    active_model: 'Active model',
    comfyui: 'ComfyUI',
    ttl_remaining: 'Time left',
    refresh: '↻ Refresh',
    logs_live: 'Live logs',
    load_logs: '↻ Load logs',
    logs_auto: 'Auto (10s)',
    logs_placeholder: 'Select a container and click "Load logs"',
    modelos_title: 'AI Models',
    modelos_note: 'Unified control for LLM models and temporary ComfyUI session.',
    data_title: 'Data (anonymous)',
    data_note: 'Only aggregated system metrics. No prompts or user content are shown.',
    tokens_total: 'Total tokens',
    chats_open: 'Open chats',
    users: 'Users',
    messages: 'Messages',
    chats_activity: 'Chat activity (14d)',
    data_sources: 'Data sources',
    session_expired: 'Session expired',
    auth_error: 'Authentication error',
    no_connection: 'No connection',
    switching: 'Switching…',
    comfy_active_badge: 'ComfyUI active',
    llm_badge: 'LLM · {model}',
    no_model_badge: 'No model',
    comfy_active_short: '▶ Active',
    comfy_inactive_short: '⏹ Inactive',
    updated_at: 'Updated: {time}',
    model_dynamic: 'dynamic',
    repo: 'repo',
    model_active_chip: '● Active',
    model_error_chip: '● Error',
    model_running_chip: '● Running',
    model_prepared_chip: '○ Ready',
    model_stopped_chip: '○ Stopped',
    model_activate: 'Activate',
    model_is_active: '✓ Active',
    open_ui: '🌐 Open UI',
    no_models: 'No models available.',
    llm_none: 'No LLM models registered.',
    comfy_card_title: 'ComfyUI',
    comfy_card_subtitle: 'Image generation (temporary session)',
    comfy_activate: '🎨 Activate ComfyUI',
    comfy_switch_wait: 'Waiting for mode transition…',
    comfy_open: '🔗 Open ComfyUI → {url}',
    comfy_available_until: 'Available until: {time}',
    comfy_return_to_llm: 'Back to LLM:',
    comfy_return: '✅ Back to LLM',
    comfy_preempt: '⚡ Urgent preemption',
    comfy_preempt_title: 'Force immediate return to LLM',
    data_not_available: 'N/A',
    data_24h_na: '24h: unavailable',
    data_24h_value: '24h: {count}',
    data_total_24h: 'Total: {total} · 24h: {last24h}',
    data_active_24h: 'Active 24h: {count}',
    data_msg_24h_req: '24h: {last24h} · Requests: {req}',
    data_no_series: 'No time-series data.',
    source_ok: 'ok',
    source_degraded: 'degraded',
    source_check_logs: 'check logs',
    logs_loading: 'Loading…',
    logs_empty: '(no output)',
    logs_error: 'Error: {error}',
    mode_not_available: '—',
    ttl_minutes: '{minutes} min',
    ttl_none: '—',
    unknown: '—',
    toast_models_load_error: 'Could not load AI models: {error}',
    toast_data_load_error: 'Could not load data: {error}',
    toast_switch_start: 'Starting switch to {model}…',
    toast_switch_started: 'Switch started → {model}',
    toast_activate_comfy: 'Activating ComfyUI ({ttl} min)…',
    toast_activate_comfy_started: 'ComfyUI is starting…',
    toast_deactivate_comfy: 'Returning to LLM ({model})…',
    toast_deactivate_comfy_started: 'Returning to LLM…',
    toast_preempt: 'Preemption: forcing return to LLM…',
    toast_preempt_started: 'Preemption started',
    ttl_control_unavailable: 'TTL control is unavailable',
    return_selector_unavailable: 'Model selector is unavailable',
  },
};

let LANG = localStorage.getItem('admin_lang') || 'es';

function t(key, vars = {}) {
  const dict = I18N[LANG] || I18N.es;
  let text = dict[key] || I18N.es[key] || key;
  Object.entries(vars).forEach(([k, v]) => {
    text = text.replaceAll(`{${k}}`, String(v));
  });
  return text;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function applyI18nStatic() {
  document.title = t('title');
  setText('login-title', t('login_title'));
  setText('login-subtitle', t('login_subtitle'));
  setText('login-email-label', t('email'));
  setText('login-pass-label', t('password'));
  const loginBtn = document.getElementById('login-btn');
  if (loginBtn && !loginBtn.disabled) loginBtn.textContent = t('login_button');
  setText('logout-btn', t('logout'));
  setText('nav-panel-label', t('panel'));
  setText('nav-estado', t('nav_estado'));
  setText('nav-modelos', t('nav_modelos'));
  setText('nav-data', t('nav_data'));
  setText('estado-title', t('estado_title'));
  setText('estado-note', t('estado_note'));
  setText('card-mode-label', t('mode'));
  setText('card-model-label', t('active_model'));
  setText('card-comfy-label', t('comfyui'));
  setText('card-ttl-label', t('ttl_remaining'));
  setText('refresh-btn', t('refresh'));
  setText('logs-live-label', t('logs_live'));
  setText('load-logs-btn', t('load_logs'));
  setText('logs-auto-label', t('logs_auto'));
  setText('modelos-title', t('modelos_title'));
  setText('modelos-note', t('modelos_note'));
  setText('data-title', t('data_title'));
  setText('data-note', t('data_note'));
  setText('data-tokens-label', t('tokens_total'));
  setText('data-chats-open-label', t('chats_open'));
  setText('data-users-label', t('users'));
  setText('data-messages-label', t('messages'));
  setText('data-chats-series-label', t('chats_activity'));
  setText('data-sources-label', t('data_sources'));
  setText('sys-label', t('loading'));

  const emailInput = document.getElementById('login-email');
  if (emailInput) emailInput.placeholder = 'admin@example.com';
  const passInput = document.getElementById('login-password');
  if (passInput) passInput.placeholder = '••••••••';
  const logBox = document.getElementById('log-box');
  if (logBox && !logsBootstrapped) logBox.textContent = t('logs_placeholder');
  setText('switch-progress-title', t('switching'));
}

function setLanguage(nextLang) {
  LANG = ['es', 'ca', 'en'].includes(nextLang) ? nextLang : 'es';
  localStorage.setItem('admin_lang', LANG);
  const select = document.getElementById('lang-select');
  if (select && select.value !== LANG) select.value = LANG;
  applyI18nStatic();
  if (statusData) renderStatus(statusData);
  if (aiModelsData || statusData) {
    renderModelGrid();
    syncReturnModelSelect();
  }
  if (dataOverview || dataSeries) renderData();
}

let TOKEN = localStorage.getItem('admin_jwt') || '';
let statusData = null;
let aiModelsData = null;
let dataOverview = null;
let dataSeries = null;

let statusTimer = null;
let modelsTimer = null;
let dataTimer = null;
let logTimer = null;
let logsBootstrapped = false;
let pendingModeTarget = null;

window.addEventListener('DOMContentLoaded', () => {
  const langSelect = document.getElementById('lang-select');
  if (langSelect) {
    langSelect.value = LANG;
    langSelect.addEventListener('change', e => setLanguage(e.target.value));
  }
  setLanguage(LANG);
  if (TOKEN) tryAutoLogin();
  document.getElementById('login-password').addEventListener('keydown', e => {
    if (e.key === 'Enter') doLogin();
  });
});

async function tryAutoLogin() {
  try {
    const me = await apiFetch('/auth/me');
    showApp(me.name);
  } catch {
    TOKEN = '';
    localStorage.removeItem('admin_jwt');
  }
}

async function doLogin() {
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  const errorEl = document.getElementById('login-error');
  const button = document.getElementById('login-btn');

  errorEl.textContent = '';
  button.disabled = true;
  button.textContent = t('login_loading');
  try {
    const r = await fetch('/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, password}),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || t('auth_error'));
    TOKEN = data.token;
    localStorage.setItem('admin_jwt', TOKEN);
    showApp(data.name);
  } catch (e) {
    errorEl.textContent = e.message;
  } finally {
    button.disabled = false;
    button.textContent = t('login_button');
  }
}

function doLogout() {
  TOKEN = '';
  localStorage.removeItem('admin_jwt');
  clearInterval(statusTimer);
  clearInterval(modelsTimer);
  clearInterval(dataTimer);
  clearInterval(logTimer);
  statusTimer = null;
  modelsTimer = null;
  dataTimer = null;
  logTimer = null;
  logsBootstrapped = false;
  pendingModeTarget = null;
  document.getElementById('app').style.display = 'none';
  document.getElementById('login-screen').style.display = 'flex';
}

function showApp(name) {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app').style.display = 'flex';
  document.getElementById('user-name').textContent = name;
  refreshAll();
  statusTimer = setInterval(refreshStatus, 5000);
  modelsTimer = setInterval(refreshModels, 15000);
  dataTimer = setInterval(refreshData, 60000);
}

function showSection(id, navEl) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('sec-' + id).classList.add('active');
  if (navEl) navEl.classList.add('active');
}

async function apiFetch(path, opts = {}) {
  const headers = {'Authorization': 'Bearer ' + TOKEN, ...(opts.headers || {})};
  const r = await fetch(path, {...opts, headers});
  if (r.status === 401) {
    doLogout();
    throw new Error(t('session_expired'));
  }
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

async function refreshAll() {
  await Promise.allSettled([refreshStatus(), refreshModels(), refreshData()]);
}

async function refreshStatus() {
  try {
    const data = await apiFetch('/api/status/full');
    statusData = data;
    if (!data.switch_in_progress) pendingModeTarget = null;
    renderStatus(data);
    ensureLogContainerOptions();
    if (!logsBootstrapped) {
      logsBootstrapped = true;
      fetchLogs();
    }
  } catch {
    setSysBadge('red', t('no_connection'));
  }
}

async function refreshModels() {
  try {
    aiModelsData = await apiFetch('/api/ai/models');
    renderModelGrid();
    syncReturnModelSelect();
    ensureLogContainerOptions();
  } catch (e) {
    showToast('err', t('toast_models_load_error', {error: e.message}));
  }
}

async function refreshData() {
  try {
    const [overview, series] = await Promise.all([
      apiFetch('/api/data/overview'),
      apiFetch('/api/data/timeseries?days=14'),
    ]);
    dataOverview = overview;
    dataSeries = series;
    renderData();
  } catch (e) {
    showToast('err', t('toast_data_load_error', {error: e.message}));
  }
}

function renderStatus(d) {
  const mode = d.mode?.active || d.active_mode || '—';
  const model = d.active_model || d.running_models?.[0] || '—';
  const comfyStatus = d.comfyui?.status || '—';
  const lease = d.mode?.lease;
  const switchInProgress = Boolean(d.switch_in_progress);

  if (switchInProgress) {
    setSysBadge('orange', t('switching'));
  } else if (mode === 'comfy') {
    setSysBadge('orange', t('comfy_active_badge'));
  } else if (mode === 'llm' && model && model !== '—') {
    setSysBadge('green', t('llm_badge', {model}));
  } else {
    setSysBadge('red', t('no_model_badge'));
  }

  document.getElementById('card-mode').textContent = String(mode).toUpperCase();
  document.getElementById('card-model').textContent = model;
  document.getElementById('card-comfy').textContent = comfyStatus === 'running' ? t('comfy_active_short') : t('comfy_inactive_short');
  document.getElementById('card-comfy').style.color = comfyStatus === 'running' ? 'var(--green)' : 'var(--text2)';

  if (lease?.remaining_seconds && !lease.expired) {
    const min = Math.ceil(lease.remaining_seconds / 60);
    document.getElementById('card-ttl').textContent = t('ttl_minutes', {minutes: min});
    document.getElementById('card-ttl').style.color = min < 10 ? 'var(--orange)' : 'var(--text)';
  } else {
    document.getElementById('card-ttl').textContent = t('ttl_none');
    document.getElementById('card-ttl').style.color = 'var(--text2)';
  }

  document.getElementById('last-updated').textContent = t('updated_at', {time: new Date().toLocaleTimeString()});
  renderSwitchProgress(d);
}

function renderSwitchProgress(d) {
  const prog = document.getElementById('switch-progress');
  if (d.switch_in_progress && d.switch) {
    prog.classList.add('visible');
    document.getElementById('switch-progress-title').textContent = d.switch.state_text || t('switching');
    const list = document.getElementById('switch-steps');
    list.innerHTML = '';
    (d.switch.steps || []).forEach(step => {
      const li = document.createElement('li');
      li.className = 'step-item ' + (step.ok === true ? 'step-ok' : step.ok === false ? 'step-fail' : 'step-cur');
      li.textContent = (step.ok === true ? '✓' : step.ok === false ? '✗' : '›') + ' ' + step.step + (step.detail ? ': ' + step.detail : '');
      list.appendChild(li);
    });
  } else {
    prog.classList.remove('visible');
  }
}

function setSysBadge(color, label) {
  const dot = document.getElementById('sys-dot');
  dot.className = 'dot ' + color;
  document.getElementById('sys-label').textContent = label;
}

function readableModelName(modelId) {
  return MODEL_INFO[modelId]?.label || modelId;
}

function readableModelMeta(model) {
  const info = MODEL_INFO[model.id] || {};
  const parts = [];
  if (info.vram) parts.push(info.vram);
  if (model.quantization) parts.push('quant=' + model.quantization);
  if (model.dtype) parts.push('dtype=' + model.dtype);
  if (model.dynamic) parts.push(t('model_dynamic'));
  return parts.join(' · ') || (model.hf_repo || model.litellm_model || '—');
}

function renderModelGrid() {
  const grid = document.getElementById('model-grid');
  const previousTtl = document.getElementById('ttl-select')?.value || '45';
  grid.innerHTML = '';
  const models = aiModelsData?.models || [];
  const activeMode = aiModelsData?.active_mode || statusData?.mode?.active || statusData?.active_mode;
  const switchInProgress = Boolean(aiModelsData?.switch_in_progress || statusData?.switch_in_progress);
  const host = window.location.hostname || '127.0.0.1';
  const protocol = window.location.protocol || 'http:';
  const webuiUrl = `${protocol}//${host}:3000`;

  models.forEach(model => {
    const isActive = Boolean(model.is_active);
    const runtime = model.runtime || {};
    const running = runtime.status === 'running' || Boolean(model.is_running);
    const isErrored = running && runtime.health === 'unhealthy';

    let chipClass = 'chip-stopped';
    let chipText = t('model_stopped_chip');
    if (isActive) {
      chipClass = 'chip-running';
      chipText = t('model_active_chip');
    } else if (isErrored) {
      chipClass = 'chip-error';
      chipText = t('model_error_chip');
    } else if (running) {
      chipClass = 'chip-loading';
      chipText = t('model_running_chip');
    } else if (runtime.status === 'created') {
      chipClass = 'chip-stopped';
      chipText = t('model_prepared_chip');
    }

    const repoText = model.hf_repo || model.litellm_model || '—';
    const disabled = switchInProgress || isActive || activeMode === 'comfy';
    const openUiBtn = isActive
      ? `<a class="btn btn-ghost" href="${webuiUrl}" target="_blank" rel="noopener noreferrer">${t('open_ui')}</a>`
      : '';
    const card = document.createElement('div');
    card.className = 'model-card' + (isActive ? ' active-model' : '');
    card.innerHTML = `
      <div class="model-card-name">${readableModelName(model.id)}</div>
      <div class="model-card-meta">${readableModelMeta(model)}</div>
      <div class="model-card-meta">${t('repo')}: ${repoText}</div>
      <span class="status-chip ${chipClass}">${chipText}</span>
      <div class="model-actions">
        <button class="btn ${isActive ? 'btn-ghost' : 'btn-primary'}" onclick="switchToModel('${model.id}')" ${disabled ? 'disabled' : ''}>
          ${isActive ? t('model_is_active') : t('model_activate')}
        </button>
        ${openUiBtn}
      </div>
    `;
    grid.appendChild(card);
  });

  const mode = statusData?.mode?.active || statusData?.active_mode || aiModelsData?.active_mode;
  const comfyRunning = statusData?.comfyui?.status === 'running' || aiModelsData?.comfyui?.status === 'running';
  const lease = statusData?.mode?.lease || aiModelsData?.mode?.lease;
  const comfyTransition = switchInProgress && (pendingModeTarget === 'comfy' || mode === 'comfy');
  const comfyUrl = `${protocol}//${host}:8188`;
  const ttlOptions = ['15', '30', '45', '60', '90']
    .map(v => `<option value="${v}" ${previousTtl === v ? 'selected' : ''}>${v} min</option>`)
    .join('');

  let comfyChip = `<span class="status-chip chip-stopped">${t('model_stopped_chip')}</span>`;
  let comfyControls = `
    <div class="comfy-controls">
      <span style="font-size:.9rem;color:var(--text2)">TTL</span>
      <select class="ttl-select" id="ttl-select">${ttlOptions}</select>
      <button class="btn btn-primary" onclick="activateComfy()">${t('comfy_activate')}</button>
    </div>
  `;
  let comfyCardClass = 'model-card';

  if (mode === 'comfy' && comfyRunning) {
    comfyChip = `<span class="status-chip chip-comfy">${t('model_active_chip')}</span>`;
    comfyCardClass = 'model-card comfy-active';
    const expires = lease?.expires_at ? new Date(lease.expires_at).toLocaleTimeString() : t('ttl_none');
    comfyControls = `
      <a class="comfy-link" href="${comfyUrl}" target="_blank" rel="noopener noreferrer">${t('comfy_open', {url: comfyUrl})}</a>
      <div class="model-card-meta">${t('comfy_available_until', {time: expires})}</div>
      <div class="model-return-row">
        <span style="font-size:.9rem;color:var(--text2)">${t('comfy_return_to_llm')}</span>
        <select class="model-select" id="return-model-select"></select>
        <button class="btn btn-success" onclick="deactivateComfy()">${t('comfy_return')}</button>
        <button class="btn btn-danger" onclick="preemptComfy()" title="${t('comfy_preempt_title')}">${t('comfy_preempt')}</button>
      </div>
    `;
  } else if (comfyTransition) {
    comfyChip = `<span class="status-chip chip-loading">⏳ ${t('switching')}</span>`;
    comfyControls = `<div class="model-card-meta">${t('comfy_switch_wait')}</div>`;
  }

  const comfyCard = document.createElement('div');
  comfyCard.className = comfyCardClass;
  comfyCard.innerHTML = `
    <div class="model-card-name">${t('comfy_card_title')}</div>
    <div class="model-card-meta">${t('comfy_card_subtitle')}</div>
    ${comfyChip}
    ${comfyControls}
  `;
  grid.appendChild(comfyCard);

  if (!models.length) {
    const emptyCard = document.createElement('div');
    emptyCard.className = 'model-card';
    emptyCard.innerHTML = `<div class="model-card-name">LLM</div><div class="model-card-meta">${t('llm_none')}</div>`;
    grid.prepend(emptyCard);
  }
}

function syncReturnModelSelect() {
  const select = document.getElementById('return-model-select');
  if (!select) return;
  const previous = select.value;
  const models = aiModelsData?.models || [];
  select.innerHTML = '';
  models.forEach(model => {
    const opt = document.createElement('option');
    opt.value = model.id;
    opt.textContent = readableModelName(model.id);
    select.appendChild(opt);
  });
  if (previous && models.some(m => m.id === previous)) {
    select.value = previous;
  } else if (statusData?.active_model && models.some(m => m.id === statusData.active_model)) {
    select.value = statusData.active_model;
  } else {
    select.value = models[0]?.id || '';
  }
}

function fmtNum(value) {
  if (value === null || value === undefined) return '—';
  return Number(value).toLocaleString();
}

function renderData() {
  const metrics = dataOverview?.metrics || {};
  const sources = dataOverview?.sources || {};

  document.getElementById('data-tokens').textContent = metrics.tokens_total == null ? t('data_not_available') : fmtNum(metrics.tokens_total);
  document.getElementById('data-tokens-sub').textContent = metrics.tokens_24h == null ? t('data_24h_na') : t('data_24h_value', {count: fmtNum(metrics.tokens_24h)});
  document.getElementById('data-chats-open').textContent = fmtNum(metrics.chats_open);
  document.getElementById('data-chats-sub').textContent = t('data_total_24h', {total: fmtNum(metrics.chats_total), last24h: fmtNum(metrics.chats_24h)});
  document.getElementById('data-users').textContent = fmtNum(metrics.users_total);
  document.getElementById('data-users-sub').textContent = t('data_active_24h', {count: fmtNum(metrics.users_active_24h)});
  document.getElementById('data-messages').textContent = fmtNum(metrics.messages_total);
  document.getElementById('data-msg-sub').textContent = t('data_msg_24h_req', {last24h: fmtNum(metrics.messages_24h), req: fmtNum(metrics.requests_total)});

  const chartEl = document.getElementById('chat-series');
  chartEl.innerHTML = '';
  const points = dataSeries?.series?.chats || [];
  const max = Math.max(...points.map(p => Number(p.chats || 0)), 1);
  points.forEach(point => {
    const value = Number(point.chats || 0);
    const width = Math.max(3, Math.round((value / max) * 100));
    const row = document.createElement('div');
    row.className = 'chart-row';
    row.innerHTML = `
      <div class="chart-date">${point.date}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div>
      <div class="chart-val">${value}</div>
    `;
    chartEl.appendChild(row);
  });
  if (!points.length) chartEl.innerHTML = `<div class="card-sub">${t('data_no_series')}</div>`;

  const srcEl = document.getElementById('data-sources');
  srcEl.innerHTML = '';
  Object.entries(sources).forEach(([name, payload]) => {
    const line = document.createElement('div');
    line.className = 'source-item';
    const ok = payload?.ok ? t('source_ok') : t('source_degraded');
    const detail = payload?.error ? ` · ${t('source_check_logs')}` : '';
    line.textContent = `${name}: ${ok}${detail}`;
    srcEl.appendChild(line);
  });
}

function getContainerCandidates() {
  const fixed = ['litellm', 'model-switcher', 'open-webui', 'comfyui'];
  const dynamic = (aiModelsData?.models || [])
    .map(m => m.container)
    .filter(Boolean);
  return [...new Set([...dynamic, ...fixed])];
}

function defaultLogContainer() {
  const mode = statusData?.mode?.active || statusData?.active_mode;
  if (mode === 'comfy') return 'comfyui';
  const activeModel = statusData?.active_model;
  if (activeModel && aiModelsData?.models) {
    const found = aiModelsData.models.find(m => m.id === activeModel && m.container);
    if (found) return found.container;
  }
  return 'litellm';
}

function ensureLogContainerOptions() {
  const select = document.getElementById('log-container-select');
  const current = select.value;
  const candidates = getContainerCandidates();
  select.innerHTML = '';
  candidates.forEach(name => {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });
  if (current && candidates.includes(current)) select.value = current;
  else select.value = defaultLogContainer();
}

async function fetchLogs() {
  const select = document.getElementById('log-container-select');
  const box = document.getElementById('log-box');
  const container = select.value || defaultLogContainer();
  box.textContent = t('logs_loading');
  try {
    const data = await apiFetch('/api/logs?container=' + encodeURIComponent(container) + '&tail=300');
    renderLogs(data.logs || t('logs_empty'));
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    box.textContent = t('logs_error', {error: e.message});
  }
}

function renderLogs(raw) {
  const box = document.getElementById('log-box');
  box.innerHTML = '';
  raw.split('\n').forEach(line => {
    const span = document.createElement('span');
    span.textContent = line + '\n';
    if (/error|exception|fatal|critical/i.test(line)) span.className = 'log-err';
    else if (/warn/i.test(line)) span.className = 'log-warn';
    else span.className = 'log-info';
    box.appendChild(span);
  });
}

function toggleAutoLogs() {
  const checked = document.getElementById('log-auto').checked;
  if (checked) {
    fetchLogs();
    logTimer = setInterval(fetchLogs, 10000);
  } else {
    clearInterval(logTimer);
    logTimer = null;
  }
}

async function switchToModel(model) {
  pendingModeTarget = 'llm';
  showToast('info', t('toast_switch_start', {model}));
  try {
    await apiFetch('/api/mode/switch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: 'llm', model, wait_for_ready: false}),
    });
    showToast('ok', t('toast_switch_started', {model}));
    setTimeout(refreshAll, 1000);
  } catch (e) {
    pendingModeTarget = null;
    showToast('err', e.message);
  }
}

async function activateComfy() {
  const ttlEl = document.getElementById('ttl-select');
  if (!ttlEl) return showToast('err', t('ttl_control_unavailable'));
  const ttl = parseInt(ttlEl.value, 10);
  pendingModeTarget = 'comfy';
  showToast('info', t('toast_activate_comfy', {ttl}));
  try {
    await apiFetch('/api/mode/switch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: 'comfy', ttl_minutes: ttl, wait_for_ready: false}),
    });
    showToast('ok', t('toast_activate_comfy_started'));
    setTimeout(refreshAll, 1000);
  } catch (e) {
    pendingModeTarget = null;
    showToast('err', e.message);
  }
}

async function deactivateComfy() {
  const selectEl = document.getElementById('return-model-select');
  if (!selectEl) return showToast('err', t('return_selector_unavailable'));
  const model = selectEl.value;
  pendingModeTarget = 'llm';
  showToast('info', t('toast_deactivate_comfy', {model}));
  try {
    await apiFetch('/api/mode/switch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: 'llm', model, wait_for_ready: false}),
    });
    showToast('ok', t('toast_deactivate_comfy_started'));
    setTimeout(refreshAll, 1000);
  } catch (e) {
    pendingModeTarget = null;
    showToast('err', e.message);
  }
}

async function preemptComfy() {
  pendingModeTarget = 'llm';
  showToast('info', t('toast_preempt'));
  try {
    await apiFetch('/api/mode/release', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
    });
    showToast('ok', t('toast_preempt_started'));
    setTimeout(refreshAll, 1000);
  } catch (e) {
    pendingModeTarget = null;
    showToast('err', e.message);
  }
}

let toastTimer = null;
function showToast(type, msg) {
  const toast = document.getElementById('toast');
  toast.className = type;
  toast.textContent = msg;
  toast.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.style.display = 'none';
  }, 3500);
}
</script>
</body>
</html>
"""


@app.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse(url="/admin")


@app.get("/admin", response_class=HTMLResponse)
def admin_ui():
    return HTMLResponse(content=HTML)
