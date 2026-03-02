# ============================================================
# Makefile — Producción (LiteLLM + vLLM + ComfyUI)
# ============================================================

SWITCHER_TOKEN ?= $(MODEL_SWITCHER_TOKEN)
SWITCHER_TOKEN := $(or $(SWITCHER_TOKEN),change_me)
SWITCHER_URL ?= http://127.0.0.1:9000
API_KEY ?= $(or $(LITELLM_KEY),cambiaLAclave)
MODEL ?= qwen-fast
HF_URL ?=
MODEL_ID ?=
LITELLM_MODEL ?=
QUANTIZATION ?=
GPU_MEMORY_UTILIZATION ?=
MAX_MODEL_LEN ?=
MAX_NUM_SEQS ?=
TRUST_REMOTE_CODE ?= false
TOKENIZER ?=
REVISION ?=
DTYPE ?=
VLLM_IMAGE ?=
EXTRA_ARGS_JSON ?=
COMFY_TTL ?= 45
TAIL ?= 200
SERVICE ?=
CONTAINER ?=
ROLLBACK_REF ?=
ARTIFACT_DIR ?=
EXTENSIVE ?= 0

-include Makefile.ops
-include versions.lock

POSTGRES_IMAGE ?= postgres:16-alpine
LITELLM_IMAGE ?= litellm/litellm:main-stable
OPENWEBUI_IMAGE ?= ghcr.io/open-webui/open-webui:main
DOCKER_SOCKET_PROXY_IMAGE ?= tecnativa/docker-socket-proxy:0.1.1
VLLM_IMAGE_FAST ?= vllm/vllm-openai:v0.5.4
VLLM_IMAGE_QUALITY ?= vllm/vllm-openai:v0.5.4
VLLM_IMAGE_DEEPSEEK ?= vllm/vllm-openai:v0.6.6.post1
VLLM_IMAGE_QWEN_MAX ?= vllm/vllm-openai:v0.5.4
COMFYUI_IMAGE ?= yanwk/comfyui-boot:cu126-slim
MODEL_SWITCHER_DYNAMIC_VLLM_IMAGE ?= vllm/vllm-openai:v0.6.6.post1
MODEL_SWITCHER_DYNAMIC_DTYPE ?= half
MODEL_SWITCHER_DYNAMIC_ALLOW_TRUST_REMOTE_CODE ?= 0
MODEL_SWITCHER_TRUSTED_REPOS ?=

PROD_DIR ?= /opt/ai/compose
PROD_COMPOSE = docker compose -f docker-compose.yml -f docker-compose.prod.yml
PROD_ENV = \
	POSTGRES_IMAGE=$(POSTGRES_IMAGE) \
	LITELLM_IMAGE=$(LITELLM_IMAGE) \
	OPENWEBUI_IMAGE=$(OPENWEBUI_IMAGE) \
	DOCKER_SOCKET_PROXY_IMAGE=$(DOCKER_SOCKET_PROXY_IMAGE) \
	VLLM_IMAGE_FAST=$(VLLM_IMAGE_FAST) \
	VLLM_IMAGE_QUALITY=$(VLLM_IMAGE_QUALITY) \
	VLLM_IMAGE_DEEPSEEK=$(VLLM_IMAGE_DEEPSEEK) \
	VLLM_IMAGE_QWEN_MAX=$(VLLM_IMAGE_QWEN_MAX) \
	COMFYUI_IMAGE=$(COMFYUI_IMAGE) \
	MODEL_SWITCHER_DYNAMIC_VLLM_IMAGE=$(MODEL_SWITCHER_DYNAMIC_VLLM_IMAGE) \
	MODEL_SWITCHER_DYNAMIC_DTYPE=$(MODEL_SWITCHER_DYNAMIC_DTYPE) \
	MODEL_SWITCHER_DYNAMIC_ALLOW_TRUST_REMOTE_CODE=$(MODEL_SWITCHER_DYNAMIC_ALLOW_TRUST_REMOTE_CODE) \
	MODEL_SWITCHER_TRUSTED_REPOS=$(MODEL_SWITCHER_TRUSTED_REPOS)
PROD = cd $(PROD_DIR) && $(PROD_ENV) $(PROD_COMPOSE)
PROD_MODEL_PROFILES = --profile qwen-fast --profile qwen-quality --profile deepseek --profile qwen-max --profile comfy
PROD_ALL_PROFILES = --profile webui $(PROD_MODEL_PROFILES)

help:
	@echo "Comandos principales:"
	@echo "  make prod-init                  # levanta servicios base y crea contenedores de modelos/comfy"
	@echo "  make prod-up                    # levanta stack base (webui + switcher + db)"
	@echo "  make prod-down                  # apaga todo"
	@echo "  make prod-ps                    # estado de contenedores"
	@echo "  make prod-pull                  # actualiza imagenes"
	@echo ""
	@echo "Control de modo/modelo:"
	@echo "  make prod-switch MODEL=<id-en-/models>"
	@echo "  make prod-register-model HF_URL=https://huggingface.co/org/repo MODEL_ID=opcional [TRUST_REMOTE_CODE=true TOKENIZER=... REVISION=...]"
	@echo "  make prod-unregister-model MODEL=<id-dinamico>"
	@echo "  make prod-comfy-on COMFY_TTL=45"
	@echo "  make prod-comfy-on-safe COMFY_TTL=45"
	@echo "  make prod-comfy-off MODEL=qwen-fast"
	@echo "  make prod-llm-priority"
	@echo "  make prod-gpu-preflight        # valida driver NVIDIA y runtime Docker"
	@echo "  make prod-status | make prod-mode-status"
	@echo "  make prod-test                 # prueba unica; decide llamada segun modo activo"
	@echo "  make prod-test-auto [EXTENSIVE=1]    # bateria automatica PASS/FAIL"
	@echo "  make prod-admin-url            # URL del panel /admin de model-switcher"
	@echo ""
	@echo "Upgrade:"
	@echo "  make prod-upgrade-precheck"
	@echo "  make prod-upgrade-canary"
	@echo "  make prod-upgrade-verify"
	@echo "  make prod-upgrade-promote"
	@echo "  make prod-upgrade-rollback ROLLBACK_REF=<git-ref>"
	@echo ""
	@echo "Logs:"
	@echo "  make prod-logs-all TAIL=200"
	@echo "  make prod-logs SERVICE=litellm TAIL=200"
	@echo "  make prod-logs-container CONTAINER=vllm-... TAIL=200"
	@echo "  make prod-logs-<servicio> TAIL=200   (ej: prod-logs-model-switcher)"
	@echo "  make prod-logs-list"

# --- Ciclo de vida ---
prod-init:
	@$(MAKE) prod-up
	@$(MAKE) prod-bootstrap-models

prod-up:               ; $(PROD) --profile webui up -d
prod-build-switcher:   ; $(PROD) --profile webui build model-switcher
prod-bootstrap-models: ; $(PROD) $(PROD_MODEL_PROFILES) create vllm-fast vllm-quality vllm-deepseek vllm-qwen32b comfyui
prod-down:             ; $(PROD) $(PROD_ALL_PROFILES) down
prod-ps:               ; $(PROD) ps
prod-pull:             ; $(PROD) $(PROD_ALL_PROFILES) pull
prod-restart:          ; $(PROD) restart

# --- Control API model-switcher ---
prod-switch:       ; curl -s $(SWITCHER_URL)/switch -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d '{"model":"$(MODEL)"}' | jq
prod-switch-async: ; curl -s $(SWITCHER_URL)/switch -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d '{"model":"$(MODEL)","wait_for_ready":false}' | jq
prod-register-model:
	@test -n "$(HF_URL)" || { echo "ERROR: define HF_URL=https://huggingface.co/org/repo"; exit 1; }
	@BODY=$$(jq -n \
	  --arg url "$(HF_URL)" \
	  --arg model_id "$(MODEL_ID)" \
	  --arg litellm_model "$(LITELLM_MODEL)" \
	  --arg quantization "$(QUANTIZATION)" \
	  --arg gpu "$(GPU_MEMORY_UTILIZATION)" \
	  --arg max_len "$(MAX_MODEL_LEN)" \
	  --arg max_seqs "$(MAX_NUM_SEQS)" \
	  --arg trust_remote_code "$(TRUST_REMOTE_CODE)" \
	  --arg tokenizer "$(TOKENIZER)" \
	  --arg revision "$(REVISION)" \
	  --arg dtype "$(DTYPE)" \
	  --arg vllm_image "$(VLLM_IMAGE)" \
	  --arg extra_args_json "$(EXTRA_ARGS_JSON)" \
	  '\
	    def maybe_string($$k; $$v): if ($$v|length) > 0 then {($$k): $$v} else {} end; \
	    ({huggingface_url: $$url} \
	      + maybe_string("model_id"; $$model_id) \
	      + maybe_string("litellm_model"; $$litellm_model) \
	      + maybe_string("quantization"; $$quantization) \
	      + maybe_string("tokenizer"; $$tokenizer) \
	      + maybe_string("revision"; $$revision) \
	      + maybe_string("dtype"; $$dtype) \
	      + maybe_string("vllm_image"; $$vllm_image) \
	      + (if ($$gpu|length) > 0 then {gpu_memory_utilization: ($$gpu|tonumber)} else {} end) \
	      + (if ($$max_len|length) > 0 then {max_model_len: ($$max_len|tonumber)} else {} end) \
	      + (if ($$max_seqs|length) > 0 then {max_num_seqs: ($$max_seqs|tonumber)} else {} end) \
	      + (if ($$trust_remote_code|ascii_downcase) == "true" then {trust_remote_code: true} else {} end) \
	      + (if ($$extra_args_json|length) > 0 then {extra_args: ($$extra_args_json|fromjson)} else {} end) \
	    )' \
	); \
	curl -s $(SWITCHER_URL)/models/register -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d "$$BODY" | jq
prod-unregister-model:
	@test -n "$(MODEL)" || { echo "ERROR: define MODEL=<id-dinamico>"; exit 1; }
	@curl -s -X DELETE $(SWITCHER_URL)/models/$(MODEL) -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-status:       ; curl -s $(SWITCHER_URL)/status -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-mode-status:  ; curl -s $(SWITCHER_URL)/mode -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-admin-url:    ; @echo "$(SWITCHER_URL)/admin"
prod-list-models:  ; curl -s $(SWITCHER_URL)/models -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-stop-models:  ; curl -s $(SWITCHER_URL)/stop -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-comfy-on:     ; curl -s $(SWITCHER_URL)/mode/switch -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d '{"mode":"comfy","ttl_minutes":$(COMFY_TTL)}' | jq
prod-comfy-off:    ; curl -s $(SWITCHER_URL)/mode/switch -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d '{"mode":"llm","model":"$(MODEL)"}' | jq
prod-llm-priority: ; curl -s $(SWITCHER_URL)/mode/release -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d '{}' | jq

# --- Upgrade canary ---
prod-upgrade-precheck:
	@$(MAKE) prod-gpu-preflight
	@$(MAKE) prod-ps
	@$(MAKE) prod-status
	@$(MAKE) prod-list-models

prod-upgrade-canary:
	@$(MAKE) prod-pull
	@$(MAKE) prod-build-switcher
	@$(MAKE) prod-down
	@$(MAKE) prod-init

prod-upgrade-verify:
	@$(MAKE) prod-test-auto

prod-upgrade-promote:
	@$(MAKE) prod-upgrade-precheck
	@$(MAKE) prod-upgrade-canary
	@$(MAKE) prod-upgrade-verify

prod-upgrade-rollback:
	@test -n "$(ROLLBACK_REF)" || { echo "ERROR: define ROLLBACK_REF=<git-ref>"; exit 1; }
	@cd $(PROD_DIR) && git fetch --all --tags && git checkout $(ROLLBACK_REF)
	@$(MAKE) prod-init
	@$(MAKE) prod-test-auto

# --- Preflight GPU para ComfyUI ---
prod-gpu-preflight:
	@echo "[preflight] Verificando GPU NVIDIA en host..."
	@command -v nvidia-smi >/dev/null 2>&1 || { \
	  echo "ERROR: nvidia-smi no encontrado en host."; \
	  echo "Accion: instala/corrige driver NVIDIA y reinicia."; \
	  echo "Ejemplo Debian: sudo apt update && sudo apt install -y nvidia-driver firmware-misc-nonfree && sudo reboot"; \
	  exit 1; \
	}
	@nvidia-smi >/dev/null 2>&1 || { \
	  echo "ERROR: nvidia-smi no puede acceder al driver NVIDIA."; \
	  echo "Accion: valida driver/kernel y vuelve a probar nvidia-smi."; \
	  exit 1; \
	}
	@echo "[ok] Driver NVIDIA disponible en host."
	@echo "[preflight] Verificando runtime NVIDIA en Docker..."
	@docker info 2>/dev/null | grep -E "Runtimes|Default Runtime" || true
	@docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi >/dev/null 2>&1 || { \
	  echo "ERROR: Docker no expone GPU NVIDIA dentro de contenedores."; \
	  echo "Accion: instala toolkit y configura runtime Docker."; \
	  echo "Comandos: sudo apt-get install -y nvidia-container-toolkit"; \
	  echo "          sudo nvidia-ctk runtime configure --runtime=docker"; \
	  echo "          sudo systemctl restart docker"; \
	  exit 1; \
	}
	@echo "[ok] Runtime NVIDIA en Docker operativo."
	@echo "[ok] Preflight GPU completado."

prod-comfy-on-safe:
	@$(MAKE) prod-gpu-preflight
	@$(MAKE) prod-comfy-on COMFY_TTL=$(COMFY_TTL)

# --- Test unico (autodetecta modo) ---
prod-test:
	@MODE=$$(curl -sf $(SWITCHER_URL)/mode -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq -r '.mode.active'); \
	if [ "$$MODE" = "llm" ]; then \
	  STATUS_JSON=$$(curl -sf $(SWITCHER_URL)/status -H "Authorization: Bearer $(SWITCHER_TOKEN)"); \
	  MODEL_ACTIVE=$$(printf '%s' "$$STATUS_JSON" | jq -r '.active_model'); \
	  if [ -z "$$MODEL_ACTIVE" ] || [ "$$MODEL_ACTIVE" = "null" ]; then \
	    echo "ERROR: no hay active_model en modo llm"; exit 1; \
	  fi; \
	  MODEL_LITELLM=$$(printf '%s' "$$STATUS_JSON" | jq -r '.active_litellm_model // empty'); \
	  if [ -z "$$MODEL_LITELLM" ]; then \
	    MODEL_LITELLM=$$(curl -sf $(SWITCHER_URL)/models -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq -r --arg mid "$$MODEL_ACTIVE" '[.models[]? | select(.id == $$mid) | .litellm_model][0] // empty'); \
	  fi; \
	  if [ -z "$$MODEL_LITELLM" ] || [ "$$MODEL_LITELLM" = "null" ]; then \
	    MODEL_LITELLM="$$MODEL_ACTIVE"; \
	  fi; \
	  echo "Llamada usada: POST http://127.0.0.1:4000/v1/chat/completions (model=$$MODEL_LITELLM)"; \
	  curl -sf http://127.0.0.1:4000/v1/chat/completions \
	    -H "Authorization: Bearer $(API_KEY)" \
	    -H "Content-Type: application/json" \
	    -d "{\"model\":\"$$MODEL_LITELLM\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"temperature\":0}" \
	    | jq -e '.choices[0].message.content' >/dev/null; \
	  echo "OK: LiteLLM/vLLM responde con $$MODEL_LITELLM"; \
	elif [ "$$MODE" = "comfy" ]; then \
	  echo "Llamada usada: GET http://127.0.0.1:8188/system_stats"; \
	  curl -sf http://127.0.0.1:8188/system_stats | jq -e '.' >/dev/null; \
	  echo "OK: ComfyUI responde"; \
	else \
	  echo "ERROR: modo desconocido '$$MODE'"; exit 1; \
	fi

prod-test-auto:
	@cd $(PROD_DIR) && \
	  MODEL_SWITCHER_TOKEN="$(SWITCHER_TOKEN)" \
	  LITELLM_KEY="$(API_KEY)" \
	  SWITCHER_URL="$(SWITCHER_URL)" \
	  ARTIFACT_DIR="$(ARTIFACT_DIR)" \
	  EXTENSIVE="$(EXTENSIVE)" \
	  ./scripts/prod_test_auto.sh

prod-test-auto-ext:
	@$(MAKE) prod-test-auto EXTENSIVE=1

# --- Logs ---
prod-logs-list:
	@echo "Servicios de logs:"
	@echo "  postgres"
	@echo "  litellm"
	@echo "  docker-socket-proxy"
	@echo "  model-switcher"
	@echo "  vllm-fast"
	@echo "  vllm-quality"
	@echo "  vllm-deepseek"
	@echo "  vllm-qwen32b"
	@echo "  comfyui"
	@echo "  open-webui"
	@echo "  (dynamic) usa: make prod-logs-container CONTAINER=vllm-<id>"

prod-logs-all:
	$(PROD) logs -f --tail=$(TAIL)

prod-logs:
ifeq ($(strip $(SERVICE)),)
	$(PROD) logs -f --tail=$(TAIL)
else
	$(PROD) logs -f --tail=$(TAIL) $(SERVICE)
endif

prod-logs-container:
	@test -n "$(CONTAINER)" || { echo "ERROR: define CONTAINER=<docker-container-name>"; exit 1; }
	@docker logs -f --tail=$(TAIL) $(CONTAINER)

prod-logs-%:
	$(PROD) logs -f --tail=$(TAIL) $*

.PHONY: help \
        prod-init prod-up prod-build-switcher prod-bootstrap-models prod-down prod-ps prod-pull prod-restart \
        prod-switch prod-switch-async prod-register-model prod-unregister-model prod-status prod-mode-status prod-admin-url prod-list-models prod-stop-models prod-comfy-on prod-comfy-off prod-comfy-on-safe prod-llm-priority prod-gpu-preflight prod-test prod-test-auto prod-test-auto-ext \
        prod-upgrade-precheck prod-upgrade-canary prod-upgrade-verify prod-upgrade-promote prod-upgrade-rollback \
        prod-logs-list prod-logs-all prod-logs prod-logs-container prod-logs-%
