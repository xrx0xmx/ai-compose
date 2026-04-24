# ============================================================
# Makefile — Producción (LiteLLM + vLLM + ComfyUI)
# ============================================================

SWITCHER_TOKEN ?= $(MODEL_SWITCHER_TOKEN)
SWITCHER_URL ?= http://127.0.0.1:9000
API_KEY ?= $(LITELLM_KEY)
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
MODE ?= all
TARGET ?= all
CONFIRM ?= NO
SCOPE ?= project
ROLLBACK_REF ?=
ARTIFACT_DIR ?=
EXTENSIVE ?= 0
HOST_BASE_URL ?= https://127.0.0.1
OPENWEBUI_URL ?= $(HOST_BASE_URL)/
COMFYUI_URL ?= $(HOST_BASE_URL)/comfy/
ADMIN_URL ?= $(HOST_BASE_URL)/admin
ADMIN_API_URL ?= $(HOST_BASE_URL)/admin-api

-include Makefile.ops
-include versions.lock

POSTGRES_IMAGE ?= postgres:16-alpine
LITELLM_IMAGE ?= litellm/litellm@sha256:303c31af87e7915e7b34d6c4d55a6ac753ef947a5deaa899e9ccfd3d1d58f7c2
OPENWEBUI_IMAGE ?= ghcr.io/open-webui/open-webui@sha256:bb3f0281554bf05a9d505ffb5a5f067ab53e13ac772eb4ea3077a92ddc64600e
DOCKER_SOCKET_PROXY_IMAGE ?= tecnativa/docker-socket-proxy:0.1.1
EDGE_PROXY_IMAGE ?= nginx:1.27.4-alpine
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
	EDGE_PROXY_IMAGE=$(EDGE_PROXY_IMAGE) \
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
PROD_BASE_SERVICES = postgres litellm docker-socket-proxy model-switcher open-webui admin-panel edge-proxy
PROD_MODEL_PROFILES = --profile qwen-fast --profile qwen-quality --profile deepseek --profile qwen-max --profile comfy
PROD_ALL_PROFILES = --profile webui $(PROD_MODEL_PROFILES)
PROD_COMPOSE_SERVICES = $(PROD_BASE_SERVICES) vllm-fast vllm-quality vllm-deepseek vllm-qwen32b comfyui

help:
	@echo "Comandos simplificados (recomendados):"
	@echo "  make up MODE=all               # levanta todo"
	@echo "  make up MODE=infra             # solo infraestructura y red"
	@echo "  make up MODE=models            # solo modelos IA (vLLM/Comfy)"
	@echo "  make down                      # para todo el stack"
	@echo "  make purge CONFIRM=YES SCOPE=project   # reset del proyecto"
	@echo "  make purge CONFIRM=YES SCOPE=host      # reset Docker host (destructivo)"
	@echo "  make logs TARGET=all TAIL=200"
	@echo "  make logs TARGET=litellm TAIL=200"
	@echo "  make logs TARGET=vllm-<id-dinamico> TAIL=200"
	@echo "  make start TARGET=admin-panel"
	@echo "  make stop TARGET=admin-panel"
	@echo ""
	@echo "VPN/Host:"
	@echo "  make vpn-up | make vpn-down | make vpn-status | make ssh"
	@echo "  make scp-home [SCP_SRC=.] [SCP_DEST=~/]"
	@echo ""
	@echo "Comandos existentes (compatibilidad):"
	@echo "  make prod-preflight-env         # valida secretos requeridos y placeholders inseguros"
	@echo "  make prod-image-lock-check      # bloquea tags latest/main en imagenes de prod"
	@echo "  make prod-baseline-snapshot     # guarda snapshot operativo en artifacts/week1-baseline"
	@echo "  make prod-init                  # levanta servicios base y crea contenedores de modelos/comfy"
	@echo "  make prod-up                    # levanta stack base y asegura contenedores de modelos/comfy"
	@echo "  make prod-up-admin              # fuerza arranque solo de admin-panel"
	@echo "  make prod-build-admin           # rebuild del panel admin"
	@echo "  make prod-down                  # apaga todo"
	@echo "  make prod-ps                    # estado de contenedores (incluye parados/fallidos)"
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
	@echo "  make prod-ports-audit          # valida exposicion publica: solo 80/443 y 9000 loopback"
	@echo "  make prod-proxy-check          # chequea edge proxy: /, /admin, /admin-api, /comfy/"
	@echo "  make prod-admin-url            # URL del panel admin"
	@echo "  make prod-openwebui-url        # URL publica de OpenWebUI (edge)"
	@echo "  make prod-comfyui-url          # URL publica de ComfyUI (edge)"
	@echo ""
	@echo "Upgrade:"
	@echo "  make prod-upgrade-precheck"
	@echo "  make prod-upgrade-canary"
	@echo "  make prod-upgrade-verify"
	@echo "  make prod-upgrade-promote"
	@echo "  make prod-upgrade-rollback ROLLBACK_REF=<git-ref>"
	@echo ""
	@echo "Logs (legacy):"
	@echo "  make prod-logs-all TAIL=200"
	@echo "  make prod-logs SERVICE=litellm TAIL=200"
	@echo "  make prod-logs-container CONTAINER=vllm-... TAIL=200"
	@echo "  make prod-logs-<servicio> TAIL=200"

# --- Guardrails de entorno / imagenes ---
prod-preflight-env:
	@set -eu; \
	if [ -f ./.env ]; then \
	  while IFS='=' read -r raw_key raw_value; do \
	    case "$$raw_key" in ""|\#*) continue ;; esac; \
	    key=$$(printf '%s' "$$raw_key" | tr -d ' '); \
	    [ -n "$$key" ] || continue; \
	    eval "current=\$${$$key-}"; \
	    if [ -n "$$current" ]; then \
	      continue; \
	    fi; \
	    value=$$(printf '%s' "$$raw_value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//'); \
	    value=$${value#\"}; \
	    value=$${value%\"}; \
	    eval "export $$key=\"$$value\""; \
	  done < ./.env; \
	fi; \
	check_required() { \
	  name="$$1"; \
	  eval "value=\$${$$name-}"; \
	  if [ -z "$$value" ]; then \
	    echo "ERROR: variable requerida no definida: $$name"; exit 1; \
	  fi; \
	  case "$$value" in \
	    change_me|cambiaLAclave|changeme_pg|change-this-jwt-secret) \
	      echo "ERROR: $$name usa un placeholder inseguro ($$value)"; exit 1 ;; \
	  esac; \
	}; \
	check_entropy() { \
	  name="$$1"; min_len="$$2"; \
	  eval "value=\$${$$name-}"; \
	  len=$$(printf '%s' "$$value" | wc -c | tr -d ' '); \
	  if [ "$$len" -lt "$$min_len" ]; then \
	    echo "ERROR: $$name debe tener longitud minima $$min_len (actual=$$len)"; exit 1; \
	  fi; \
	  classes=0; \
	  printf '%s' "$$value" | grep -q '[a-z]' && classes=$$((classes+1)) || true; \
	  printf '%s' "$$value" | grep -q '[A-Z]' && classes=$$((classes+1)) || true; \
	  printf '%s' "$$value" | grep -q '[0-9]' && classes=$$((classes+1)) || true; \
	  printf '%s' "$$value" | grep -q '[^A-Za-z0-9]' && classes=$$((classes+1)) || true; \
	  if [ "$$classes" -lt 3 ]; then \
	    echo "ERROR: $$name necesita al menos 3 clases de caracteres (a-z/A-Z/0-9/simbolos)"; exit 1; \
	  fi; \
	}; \
	check_required MODEL_SWITCHER_TOKEN; \
	check_required POSTGRES_PASSWORD; \
	check_required LITELLM_KEY; \
	check_required ADMIN_JWT_SECRET; \
	check_required MODEL_SWITCHER_DEFAULT; \
	if [ -n "$${MODEL_SWITCHER_ADMIN_TOKEN:-}" ]; then \
	  echo "WARN: MODEL_SWITCHER_ADMIN_TOKEN esta deprecado y se ignora. Usa solo MODEL_SWITCHER_TOKEN"; \
	fi; \
	check_entropy MODEL_SWITCHER_TOKEN 24; \
	check_entropy POSTGRES_PASSWORD 16; \
	check_entropy LITELLM_KEY 24; \
	check_entropy ADMIN_JWT_SECRET 32; \
	echo "[ok] preflight de entorno completado."

prod-image-lock-check:
	@set -eu; \
	if [ -f ./versions.lock ]; then set -a; . ./versions.lock; set +a; fi; \
	for var in POSTGRES_IMAGE LITELLM_IMAGE OPENWEBUI_IMAGE DOCKER_SOCKET_PROXY_IMAGE EDGE_PROXY_IMAGE VLLM_IMAGE_FAST VLLM_IMAGE_QUALITY VLLM_IMAGE_DEEPSEEK VLLM_IMAGE_QWEN_MAX COMFYUI_IMAGE MODEL_SWITCHER_DYNAMIC_VLLM_IMAGE; do \
	  eval "value=\$${$$var-}"; \
	  [ -n "$$value" ] || continue; \
	  case "$$value" in \
	    *:latest|*:main) \
	      echo "ERROR: $$var usa tag no determinista ($$value)"; \
	      exit 1 ;; \
	  esac; \
	done; \
	for var in LITELLM_IMAGE OPENWEBUI_IMAGE; do \
	  eval "value=\$${$$var-}"; \
	  [ -n "$$value" ] || continue; \
	  case "$$value" in \
	    *@sha256:*) ;; \
	    *) \
	      echo "ERROR: $$var debe ir pinneada por digest @sha256 ($$value)"; \
	      exit 1 ;; \
	  esac; \
	done; \
	echo "[ok] image lock check completado."

prod-baseline-snapshot:
	@set -eu; \
	TS=$$(date -u +%Y%m%dT%H%M%SZ); \
	DIR=artifacts/week1-baseline/$$TS; \
	mkdir -p "$$DIR"; \
	$(MAKE) prod-ps > "$$DIR/prod-ps.txt"; \
	$(MAKE) prod-status > "$$DIR/prod-status.json"; \
	$(MAKE) prod-mode-status > "$$DIR/prod-mode-status.json"; \
	echo "[ok] baseline guardado en $$DIR"

# --- UX simplificada ---
up: prod-preflight-env prod-image-lock-check
	@set -eu; \
	case "$(MODE)" in \
	  all) \
	    $(MAKE) prod-up ;; \
	  infra) \
	    $(PROD) --profile webui up -d --remove-orphans $(PROD_BASE_SERVICES) ;; \
	  models) \
	    $(PROD) $(PROD_MODEL_PROFILES) up -d --remove-orphans vllm-fast vllm-quality vllm-deepseek vllm-qwen32b comfyui ;; \
	  *) \
	    echo "ERROR: MODE invalido: $(MODE). Usa MODE=all|infra|models"; exit 1 ;; \
	esac

down:
	@$(MAKE) prod-down

purge:
	@set -eu; \
	[ "$(CONFIRM)" = "YES" ] || { echo "ERROR: purge es destructivo. Usa CONFIRM=YES"; exit 1; }; \
	case "$(SCOPE)" in \
	  project) \
	    echo "[purge] scope=project"; \
	    $(PROD) $(PROD_ALL_PROFILES) down --remove-orphans --volumes --rmi all || true; \
	    docker builder prune -af || true; \
	    echo "[ok] reset de proyecto completado." ;; \
	  host) \
	    echo "[purge] scope=host (docker completo)"; \
	    IDS=$$(docker ps -q); \
	    if [ -n "$$IDS" ]; then docker stop $$IDS; fi; \
	    ALL_IDS=$$(docker ps -aq); \
	    if [ -n "$$ALL_IDS" ]; then docker rm -f $$ALL_IDS; fi; \
	    docker system prune -af --volumes; \
	    docker builder prune -af || true; \
	    echo "[ok] reset completo de docker host completado." ;; \
	  *) \
	    echo "ERROR: SCOPE invalido: $(SCOPE). Usa SCOPE=project|host"; exit 1 ;; \
	esac

logs:
	@set -eu; \
	if [ "$(TARGET)" = "all" ]; then \
	  $(PROD) logs -f --tail=$(TAIL); \
	  exit 0; \
	fi; \
	case " $(PROD_COMPOSE_SERVICES) " in \
	  *" $(TARGET) "*) \
	    $(PROD) logs -f --tail=$(TAIL) "$(TARGET)" ;; \
	  *) \
	    docker logs -f --tail=$(TAIL) "$(TARGET)" ;; \
	esac

start:
	@set -eu; \
	[ "$(TARGET)" != "all" ] || { echo "ERROR: define TARGET=<servicio|contenedor>"; exit 1; }; \
	case " $(PROD_COMPOSE_SERVICES) " in \
	  *" $(TARGET) "*) \
	    $(PROD) $(PROD_ALL_PROFILES) up -d "$(TARGET)" ;; \
	  *) \
	    docker start "$(TARGET)" ;; \
	esac

stop:
	@set -eu; \
	[ "$(TARGET)" != "all" ] || { echo "ERROR: define TARGET=<servicio|contenedor>"; exit 1; }; \
	case " $(PROD_COMPOSE_SERVICES) " in \
	  *" $(TARGET) "*) \
	    $(PROD) $(PROD_ALL_PROFILES) stop "$(TARGET)" ;; \
	  *) \
	    docker stop "$(TARGET)" ;; \
	esac

# --- Ciclo de vida ---
prod-init: prod-preflight-env prod-image-lock-check
	@$(MAKE) prod-up

prod-up: prod-preflight-env prod-image-lock-check
	@$(PROD) --profile webui up -d --remove-orphans $(PROD_BASE_SERVICES)
	@$(PROD) $(PROD_MODEL_PROFILES) create vllm-fast vllm-quality vllm-deepseek vllm-qwen32b comfyui
prod-up-admin:         ; $(PROD) --profile webui up -d --remove-orphans admin-panel
prod-build-admin:      ; $(PROD) --profile webui build admin-panel && $(PROD) --profile webui up -d --remove-orphans admin-panel
prod-build-switcher:   ; $(PROD) --profile webui build model-switcher
prod-bootstrap-models: ; $(PROD) $(PROD_MODEL_PROFILES) create vllm-fast vllm-quality vllm-deepseek vllm-qwen32b comfyui
prod-down:             ; $(PROD) $(PROD_ALL_PROFILES) down --remove-orphans
prod-ps:               ; $(PROD) $(PROD_ALL_PROFILES) ps --all
prod-pull:             ; $(PROD) $(PROD_ALL_PROFILES) pull
prod-restart:          ; $(PROD) --profile webui restart $(PROD_BASE_SERVICES)

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
prod-admin-url:    ; @echo "$(ADMIN_URL)"
prod-openwebui-url:; @echo "$(OPENWEBUI_URL)"
prod-comfyui-url:  ; @echo "$(COMFYUI_URL)"
prod-list-models:  ; curl -s $(SWITCHER_URL)/models -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-stop-models:  ; curl -s $(SWITCHER_URL)/stop -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-comfy-on:     ; curl -s $(SWITCHER_URL)/mode/switch -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d '{"mode":"comfy","ttl_minutes":$(COMFY_TTL)}' | jq
prod-comfy-off:    ; curl -s $(SWITCHER_URL)/mode/switch -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d '{"mode":"llm","model":"$(MODEL)"}' | jq
prod-llm-priority: ; curl -s $(SWITCHER_URL)/mode/release -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d '{}' | jq

# --- Upgrade canary ---
prod-upgrade-precheck: prod-preflight-env prod-image-lock-check
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
	  echo "Llamada usada: GET $(SWITCHER_URL)/healthz/ready"; \
	  curl -sf "$(SWITCHER_URL)/healthz/ready" -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq -e '.status == "ready"' >/dev/null; \
	  echo "OK: modo llm listo"; \
	elif [ "$$MODE" = "comfy" ]; then \
	  STATUS_JSON=$$(curl -sf "$(SWITCHER_URL)/status" -H "Authorization: Bearer $(SWITCHER_TOKEN)"); \
	  COMFY_STATUS=$$(printf '%s' "$$STATUS_JSON" | jq -r '.comfyui.status // empty'); \
	  COMFY_HEALTH=$$(printf '%s' "$$STATUS_JSON" | jq -r '.comfyui.health // "none"'); \
	  [ "$$COMFY_STATUS" = "running" ] || { echo "ERROR: comfyui no esta running (status=$$COMFY_STATUS)"; exit 1; }; \
	  if [ "$$COMFY_HEALTH" = "unhealthy" ]; then \
	    echo "ERROR: comfyui esta unhealthy"; exit 1; \
	  fi; \
	  echo "OK: modo comfy activo (health=$$COMFY_HEALTH)"; \
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

prod-ports-check:
	@$(MAKE) prod-ports-audit

prod-ports-audit:
	@set -eu; \
	command -v ss >/dev/null 2>&1 || { echo "ERROR: 'ss' no disponible"; exit 1; }; \
	for port in 80 443; do \
	  if ! ss -ltnH "( sport = :$$port )" | grep -q .; then \
	    echo "ERROR: puerto requerido no publicado: $$port"; exit 1; \
	  fi; \
	done; \
	for blocked in 3000 4000 8001 8002 8003 8004 8188; do \
	  if ss -ltnH "( sport = :$$blocked )" | grep -q .; then \
	    echo "ERROR: puerto no permitido publicado: $$blocked"; exit 1; \
	  fi; \
	done; \
	if ss -ltnH "( sport = :9000 )" | grep -q .; then \
	  BAD=$$(ss -ltnH "( sport = :9000 )" | awk '{print $$4}' | grep -Ev '^(127\.0\.0\.1|::1|\[::1\]):9000$$' || true); \
	  if [ -n "$$BAD" ]; then \
	    echo "ERROR: puerto 9000 debe ser loopback-only; listeners invalidos: $$BAD"; \
	    exit 1; \
	  fi; \
	fi; \
	echo "OK: audit de puertos completado (80/443 publicos, backends cerrados, 9000 loopback)."

prod-proxy-check:
	@set -e; \
	CODE_ROOT=$$(curl -k -s -o /dev/null -w '%{http_code}' "$(HOST_BASE_URL)/"); \
	case "$$CODE_ROOT" in \
	  200|301|302|307|308) echo "OK: $(HOST_BASE_URL)/ -> $$CODE_ROOT" ;; \
	  *) echo "ERROR: $(HOST_BASE_URL)/ -> $$CODE_ROOT"; exit 1 ;; \
	esac; \
	CODE_ADMIN=$$(curl -k -s -o /dev/null -w '%{http_code}' "$(ADMIN_URL)"); \
	case "$$CODE_ADMIN" in \
	  200|301|302|307|308) echo "OK: $(ADMIN_URL) -> $$CODE_ADMIN" ;; \
	  *) echo "ERROR: $(ADMIN_URL) -> $$CODE_ADMIN"; exit 1 ;; \
	esac; \
	CODE_ADMIN_API=$$(curl -k -s -o /dev/null -w '%{http_code}' "$(ADMIN_API_URL)/status" || true); \
	case "$$CODE_ADMIN_API" in \
	  401|403) echo "OK: $(ADMIN_API_URL)/status protegido -> $$CODE_ADMIN_API" ;; \
	  *) echo "ERROR: $(ADMIN_API_URL)/status deberia requerir auth (actual=$$CODE_ADMIN_API)"; exit 1 ;; \
	esac; \
	CODE_COMFY=$$(curl -k -s -o /dev/null -w '%{http_code}' "$(COMFYUI_URL)" || true); \
	case "$$CODE_COMFY" in \
	  401|403) echo "OK: $(COMFYUI_URL) protegido -> $$CODE_COMFY" ;; \
	  *) echo "ERROR: $(COMFYUI_URL) deberia estar protegido (actual=$$CODE_COMFY)"; exit 1 ;; \
	esac

# --- Logs ---
prod-logs-list:
	@echo "Usa make logs TARGET=<all|servicio|contenedor> TAIL=200"
	@echo "Servicios compose: $(PROD_COMPOSE_SERVICES)"
	@echo "(dynamic) ejemplo: make logs TARGET=vllm-<id-dinamico> TAIL=200"

prod-logs-all:
	@$(MAKE) logs TARGET=all TAIL=$(TAIL)

prod-logs:
ifeq ($(strip $(SERVICE)),)
	@$(MAKE) logs TARGET=all TAIL=$(TAIL)
else
	@$(MAKE) logs TARGET=$(SERVICE) TAIL=$(TAIL)
endif

prod-logs-container:
	@test -n "$(CONTAINER)" || { echo "ERROR: define CONTAINER=<docker-container-name>"; exit 1; }
	@$(MAKE) logs TARGET=$(CONTAINER) TAIL=$(TAIL)

prod-logs-%:
	@$(MAKE) logs TARGET=$* TAIL=$(TAIL)

.PHONY: help up down purge logs start stop \
        prod-preflight-env prod-image-lock-check prod-baseline-snapshot \
        prod-init prod-up prod-up-admin prod-build-admin prod-build-switcher prod-bootstrap-models prod-down prod-ps prod-pull prod-restart \
        prod-switch prod-switch-async prod-register-model prod-unregister-model prod-status prod-mode-status prod-admin-url prod-openwebui-url prod-comfyui-url prod-list-models prod-stop-models prod-comfy-on prod-comfy-off prod-comfy-on-safe prod-llm-priority prod-gpu-preflight prod-test prod-test-auto prod-test-auto-ext prod-ports-check prod-ports-audit prod-proxy-check \
        prod-upgrade-precheck prod-upgrade-canary prod-upgrade-verify prod-upgrade-promote prod-upgrade-rollback \
        prod-logs-list prod-logs-all prod-logs prod-logs-container prod-logs-%
