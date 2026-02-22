# ============================================================
# Makefile — Producción (LiteLLM + vLLM + ComfyUI)
# ============================================================

SWITCHER_TOKEN ?= $(MODEL_SWITCHER_TOKEN)
SWITCHER_TOKEN := $(or $(SWITCHER_TOKEN),change_me)
SWITCHER_URL ?= http://127.0.0.1:9000
API_KEY ?= $(or $(LITELLM_KEY),cambiaLAclave)
MODEL ?= qwen-fast
COMFY_TTL ?= 45
TAIL ?= 200
SERVICE ?=

-include Makefile.ops

PROD_DIR ?= /opt/ai/compose
PROD_COMPOSE = docker compose -f docker-compose.yml -f docker-compose.prod.yml
PROD = cd $(PROD_DIR) && $(PROD_COMPOSE)
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
	@echo "  make prod-switch MODEL=qwen-fast|qwen-quality|deepseek|qwen-max"
	@echo "  make prod-comfy-on COMFY_TTL=45"
	@echo "  make prod-comfy-on-safe COMFY_TTL=45"
	@echo "  make prod-comfy-off MODEL=qwen-fast"
	@echo "  make prod-llm-priority"
	@echo "  make prod-gpu-preflight        # valida driver NVIDIA y runtime Docker"
	@echo "  make prod-status | make prod-mode-status"
	@echo "  make prod-test                 # prueba unica; decide llamada segun modo activo"
	@echo "  make prod-admin-url            # URL del panel /admin de model-switcher"
	@echo ""
	@echo "Logs:"
	@echo "  make prod-logs-all TAIL=200"
	@echo "  make prod-logs SERVICE=litellm TAIL=200"
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
prod-status:       ; curl -s $(SWITCHER_URL)/status -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-mode-status:  ; curl -s $(SWITCHER_URL)/mode -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-admin-url:    ; @echo "$(SWITCHER_URL)/admin"
prod-list-models:  ; curl -s $(SWITCHER_URL)/models -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-stop-models:  ; curl -s $(SWITCHER_URL)/stop -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-comfy-on:     ; curl -s $(SWITCHER_URL)/mode/switch -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d '{"mode":"comfy","ttl_minutes":$(COMFY_TTL)}' | jq
prod-comfy-off:    ; curl -s $(SWITCHER_URL)/mode/switch -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d '{"mode":"llm","model":"$(MODEL)"}' | jq
prod-llm-priority: ; curl -s $(SWITCHER_URL)/mode/release -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d '{}' | jq

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

prod-logs-all:
	$(PROD) logs -f --tail=$(TAIL)

prod-logs:
ifeq ($(strip $(SERVICE)),)
	$(PROD) logs -f --tail=$(TAIL)
else
	$(PROD) logs -f --tail=$(TAIL) $(SERVICE)
endif

prod-logs-%:
	$(PROD) logs -f --tail=$(TAIL) $*

.PHONY: help \
        prod-init prod-up prod-build-switcher prod-bootstrap-models prod-down prod-ps prod-pull prod-restart \
        prod-switch prod-switch-async prod-status prod-mode-status prod-admin-url prod-list-models prod-stop-models prod-comfy-on prod-comfy-off prod-comfy-on-safe prod-llm-priority prod-gpu-preflight prod-test \
        prod-logs-list prod-logs-all prod-logs prod-logs-%
