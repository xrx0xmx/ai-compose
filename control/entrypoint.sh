#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${MODEL_CONFIG_DIR:-/config}"
TEMPLATE_DIR="${MODEL_TEMPLATE_DIR:-/opt/model-configs}"
DEFAULT_MODEL="${MODEL_SWITCHER_DEFAULT:-qwen-fast}"
ACTIVE_CONFIG="${CONFIG_DIR}/active.yml"
ACTIVE_MODEL_FILE="${CONFIG_DIR}/active.model"
ACTIVE_MODE_FILE="${CONFIG_DIR}/active.mode"
ACTIVE_COMFY_LEASE_FILE="${CONFIG_DIR}/active.mode.lease_until"

mkdir -p "${CONFIG_DIR}"

if [ ! -f "${ACTIVE_CONFIG}" ]; then
  template="${TEMPLATE_DIR}/${DEFAULT_MODEL}.yml"
  if [ ! -f "${template}" ]; then
    echo "missing template: ${template}" >&2
    exit 1
  fi
  cp "${template}" "${ACTIVE_CONFIG}"
  echo "${DEFAULT_MODEL}" > "${ACTIVE_MODEL_FILE}"
fi

if [ ! -f "${ACTIVE_MODE_FILE}" ]; then
  echo "llm" > "${ACTIVE_MODE_FILE}"
fi

if [ "$(tr -d '[:space:]' < "${ACTIVE_MODE_FILE}")" != "comfy" ]; then
  rm -f "${ACTIVE_COMFY_LEASE_FILE}"
fi

BIND="${MODEL_SWITCHER_BIND:-0.0.0.0}"
PORT="${MODEL_SWITCHER_PORT:-9000}"

exec python3 -m uvicorn control.app:app --host "${BIND}" --port "${PORT}"
