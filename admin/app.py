"""
Admin Panel — Puerto 80
Autenticación via Open WebUI SQLite (bcrypt), sesión JWT, proxy al model-switcher.
"""
import os
import sqlite3
import time
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
import requests
from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
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


@app.get("/api/models")
def api_models(user: dict = Depends(get_current_user)):
    try:
        return switcher_get("/models")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


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


@app.get("/api/logs/{container_name}")
def api_logs(container_name: str, tail: int = 200, user: dict = Depends(get_current_user)):
    if container_name not in ALLOWED_CONTAINERS:
        raise HTTPException(status_code=400, detail=f"Contenedor no permitido: {container_name}")
    try:
        # Find container ID by name via docker socket proxy
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
            timeout=15,
            stream=True,
        )
        log_r.raise_for_status()

        # Docker log stream has 8-byte header per line (stream multiplexing)
        raw = log_r.content
        lines = []
        i = 0
        while i < len(raw):
            if i + 8 > len(raw):
                break
            size = int.from_bytes(raw[i + 4:i + 8], "big")
            i += 8
            if size > 0 and i + size <= len(raw):
                lines.append(raw[i:i + size].decode("utf-8", errors="replace"))
            i += size

        if not lines:
            # Fallback: try raw decode if no multiplexing headers
            lines = [raw.decode("utf-8", errors="replace")]

        return {"logs": "".join(lines), "container": container_name}
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
    --bg: #0d1117;
    --surface: #161b22;
    --surface2: #1c2230;
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
  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; min-height: 100vh; }

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
  .model-card-name { font-weight: 600; font-size: 1rem; }
  .model-card-meta { font-size: .8rem; color: var(--text2); }
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
    padding: 16px; height: 500px; overflow-y: auto; font-family: 'Menlo','Monaco','Courier New',monospace;
    font-size: .78rem; line-height: 1.55; white-space: pre-wrap; word-break: break-all;
  }
  .log-err  { color: #f85149; }
  .log-warn { color: #e3b341; }
  .log-info { color: #8b949e; }

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
  }
</style>
</head>
<body>

<!-- ═══════════════════════════════ LOGIN ═══════════════════════════════ -->
<div id="login-screen">
  <div class="login-card">
    <h1>🛡️ Admin Panel</h1>
    <p>Usa tus credenciales de Open WebUI (solo admins)</p>
    <div class="form-group">
      <label>Email</label>
      <input type="email" id="login-email" placeholder="admin@ejemplo.com" autocomplete="email">
    </div>
    <div class="form-group">
      <label>Contraseña</label>
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
      <span class="user-name" id="user-name"></span>
      <button class="btn btn-ghost" onclick="doLogout()" style="padding:6px 12px;font-size:.8rem">Salir</button>
    </div>
  </header>

  <div class="shell">
    <nav>
      <div class="nav-section">
        <div class="nav-section-label">Panel</div>
        <div class="nav-item active" onclick="showSection('estado')">
          <span class="nav-icon">📊</span><span>Estado</span>
        </div>
        <div class="nav-item" onclick="showSection('modelos')">
          <span class="nav-icon">🤖</span><span>Modelos LLM</span>
        </div>
        <div class="nav-item" onclick="showSection('comfy')">
          <span class="nav-icon">🎨</span><span>ComfyUI</span>
        </div>
        <div class="nav-item" onclick="showSection('logs')">
          <span class="nav-icon">📋</span><span>Logs</span>
        </div>
      </div>
    </nav>

    <main>
      <!-- ── Estado ── -->
      <div class="section active" id="sec-estado">
        <h2>Estado del sistema</h2>
        <div class="cards" id="status-cards">
          <div class="card"><div class="card-label">Modo</div><div class="card-value" id="card-mode">—</div></div>
          <div class="card"><div class="card-label">Modelo activo</div><div class="card-value" id="card-model">—</div></div>
          <div class="card"><div class="card-label">ComfyUI</div><div class="card-value" id="card-comfy">—</div></div>
          <div class="card"><div class="card-label">Tiempo restante</div><div class="card-value" id="card-ttl">—</div></div>
        </div>

        <div class="switch-progress" id="switch-progress">
          <div class="progress-title" id="switch-progress-title">Cambiando modo…</div>
          <ul class="steps-list" id="switch-steps"></ul>
        </div>

        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">
          <button class="btn btn-ghost" onclick="refreshStatus()">↻ Refrescar</button>
        </div>
        <div style="font-size:.75rem;color:var(--text2)" id="last-updated"></div>
      </div>

      <!-- ── Modelos LLM ── -->
      <div class="section" id="sec-modelos">
        <h2>Modelos LLM</h2>
        <div class="model-grid" id="model-grid"></div>
      </div>

      <!-- ── ComfyUI ── -->
      <div class="section" id="sec-comfy">
        <h2>ComfyUI — Generación de imagen</h2>
        <div class="comfy-box">
          <div class="comfy-status-row">
            <div class="comfy-status-label">Estado:</div>
            <div id="comfy-status-chip"><span class="status-chip chip-stopped">Inactivo</span></div>
          </div>
          <div id="comfy-link-row" style="display:none;margin-bottom:16px;">
            <a class="comfy-link" id="comfy-link" href="#" target="_blank">
              🔗 Abrir ComfyUI
            </a>
            <span style="font-size:.8rem;color:var(--text2);margin-left:10px;">Disponible hasta: <span id="comfy-expires"></span></span>
          </div>
          <div id="comfy-inactive-controls" class="comfy-controls">
            <span style="font-size:.9rem;color:var(--text2)">TTL:</span>
            <select class="ttl-select" id="ttl-select">
              <option value="15">15 min</option>
              <option value="30">30 min</option>
              <option value="45" selected>45 min</option>
              <option value="60">60 min</option>
              <option value="90">90 min</option>
            </select>
            <button class="btn btn-primary" onclick="activateComfy()">🎨 Activar ComfyUI</button>
          </div>
          <div id="comfy-active-controls" style="display:none;">
            <div class="model-return-row">
              <span style="font-size:.9rem;color:var(--text2)">Volver a LLM:</span>
              <select class="model-select" id="return-model-select">
                <option value="qwen-fast">Qwen 7B (rápido)</option>
                <option value="qwen-quality">Qwen 14B (calidad)</option>
                <option value="deepseek">DeepSeek-R1</option>
                <option value="qwen-max">Qwen 32B (máximo)</option>
              </select>
              <button class="btn btn-success" onclick="deactivateComfy()">✅ Volver a LLM</button>
              <button class="btn btn-danger" onclick="preemptComfy()" title="Fuerza el retorno inmediato a LLM">⚡ Preemption urgente</button>
            </div>
          </div>
        </div>
      </div>

      <!-- ── Logs ── -->
      <div class="section" id="sec-logs">
        <h2>Logs de contenedores</h2>
        <div class="logs-toolbar">
          <select class="container-select" id="log-container-select">
            <option value="litellm">litellm</option>
            <option value="comfyui">comfyui</option>
            <option value="vllm-fast">vllm-fast</option>
            <option value="vllm-quality">vllm-quality</option>
            <option value="vllm-deepseek">vllm-deepseek</option>
            <option value="vllm-qwen32b">vllm-qwen32b</option>
            <option value="model-switcher">model-switcher</option>
            <option value="open-webui">open-webui</option>
          </select>
          <button class="btn btn-ghost" onclick="fetchLogs()">↻ Cargar logs</button>
          <label class="auto-label">
            <input type="checkbox" id="log-auto" onchange="toggleAutoLogs()">
            Auto (10s)
          </label>
        </div>
        <div class="log-box" id="log-box">Selecciona un contenedor y pulsa "Cargar logs"</div>
      </div>
    </main>
  </div>
</div>

<div id="toast"></div>

<script>
// ─── State ───────────────────────────────────────────────────────────────────
let TOKEN = localStorage.getItem('admin_jwt') || '';
let statusData = null;
let statusTimer = null;
let logTimer = null;
let serverHost = window.location.hostname;

// ─── Init ────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  if (TOKEN) tryAutoLogin();
  document.getElementById('login-password').addEventListener('keydown', e => {
    if (e.key === 'Enter') doLogin();
  });
});

async function tryAutoLogin() {
  try {
    const r = await apiFetch('/auth/me');
    showApp(r.name);
  } catch {
    TOKEN = '';
    localStorage.removeItem('admin_jwt');
  }
}

// ─── Auth ────────────────────────────────────────────────────────────────────
async function doLogin() {
  const email = document.getElementById('login-email').value.trim();
  const pass  = document.getElementById('login-password').value;
  const errEl = document.getElementById('login-error');
  const btn   = document.getElementById('login-btn');

  errEl.textContent = '';
  btn.disabled = true; btn.textContent = 'Entrando…';

  try {
    const r = await fetch('/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, password: pass}),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || 'Error de autenticación');
    TOKEN = data.token;
    localStorage.setItem('admin_jwt', TOKEN);
    showApp(data.name);
  } catch (e) {
    errEl.textContent = e.message;
  } finally {
    btn.disabled = false; btn.textContent = 'Entrar';
  }
}

function doLogout() {
  TOKEN = '';
  localStorage.removeItem('admin_jwt');
  clearInterval(statusTimer);
  clearInterval(logTimer);
  document.getElementById('app').style.display = 'none';
  document.getElementById('login-screen').style.display = 'flex';
}

function showApp(name) {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app').style.display = 'flex';
  document.getElementById('user-name').textContent = name;
  refreshStatus();
  statusTimer = setInterval(refreshStatus, 5000);
}

// ─── Navigation ──────────────────────────────────────────────────────────────
function showSection(id) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('sec-' + id).classList.add('active');
  event.currentTarget.classList.add('active');
}

// ─── API ─────────────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  const headers = {'Authorization': 'Bearer ' + TOKEN, ...(opts.headers || {})};
  const r = await fetch(path, {...opts, headers});
  if (r.status === 401) { doLogout(); throw new Error('Sesión expirada'); }
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

// ─── Status ──────────────────────────────────────────────────────────────────
async function refreshStatus() {
  try {
    const data = await apiFetch('/api/status');
    statusData = data;
    renderStatus(data);
  } catch (e) {
    setSysBadge('red', 'Sin conexión');
  }
}

function renderStatus(d) {
  const mode = d.mode?.active || d.active_mode || '—';
  const model = d.running_models?.[0] || d.active_model || '—';
  const comfyStatus = d.comfyui?.status || '—';
  const lease = d.mode?.lease;
  const switchInProgress = d.switch_in_progress;

  // Header badge
  if (switchInProgress) {
    setSysBadge('orange', 'Cambiando…');
  } else if (mode === 'comfy') {
    setSysBadge('orange', 'ComfyUI activo');
  } else if (mode === 'llm' && model && model !== '—') {
    setSysBadge('green', 'LLM · ' + model);
  } else {
    setSysBadge('red', 'Sin modelo');
  }

  // Cards
  document.getElementById('card-mode').textContent = mode.toUpperCase();
  document.getElementById('card-model').textContent = model;
  document.getElementById('card-comfy').textContent = comfyStatus === 'running' ? '▶ Activo' : '⏹ Inactivo';
  document.getElementById('card-comfy').style.color = comfyStatus === 'running' ? 'var(--green)' : 'var(--text2)';

  if (lease?.remaining_seconds && !lease.expired) {
    const min = Math.ceil(lease.remaining_seconds / 60);
    document.getElementById('card-ttl').textContent = min + ' min';
    document.getElementById('card-ttl').style.color = min < 10 ? 'var(--orange)' : 'var(--text)';
  } else {
    document.getElementById('card-ttl').textContent = '—';
    document.getElementById('card-ttl').style.color = 'var(--text2)';
  }

  document.getElementById('last-updated').textContent = 'Actualizado: ' + new Date().toLocaleTimeString();

  // Switch progress
  const prog = document.getElementById('switch-progress');
  if (switchInProgress && d.switch) {
    prog.classList.add('visible');
    document.getElementById('switch-progress-title').textContent =
      d.switch.state_text || 'Cambiando…';
    const ul = document.getElementById('switch-steps');
    ul.innerHTML = '';
    (d.switch.steps || []).forEach(s => {
      const li = document.createElement('li');
      li.className = 'step-item ' + (s.ok === true ? 'step-ok' : s.ok === false ? 'step-fail' : 'step-cur');
      li.textContent = (s.ok === true ? '✓' : s.ok === false ? '✗' : '›') + ' ' + s.step + (s.detail ? ': ' + s.detail : '');
      ul.appendChild(li);
    });
  } else {
    prog.classList.remove('visible');
  }

  // Model cards
  renderModelGrid(d);

  // ComfyUI section
  renderComfySection(d);
}

function setSysBadge(color, label) {
  const dot = document.getElementById('sys-dot');
  dot.className = 'dot ' + color;
  document.getElementById('sys-label').textContent = label;
}

function renderModelGrid(d) {
  const grid = document.getElementById('model-grid');
  const activeModel = d.running_models?.[0] || d.active_model;
  const mode = d.mode?.active || d.active_mode;
  const switchInProgress = d.switch_in_progress;

  const modelMeta = {
    'qwen-fast':    {label:'Qwen 2.5 7B',    vram:'~13 GB (55%)',  container:'vllm-fast'},
    'qwen-quality': {label:'Qwen 2.5 14B',   vram:'~20 GB (85%)',  container:'vllm-quality'},
    'deepseek':     {label:'DeepSeek-R1 14B',vram:'~21 GB (95%)',  container:'vllm-deepseek'},
    'qwen-max':     {label:'Qwen 2.5 32B',   vram:'~21 GB (95%)',  container:'vllm-qwen32b'},
  };

  grid.innerHTML = '';
  for (const [id, meta] of Object.entries(modelMeta)) {
    const cont = d.containers?.[id] || {};
    const isActive = (activeModel === id) && (mode === 'llm');
    const running = cont.status === 'running';

    const card = document.createElement('div');
    card.className = 'model-card' + (isActive ? ' active-model' : '');

    let chip = '';
    if (isActive)   chip = '<span class="status-chip chip-running">● Activo</span>';
    else if (running) chip = '<span class="status-chip chip-loading">● Cargando</span>';
    else             chip = '<span class="status-chip chip-stopped">○ Detenido</span>';

    card.innerHTML = `
      <div class="model-card-name">${meta.label}</div>
      <div class="model-card-meta">${meta.vram}</div>
      ${chip}
      <button class="btn ${isActive ? 'btn-ghost' : 'btn-primary'}"
        onclick="switchToModel('${id}')"
        ${(switchInProgress || isActive || mode === 'comfy') ? 'disabled' : ''}>
        ${isActive ? '✓ Activo' : 'Activar'}
      </button>
    `;
    grid.appendChild(card);
  }
}

function renderComfySection(d) {
  const mode = d.mode?.active || d.active_mode;
  const comfyRunning = d.comfyui?.status === 'running';
  const lease = d.mode?.lease;

  const chipEl = document.getElementById('comfy-status-chip');
  const linkRow = document.getElementById('comfy-link-row');
  const inactiveCtrl = document.getElementById('comfy-inactive-controls');
  const activeCtrl = document.getElementById('comfy-active-controls');

  if (mode === 'comfy' && comfyRunning) {
    chipEl.innerHTML = '<span class="status-chip chip-comfy">● Activo</span>';
    linkRow.style.display = 'block';
    const comfyUrl = 'http://' + serverHost + ':8188';
    document.getElementById('comfy-link').href = comfyUrl;
    document.getElementById('comfy-link').textContent = '🔗 Abrir ComfyUI → ' + comfyUrl;
    if (lease?.expires_at) {
      document.getElementById('comfy-expires').textContent =
        new Date(lease.expires_at).toLocaleTimeString();
    }
    inactiveCtrl.style.display = 'none';
    activeCtrl.style.display = 'block';
  } else if (d.switch_in_progress) {
    chipEl.innerHTML = '<span class="status-chip chip-loading">⏳ Cambiando…</span>';
    linkRow.style.display = 'none';
    inactiveCtrl.style.display = 'none';
    activeCtrl.style.display = 'none';
  } else {
    chipEl.innerHTML = '<span class="status-chip chip-stopped">○ Inactivo</span>';
    linkRow.style.display = 'none';
    inactiveCtrl.style.display = 'flex';
    activeCtrl.style.display = 'none';
  }
}

// ─── Actions ─────────────────────────────────────────────────────────────────
async function switchToModel(model) {
  showToast('info', 'Iniciando cambio a ' + model + '…');
  try {
    await apiFetch('/api/mode/switch', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({mode:'llm', model, wait_for_ready: false}),
    });
    showToast('ok', 'Cambio iniciado → ' + model);
    setTimeout(refreshStatus, 1000);
  } catch(e) {
    showToast('err', e.message);
  }
}

async function activateComfy() {
  const ttl = parseInt(document.getElementById('ttl-select').value);
  showToast('info', 'Activando ComfyUI (' + ttl + ' min)…');
  try {
    await apiFetch('/api/mode/switch', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({mode:'comfy', ttl_minutes: ttl, wait_for_ready: false}),
    });
    showToast('ok', 'ComfyUI activándose…');
    setTimeout(refreshStatus, 1000);
  } catch(e) {
    showToast('err', e.message);
  }
}

async function deactivateComfy() {
  const model = document.getElementById('return-model-select').value;
  showToast('info', 'Volviendo a LLM (' + model + ')…');
  try {
    await apiFetch('/api/mode/switch', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({mode:'llm', model, wait_for_ready: false}),
    });
    showToast('ok', 'Retornando a LLM…');
    setTimeout(refreshStatus, 1000);
  } catch(e) {
    showToast('err', e.message);
  }
}

async function preemptComfy() {
  showToast('info', 'Preemption: forzando retorno a LLM…');
  try {
    await apiFetch('/api/mode/release', {method:'POST', headers:{'Content-Type':'application/json'}});
    showToast('ok', 'Preemption iniciado');
    setTimeout(refreshStatus, 1000);
  } catch(e) {
    showToast('err', e.message);
  }
}

// ─── Logs ─────────────────────────────────────────────────────────────────────
async function fetchLogs() {
  const container = document.getElementById('log-container-select').value;
  const box = document.getElementById('log-box');
  box.textContent = 'Cargando…';
  try {
    const data = await apiFetch('/api/logs/' + container + '?tail=300');
    renderLogs(data.logs || '(sin output)');
    box.scrollTop = box.scrollHeight;
  } catch(e) {
    box.textContent = 'Error: ' + e.message;
  }
}

function renderLogs(raw) {
  const box = document.getElementById('log-box');
  box.innerHTML = '';
  const lines = raw.split('\n');
  lines.forEach(line => {
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
  }
}

// ─── Toast ────────────────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(type, msg) {
  const t = document.getElementById('toast');
  t.className = type;
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.style.display = 'none'; }, 3500);
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
