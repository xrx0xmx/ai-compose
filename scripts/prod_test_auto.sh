#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT_DIR="${ARTIFACT_DIR:-$ROOT_DIR/artifacts}"
SWITCHER_URL="${SWITCHER_URL:-http://127.0.0.1:9000}"
MODEL_SWITCHER_TOKEN="${MODEL_SWITCHER_TOKEN:-}"
LITELLM_KEY="${LITELLM_KEY:-cambiaLAclave}"
EXTENSIVE="${EXTENSIVE:-0}"
TEST_DYNAMIC_HF_URL="${TEST_DYNAMIC_HF_URL:-https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-AWQ}"
TEST_DYNAMIC_MODEL_ID="${TEST_DYNAMIC_MODEL_ID:-}"
DYNAMIC_REQUIRED="${DYNAMIC_REQUIRED:-1}"

if [[ -z "$MODEL_SWITCHER_TOKEN" ]]; then
  echo "ERROR: MODEL_SWITCHER_TOKEN is required"
  exit 1
fi

for cmd in jq curl docker make; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $cmd"
    exit 1
  fi
done

mkdir -p "$ARTIFACT_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -z "$TEST_DYNAMIC_MODEL_ID" ]]; then
  TEST_DYNAMIC_MODEL_ID="auto-test-qwen25-3b-awq-$timestamp"
fi

normalize_model_id() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9-]+/-/g; s/-+/-/g; s/^-+//; s/-+$//'
}

TEST_DYNAMIC_MODEL_ID="$(normalize_model_id "$TEST_DYNAMIC_MODEL_ID")"

log_file="$ARTIFACT_DIR/prod-test-auto-$timestamp.log"
json_file="$ARTIFACT_DIR/prod-test-auto-$timestamp.json"
results_tmp="$(mktemp)"
trap 'rm -f "$results_tmp"' EXIT

pass_count=0
fail_count=0
skip_count=0
dynamic_created=0
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

exec > >(tee -a "$log_file") 2>&1

add_result() {
  local step="$1"
  local status="$2"
  local detail="$3"
  jq -nc --arg step "$step" --arg status "$status" --arg detail "$detail" \
    '{step:$step,status:$status,detail:$detail}' >> "$results_tmp"
}

pass_step() {
  local step="$1"
  local detail="$2"
  pass_count=$((pass_count + 1))
  add_result "$step" "pass" "$detail"
  echo "[PASS] $step - $detail"
}

fail_step() {
  local step="$1"
  local detail="$2"
  fail_count=$((fail_count + 1))
  add_result "$step" "fail" "$detail"
  echo "[FAIL] $step - $detail"
}

skip_step() {
  local step="$1"
  local detail="$2"
  skip_count=$((skip_count + 1))
  add_result "$step" "skip" "$detail"
  echo "[SKIP] $step - $detail"
}

run_step() {
  local step="$1"
  local mode="$2"
  local cmd="$3"
  local output
  echo "[STEP] $step"
  if output="$(bash -lc "$cmd" 2>&1)"; then
    [[ -n "$output" ]] && echo "$output"
    pass_step "$step" "ok"
    return 0
  fi

  local rc=$?
  [[ -n "$output" ]] && echo "$output"
  if [[ "$mode" == "soft" ]]; then
    skip_step "$step" "soft failure (rc=$rc)"
    return 0
  fi
  fail_step "$step" "command failed (rc=$rc)"
  return "$rc"
}

switch_model() {
  local model_id
  model_id="$(normalize_model_id "$1")"
  local mode="$2"
  local step="switch_${model_id}"
  if ! curl -sf "$SWITCHER_URL/models" \
    -H "Authorization: Bearer $MODEL_SWITCHER_TOKEN" | jq -e --arg model "$model_id" '.models[] | select(.id == $model)' >/dev/null; then
    skip_step "$step" "model not registered"
    return 0
  fi

  local output
  if output="$(MODEL_SWITCHER_TOKEN="$MODEL_SWITCHER_TOKEN" MODEL="$model_id" make prod-switch 2>&1)"; then
    echo "$output"
    pass_step "$step" "switch success"
    return 0
  fi

  echo "$output"
  if [[ "$mode" == "soft" ]] && echo "$output" | grep -Eiq "out of memory|cuda|not enough memory"; then
    skip_step "$step" "skipped due to memory pressure"
    return 0
  fi
  if [[ "$mode" == "soft" ]]; then
    skip_step "$step" "soft failure"
    return 0
  fi
  fail_step "$step" "switch failed"
  return 1
}

register_dynamic_model() {
  MODEL_SWITCHER_TOKEN="$MODEL_SWITCHER_TOKEN" \
  HF_URL="$TEST_DYNAMIC_HF_URL" \
  MODEL_ID="$TEST_DYNAMIC_MODEL_ID" \
  make prod-register-model
}

unregister_dynamic_model() {
  MODEL_SWITCHER_TOKEN="$MODEL_SWITCHER_TOKEN" MODEL="$TEST_DYNAMIC_MODEL_ID" make prod-unregister-model
}

cleanup_dynamic_model() {
  if [[ "$dynamic_created" != "1" ]]; then
    return
  fi
  if curl -sf "$SWITCHER_URL/models" \
    -H "Authorization: Bearer $MODEL_SWITCHER_TOKEN" | jq -e --arg model "$TEST_DYNAMIC_MODEL_ID" '.models[] | select(.id == $model and .dynamic == true)' >/dev/null; then
    unregister_dynamic_model >/dev/null 2>&1 || true
  fi
}

trap cleanup_dynamic_model EXIT

run_step "preflight_gpu" "strict" "MODEL_SWITCHER_TOKEN='$MODEL_SWITCHER_TOKEN' make prod-gpu-preflight"
run_step "preflight_env" "strict" "MODEL_SWITCHER_TOKEN='$MODEL_SWITCHER_TOKEN' LITELLM_KEY='$LITELLM_KEY' make prod-preflight-env"
run_step "image_lock_check" "strict" "make prod-image-lock-check"
run_step "health_status" "strict" "MODEL_SWITCHER_TOKEN='$MODEL_SWITCHER_TOKEN' make prod-status >/dev/null"
run_step "health_models" "strict" "MODEL_SWITCHER_TOKEN='$MODEL_SWITCHER_TOKEN' make prod-list-models >/dev/null"

switch_model "qwen-fast" "strict"
run_step "baseline_test" "strict" "MODEL_SWITCHER_TOKEN='$MODEL_SWITCHER_TOKEN' API_KEY='$LITELLM_KEY' make prod-test"

switch_model "qwen-quality" "soft"
switch_model "deepseek" "soft"
switch_model "qwen-max" "soft"

switch_model "qwen-fast" "strict"

echo "[STEP] dynamic_register"
if register_dynamic_model >/tmp/prod_dynamic_register.out 2>&1; then
  cat /tmp/prod_dynamic_register.out
  registered_id="$(jq -r '.model.id // empty' /tmp/prod_dynamic_register.out 2>/dev/null || true)"
  if [[ -n "${registered_id:-}" ]]; then
    TEST_DYNAMIC_MODEL_ID="$(normalize_model_id "$registered_id")"
  fi
  dynamic_created=1
  pass_step "dynamic_register" "model registered"
  rm -f /tmp/prod_dynamic_register.out
else
  cat /tmp/prod_dynamic_register.out
  rm -f /tmp/prod_dynamic_register.out
  if [[ "$DYNAMIC_REQUIRED" == "1" ]]; then
    fail_step "dynamic_register" "required dynamic registration failed"
    exit 1
  else
    skip_step "dynamic_register" "dynamic test disabled on failure"
  fi
fi

if [[ "$DYNAMIC_REQUIRED" == "1" ]]; then
  switch_model "$TEST_DYNAMIC_MODEL_ID" "strict"
  run_step "dynamic_model_test" "strict" "MODEL_SWITCHER_TOKEN='$MODEL_SWITCHER_TOKEN' API_KEY='$LITELLM_KEY' make prod-test"
  switch_model "qwen-fast" "strict"
  echo "[STEP] dynamic_unregister"
  if MODEL_SWITCHER_TOKEN="$MODEL_SWITCHER_TOKEN" MODEL="$TEST_DYNAMIC_MODEL_ID" make prod-unregister-model >/tmp/prod_dynamic_unregister.out 2>&1; then
    cat /tmp/prod_dynamic_unregister.out
    dynamic_created=0
    pass_step "dynamic_unregister" "model removed"
  else
    cat /tmp/prod_dynamic_unregister.out
    fail_step "dynamic_unregister" "could not remove dynamic model"
    rm -f /tmp/prod_dynamic_unregister.out
    exit 1
  fi
  rm -f /tmp/prod_dynamic_unregister.out
fi

run_step "mode_comfy_on_safe" "strict" "MODEL_SWITCHER_TOKEN='$MODEL_SWITCHER_TOKEN' COMFY_TTL='10' make prod-comfy-on-safe >/dev/null"
run_step "mode_comfy_status" "strict" "MODEL_SWITCHER_TOKEN='$MODEL_SWITCHER_TOKEN' make prod-mode-status >/dev/null"
run_step "mode_comfy_off" "strict" "MODEL_SWITCHER_TOKEN='$MODEL_SWITCHER_TOKEN' MODEL='qwen-fast' make prod-comfy-off >/dev/null"
run_step "mode_back_to_llm" "strict" "MODEL_SWITCHER_TOKEN='$MODEL_SWITCHER_TOKEN' API_KEY='$LITELLM_KEY' make prod-test"
run_step "edge_proxy_check" "strict" "make prod-proxy-check"
run_step "ports_audit" "strict" "make prod-ports-audit"

if [[ "$EXTENSIVE" == "1" ]]; then
  BAD_MODEL_ID="$(normalize_model_id "auto-test-invalid-model")"
  BAD_HF_URL="https://huggingface.co/this-model/does-not-exist"
  echo "[STEP] rollback_drill_register_invalid"
  if MODEL_SWITCHER_TOKEN="$MODEL_SWITCHER_TOKEN" HF_URL="$BAD_HF_URL" MODEL_ID="$BAD_MODEL_ID" make prod-register-model >/tmp/prod_bad_register.out 2>&1; then
    cat /tmp/prod_bad_register.out
    pass_step "rollback_drill_register_invalid" "invalid model registered for failure drill"
  else
    cat /tmp/prod_bad_register.out
    fail_step "rollback_drill_register_invalid" "could not register invalid model"
    rm -f /tmp/prod_bad_register.out
    exit 1
  fi
  rm -f /tmp/prod_bad_register.out

  echo "[STEP] rollback_drill_switch_invalid"
  if MODEL_SWITCHER_TOKEN="$MODEL_SWITCHER_TOKEN" MODEL="$BAD_MODEL_ID" make prod-switch >/tmp/prod_bad_switch.out 2>&1; then
    cat /tmp/prod_bad_switch.out
    if jq -e '.status == "failed" or .status == "rolled_back"' /tmp/prod_bad_switch.out >/dev/null 2>&1; then
      pass_step "rollback_drill_switch_invalid" "failure/rollback observed"
    else
      fail_step "rollback_drill_switch_invalid" "switch unexpectedly succeeded"
      rm -f /tmp/prod_bad_switch.out
      exit 1
    fi
  else
    cat /tmp/prod_bad_switch.out
    fail_step "rollback_drill_switch_invalid" "switch command failed unexpectedly"
    rm -f /tmp/prod_bad_switch.out
    exit 1
  fi
  rm -f /tmp/prod_bad_switch.out

  run_step "rollback_drill_recover" "strict" "MODEL_SWITCHER_TOKEN='$MODEL_SWITCHER_TOKEN' MODEL='qwen-fast' make prod-switch >/dev/null"
  run_step "rollback_drill_cleanup" "soft" "MODEL_SWITCHER_TOKEN='$MODEL_SWITCHER_TOKEN' MODEL='$BAD_MODEL_ID' make prod-unregister-model >/dev/null"
fi

run_step "final_status" "strict" "curl -sf '$SWITCHER_URL/status' -H 'Authorization: Bearer $MODEL_SWITCHER_TOKEN' | jq -e '.switch_in_progress == false' >/dev/null"

FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq -s \
  --arg started_at "$STARTED_AT" \
  --arg finished_at "$FINISHED_AT" \
  --arg log_file "$log_file" \
  --argjson pass "$pass_count" \
  --argjson fail "$fail_count" \
  --argjson skip "$skip_count" \
  '{
    started_at: $started_at,
    finished_at: $finished_at,
    summary: {
      pass: $pass,
      fail: $fail,
      skip: $skip
    },
    log_file: $log_file,
    results: .
  }' "$results_tmp" > "$json_file"

echo "[SUMMARY] pass=$pass_count fail=$fail_count skip=$skip_count"
echo "[ARTIFACT] log=$log_file"
echo "[ARTIFACT] json=$json_file"

if [[ "$fail_count" -gt 0 ]]; then
  exit 1
fi
