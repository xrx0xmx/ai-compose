#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

LOCK_FILE="/var/lock/model-switcher.lock"
COMPOSE_DIR="/opt/ai/compose"
COMPOSE="docker compose -f ${COMPOSE_DIR}/docker-compose.yml -f ${COMPOSE_DIR}/docker-compose.prod.yml"
ACTIVE_LINK="${COMPOSE_DIR}/litellm-active.yml"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-420}"

die() {
  echo "error: $1" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  switch-model.sh list
  switch-model.sh status
  switch-model.sh switch <qwen-fast|qwen-quality|deepseek|qwen-max>
  switch-model.sh stop
EOF
}

model_to_profile() {
  case "$1" in
    qwen-fast) echo "qwen-fast" ;;
    qwen-quality) echo "qwen-quality" ;;
    deepseek) echo "deepseek" ;;
    qwen-max) echo "qwen-max" ;;
    *) return 1 ;;
  esac
}

model_to_service() {
  case "$1" in
    qwen-fast) echo "vllm-fast" ;;
    qwen-quality) echo "vllm-quality" ;;
    deepseek) echo "vllm-deepseek" ;;
    qwen-max) echo "vllm-qwen32b" ;;
    *) return 1 ;;
  esac
}

model_to_config() {
  case "$1" in
    qwen-fast) echo "litellm-config.qwen-fast.yml" ;;
    qwen-quality) echo "litellm-config.qwen-quality.yml" ;;
    deepseek) echo "litellm-config.deepseek.yml" ;;
    qwen-max) echo "litellm-config.qwen-max.yml" ;;
    *) return 1 ;;
  esac
}

service_to_model() {
  case "$1" in
    vllm-fast) echo "qwen-fast" ;;
    vllm-quality) echo "qwen-quality" ;;
    vllm-deepseek) echo "deepseek" ;;
    vllm-qwen32b) echo "qwen-max" ;;
    *) return 1 ;;
  esac
}

json_array() {
  local first=1
  printf "["
  for item in "$@"; do
    if [ $first -eq 0 ]; then printf ","; fi
    printf "\"%s\"" "$item"
    first=0
  done
  printf "]"
}

list_models() {
  cat <<'EOF'
{"models":[{"id":"qwen-fast","profile":"qwen-fast","service":"vllm-fast","config":"litellm-config.qwen-fast.yml"},{"id":"qwen-quality","profile":"qwen-quality","service":"vllm-quality","config":"litellm-config.qwen-quality.yml"},{"id":"deepseek","profile":"deepseek","service":"vllm-deepseek","config":"litellm-config.deepseek.yml"},{"id":"qwen-max","profile":"qwen-max","service":"vllm-qwen32b","config":"litellm-config.qwen-max.yml"}]}
EOF
}

status_models() {
  local active_config=""
  local active_model=""
  local running_models=()
  local running_services=()

  if [ -L "$ACTIVE_LINK" ]; then
    active_config=$(readlink -f "$ACTIVE_LINK" 2>/dev/null || true)
  fi

  case "$active_config" in
    *litellm-config.qwen-fast.yml) active_model="qwen-fast" ;;
    *litellm-config.qwen-quality.yml) active_model="qwen-quality" ;;
    *litellm-config.deepseek.yml) active_model="deepseek" ;;
    *litellm-config.qwen-max.yml) active_model="qwen-max" ;;
    *) active_model="" ;;
  esac

  for svc in vllm-fast vllm-quality vllm-deepseek vllm-qwen32b; do
    if docker ps --format '{{.Names}}' | grep -qx "$svc"; then
      running_services+=("$svc")
    fi
  done

  for svc in "${running_services[@]}"; do
    if model=$(service_to_model "$svc" 2>/dev/null); then
      running_models+=("$model")
    fi
  done

  printf '{'
  printf '"running_models":%s,' "$(json_array "${running_models[@]}")"
  if [ -n "$active_model" ]; then
    printf '"active_model":"%s",' "$active_model"
  else
    printf '"active_model":null,'
  fi
  if [ -n "$active_config" ]; then
    printf '"active_config":"%s"' "$active_config"
  else
    printf '"active_config":null'
  fi
  printf '}\n'
}

wait_healthy() {
  local service="$1"
  local elapsed=0
  local status=""

  while [ "$elapsed" -lt "$WAIT_TIMEOUT" ]; do
    status=$(docker inspect --format='{{.State.Health.Status}}' "$service" 2>/dev/null || true)
    if [ "$status" = "healthy" ]; then
      return 0
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
  die "timeout waiting for $service to be healthy"
}

switch_model() {
  local model="$1"
  local profile service config config_path

  profile=$(model_to_profile "$model") || die "unknown model: $model"
  service=$(model_to_service "$model") || die "unknown model: $model"
  config=$(model_to_config "$model") || die "unknown model: $model"
  config_path="${COMPOSE_DIR}/${config}"

  [ -f "$config_path" ] || die "config not found: $config_path"

  $COMPOSE stop vllm-fast vllm-quality vllm-deepseek vllm-qwen32b >/dev/null || true
  ln -sf "$config_path" "$ACTIVE_LINK"
  $COMPOSE --profile "$profile" up -d "$service"
  $COMPOSE restart litellm
  wait_healthy "$service"
  status_models
}

stop_models() {
  $COMPOSE stop vllm-fast vllm-quality vllm-deepseek vllm-qwen32b >/dev/null || true
  status_models
}

main() {
  if [ $# -lt 1 ]; then
    usage
    exit 1
  fi

  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    die "another operation is in progress"
  fi

  case "$1" in
    list) list_models ;;
    status) status_models ;;
    switch)
      [ $# -eq 2 ] || die "switch requires a model name"
      switch_model "$2"
      ;;
    stop) stop_models ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
