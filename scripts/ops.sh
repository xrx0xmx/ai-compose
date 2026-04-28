#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.prod.yml)

load_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$file"
    set +a
  fi
}

load_file "$ROOT_DIR/versions.lock"
load_file "$ROOT_DIR/.env"

SWITCHER_URL="${SWITCHER_URL:-http://127.0.0.1:9000}"
HOST_BASE_URL="${HOST_BASE_URL:-http://127.0.0.1}"
OPENWEBUI_URL="${OPENWEBUI_URL:-${HOST_BASE_URL}:3000}"
COMFYUI_URL="${COMFYUI_URL:-${HOST_BASE_URL}:8188}"
ADMIN_URL="${ADMIN_URL:-${HOST_BASE_URL}/admin}"
MATXA_PROFILE="${MATXA_PROFILE:-matxa-cuda}"
MATXA_BACKEND_SERVICE="${MATXA_BACKEND_SERVICE:-}"
MATXA_TTS_PORT="${MATXA_ADAPTER_HOST_PORT:-8012}"
MATXA_TTS_URL="${MATXA_TTS_URL:-${HOST_BASE_URL}:${MATXA_TTS_PORT}/v1}"
MATXA_TTS_MODEL="${MATXA_TTS_MODEL:-tts-1}"
MATXA_TTS_VOICE="${MATXA_TTS_VOICE:-central-grau}"
MATXA_TTS_KEY="${MATXA_TTS_KEY:-matxa-local}"
MATXA_TTS_TEXT="${MATXA_TTS_TEXT:-La seva gerra sembla molt antiga i el viatge fou molt llarg.}"
MODEL_SWITCHER_DEFAULT="${MODEL_SWITCHER_DEFAULT:-qwen-fast}"
DEFAULT_TTL="${COMFY_TTL:-45}"

case "$MATXA_PROFILE" in
  matxa-cuda)
    MATXA_BACKEND_SERVICE="${MATXA_BACKEND_SERVICE:-matxa-backend-cuda}"
    ;;
  matxa-cpu)
    MATXA_BACKEND_SERVICE="${MATXA_BACKEND_SERVICE:-matxa-backend-cpu}"
    ;;
  *)
    echo "ERROR: MATXA_PROFILE invalido: $MATXA_PROFILE (usa matxa-cuda|matxa-cpu)"
    exit 1
    ;;
esac

WEBUI_PROFILE=(--profile webui --profile "$MATXA_PROFILE")
MODEL_PROFILES=(--profile qwen-fast --profile qwen-quality --profile deepseek --profile qwen-max --profile comfy --profile "$MATXA_PROFILE")
ALL_PROFILES=(--profile webui --profile qwen-fast --profile qwen-quality --profile deepseek --profile qwen-max --profile comfy --profile "$MATXA_PROFILE")
BASE_SERVICES=(postgres litellm docker-socket-proxy model-switcher open-webui admin-panel "$MATXA_BACKEND_SERVICE" catotron-cpu matxa-adapter)
MODEL_SERVICES=(vllm-fast vllm-quality vllm-deepseek vllm-qwen32b comfyui)
COMPOSE_SERVICES=(postgres litellm docker-socket-proxy model-switcher open-webui admin-panel matxa-backend-cuda matxa-backend-cpu catotron-cpu matxa-adapter vllm-fast vllm-quality vllm-deepseek vllm-qwen32b comfyui)

compose() {
  docker compose "${COMPOSE_FILES[@]}" "$@"
}

compose_webui() {
  compose "${WEBUI_PROFILE[@]}" "$@"
}

compose_models() {
  compose "${MODEL_PROFILES[@]}" "$@"
}

compose_all() {
  compose "${ALL_PROFILES[@]}" "$@"
}

require_cmds() {
  local cmd
  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "ERROR: required command not found: $cmd"
      exit 1
    fi
  done
}

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "ERROR: missing .env variable: $name"
    exit 1
  fi
}

is_compose_service() {
  local target="$1"
  local service
  for service in "${COMPOSE_SERVICES[@]}"; do
    if [[ "$service" == "$target" ]]; then
      return 0
    fi
  done
  return 1
}

switcher_get() {
  local path="$1"
  require_cmds curl jq
  require_env MODEL_SWITCHER_TOKEN
  curl -fsS "$SWITCHER_URL$path" \
    -H "Authorization: Bearer $MODEL_SWITCHER_TOKEN"
}

switcher_post() {
  local path="$1"
  local payload="$2"
  require_cmds curl jq
  require_env MODEL_SWITCHER_TOKEN
  curl -fsS "$SWITCHER_URL$path" \
    -H "Authorization: Bearer $MODEL_SWITCHER_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$payload"
}

current_mode() {
  switcher_get "/mode" | jq -r '.mode.active'
}

current_status_json() {
  switcher_get "/status"
}

current_mode_json() {
  switcher_get "/mode"
}

http_ok() {
  local url="$1"
  local label="$2"
  local code
  code="$(curl -k -s -o /dev/null -w '%{http_code}' "$url" || true)"
  case "$code" in
    200|301|302|307|308)
      echo "OK: $label -> $code"
      ;;
    *)
      echo "ERROR: $label -> $code"
      exit 1
      ;;
  esac
}

cmd_up() {
  compose_webui up -d --remove-orphans "${BASE_SERVICES[@]}"
  compose_models create "${MODEL_SERVICES[@]}"
}

cmd_down() {
  compose_all down --remove-orphans
}

cmd_deploy() {
  compose_webui build admin-panel model-switcher "$MATXA_BACKEND_SERVICE" matxa-adapter
  cmd_down
  cmd_up
}

cmd_ps() {
  compose_all ps --all
}

cmd_pull() {
  compose_all pull
}

cmd_logs() {
  local target="${1:-all}"
  local tail="${2:-200}"
  if [[ "$target" == "all" ]]; then
    compose_all logs -f --tail="$tail"
    return 0
  fi

  if is_compose_service "$target"; then
    compose_all logs -f --tail="$tail" "$target"
  else
    docker logs -f --tail "$tail" "$target"
  fi
}

cmd_status() {
  local mode_json status_json
  mode_json="$(current_mode_json)"
  status_json="$(current_status_json)"

  jq -n \
    --arg switcher "ok" \
    --argjson mode "$mode_json" \
    --argjson status "$status_json" \
    '{
      switcher: $switcher,
      mode: $mode.mode.active,
      active_model: ($status.active_model // null),
      active_litellm_model: ($status.active_litellm_model // null),
      ready: ($status.switch.ready // null),
      last_error: ($status.last_error // null)
    }'
}

cmd_test() {
  require_cmds curl jq
  require_env MODEL_SWITCHER_TOKEN

  local active_mode status_json model_active model_litellm
  active_mode="$(current_mode)"

  if [[ "$active_mode" == "llm" ]]; then
    require_env LITELLM_KEY
    status_json="$(current_status_json)"
    model_active="$(printf '%s' "$status_json" | jq -r '.active_model // empty')"
    if [[ -z "$model_active" ]]; then
      echo "ERROR: no active_model in llm mode"
      exit 1
    fi

    model_litellm="$(printf '%s' "$status_json" | jq -r '.active_litellm_model // empty')"
    if [[ -z "$model_litellm" ]]; then
      model_litellm="$(switcher_get "/models" | jq -r --arg mid "$model_active" '[.models[]? | select(.id == $mid) | .litellm_model][0] // empty')"
    fi
    if [[ -z "$model_litellm" ]]; then
      model_litellm="$model_active"
    fi

    echo "Llamada usada: POST http://127.0.0.1:4000/v1/chat/completions (model=$model_litellm)"
    curl -fsS http://127.0.0.1:4000/v1/chat/completions \
      -H "Authorization: Bearer $LITELLM_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"model\":\"$model_litellm\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"temperature\":0}" \
      | jq -e '.choices[0].message.content' >/dev/null
    echo "OK: LiteLLM/vLLM responde con $model_litellm"
    return 0
  fi

  if [[ "$active_mode" == "comfy" ]]; then
    echo "Llamada usada: GET $COMFYUI_URL/system_stats"
    curl -fsS "$COMFYUI_URL/system_stats" | jq -e '.' >/dev/null
    echo "OK: ComfyUI responde"
    return 0
  fi

  echo "ERROR: modo desconocido '$active_mode'"
  exit 1
}

cmd_test_tts() {
  require_cmds curl jq python3

  local tmp_wav
  local payload
  tmp_wav="$(mktemp /tmp/matxa-tts.XXXXXX.wav)"
  trap 'rm -f "${tmp_wav:-}"' RETURN

  payload="$(jq -nc \
    --arg model "$MATXA_TTS_MODEL" \
    --arg input "$MATXA_TTS_TEXT" \
    --arg voice "$MATXA_TTS_VOICE" \
    '{model:$model,input:$input,voice:$voice,response_format:"wav",speed:1.0}')"

  echo "Llamada usada: POST $MATXA_TTS_URL/audio/speech (voice=$MATXA_TTS_VOICE)"
  curl -fsS "$MATXA_TTS_URL/audio/speech" \
    -H "Authorization: Bearer $MATXA_TTS_KEY" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    -o "$tmp_wav"

  python3 - "$tmp_wav" <<'PY'
import sys
import wave

path = sys.argv[1]
with wave.open(path, "rb") as wav_file:
  frames = wav_file.getnframes()
  rate = wav_file.getframerate()
  channels = wav_file.getnchannels()

if frames <= 0:
  raise SystemExit("WAV has no frames")

print(f"OK: Matxa TTS devuelve WAV ({channels}ch, {rate}Hz, {frames} frames)")
PY
}

cmd_switch() {
  local model="$1"
  switcher_post "/switch" "{\"model\":\"$model\"}" | jq
}

cmd_mode() {
  local requested_mode="$1"
  local model="${2:-}"
  local ttl="${3:-$DEFAULT_TTL}"

  case "$requested_mode" in
    comfy)
      switcher_post "/mode/switch" "{\"mode\":\"comfy\",\"ttl_minutes\":$ttl}" | jq
      ;;
    llm)
      if [[ -z "$model" ]]; then
        model="$MODEL_SWITCHER_DEFAULT"
      fi
      switcher_post "/mode/switch" "{\"mode\":\"llm\",\"model\":\"$model\"}" | jq
      ;;
    *)
      echo "ERROR: MODE invalido: $requested_mode (usa llm|comfy)"
      exit 1
      ;;
  esac
}

cmd_doctor() {
  local active_mode
  echo "[doctor] docker compose ps"
  cmd_ps
  echo
  echo "[doctor] status"
  cmd_status
  echo
  echo "[doctor] smoke test"
  cmd_test
  echo
  echo "[doctor] open webui"
  http_ok "$OPENWEBUI_URL" "$OPENWEBUI_URL"
  echo "[doctor] admin"
  http_ok "$ADMIN_URL" "$ADMIN_URL"
  echo "[doctor] matxa tts"
  cmd_test_tts
  active_mode="$(current_mode)"
  if [[ "$active_mode" == "comfy" ]]; then
    echo "[doctor] comfy"
    curl -fsS "$COMFYUI_URL/system_stats" | jq -e '.' >/dev/null
    echo "OK: $COMFYUI_URL/system_stats -> 200"
  else
    echo "[doctor] comfy skip (modo actual: $active_mode)"
  fi
}

cmd_help() {
  cat <<'HELP'
Uso: scripts/ops.sh <up|down|deploy|ps|pull|logs|status|test|test-tts|switch|mode|doctor>
HELP
}

main() {
  local command="${1:-help}"
  case "$command" in
    up)
      cmd_up
      ;;
    down)
      cmd_down
      ;;
    deploy)
      cmd_deploy
      ;;
    ps)
      cmd_ps
      ;;
    pull)
      cmd_pull
      ;;
    logs)
      cmd_logs "${2:-all}" "${3:-200}"
      ;;
    status)
      cmd_status
      ;;
    test)
      cmd_test
      ;;
    test-tts)
      cmd_test_tts
      ;;
    switch)
      [[ -n "${2:-}" ]] || { echo "ERROR: missing model id"; exit 1; }
      cmd_switch "$2"
      ;;
    mode)
      [[ -n "${2:-}" ]] || { echo "ERROR: missing mode (llm|comfy)"; exit 1; }
      cmd_mode "$2" "${3:-}" "${4:-$DEFAULT_TTL}"
      ;;
    doctor)
      cmd_doctor
      ;;
    help|-h|--help)
      cmd_help
      ;;
    *)
      echo "ERROR: comando no soportado: $command"
      cmd_help
      exit 1
      ;;
  esac
}

main "$@"
