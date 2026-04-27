#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="${MATXA_MODEL_DIR:-/cache/models}"
MODEL_FILE="${MODEL_DIR}/matxa_multiaccent_wavenext_e2e.onnx"
CONFIG_FILE="${MODEL_DIR}/config.yaml"
SPEAKER_FILE="${MODEL_DIR}/spk_to_id_3.json"

mkdir -p "${MODEL_DIR}" "${HF_HOME:-/cache/huggingface}" "${HUGGINGFACE_HUB_CACHE:-/cache/huggingface/hub}"

if ! command -v hf >/dev/null 2>&1; then
  echo "[matxa-backend] missing 'hf' CLI. Install huggingface-hub[cli] in the image."
  exit 1
fi

if [[ ! -f "${MODEL_FILE}" ]] || [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "[matxa-backend] downloading Matxa model assets into ${MODEL_DIR}"
  hf download projecte-aina/matxa-tts-cat-multiaccent matxa_multiaccent_wavenext_e2e.onnx --local-dir "${MODEL_DIR}"
  hf download projecte-aina/matxa-tts-cat-multiaccent config.yaml --local-dir "${MODEL_DIR}"
fi

if [[ ! -f "${SPEAKER_FILE}" ]]; then
  cp /opt/build/upstream/spk_to_id_3.json "${SPEAKER_FILE}"
fi

ln -sf "${MODEL_FILE}" /opt/build/upstream/matxa_multiaccent_wavenext_e2e.onnx
ln -sf "${CONFIG_FILE}" /opt/build/upstream/config.yaml
ln -sf "${SPEAKER_FILE}" /opt/build/upstream/spk_to_id_3.json

exec python3 -u app.py
