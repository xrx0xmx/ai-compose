import json
import os
import subprocess
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

SCRIPT_PATH = os.getenv("MODEL_SWITCHER_SCRIPT", "/opt/ai/compose/scripts/switch-model.sh")
TOKEN = os.getenv("MODEL_SWITCHER_TOKEN", "")

ALLOWED_MODELS = {"qwen-fast", "qwen-quality", "deepseek", "qwen-max"}

app = FastAPI(title="AI Model Switcher", version="1.0.0")


def require_token(authorization: Optional[str] = Header(default=None)) -> None:
  if not TOKEN:
    raise HTTPException(status_code=500, detail="MODEL_SWITCHER_TOKEN not set")
  if not authorization or not authorization.startswith("Bearer "):
    raise HTTPException(status_code=401, detail="Missing bearer token")
  supplied = authorization.split(" ", 1)[1].strip()
  if supplied != TOKEN:
    raise HTTPException(status_code=403, detail="Invalid token")


def run_script(args: List[str]) -> Dict[str, Any]:
  cmd = ["sudo", SCRIPT_PATH] + args
  result = subprocess.run(cmd, capture_output=True, text=True)
  if result.returncode != 0:
    detail = result.stderr.strip() or result.stdout.strip() or "script failed"
    raise HTTPException(status_code=500, detail=detail)
  output = result.stdout.strip()
  try:
    return json.loads(output)
  except json.JSONDecodeError as exc:
    raise HTTPException(status_code=500, detail=f"invalid json from script: {exc}") from exc


class SwitchRequest(BaseModel):
  model: str


@app.get("/health")
def health() -> Dict[str, str]:
  return {"status": "ok"}


@app.get("/models", dependencies=[Depends(require_token)])
def models() -> Dict[str, Any]:
  return run_script(["list"])


@app.get("/status", dependencies=[Depends(require_token)])
def status() -> Dict[str, Any]:
  return run_script(["status"])


@app.post("/switch", dependencies=[Depends(require_token)])
def switch(req: SwitchRequest) -> Dict[str, Any]:
  if req.model not in ALLOWED_MODELS:
    raise HTTPException(status_code=400, detail="unknown model")
  return run_script(["switch", req.model])


@app.post("/stop", dependencies=[Depends(require_token)])
def stop() -> Dict[str, Any]:
  return run_script(["stop"])
