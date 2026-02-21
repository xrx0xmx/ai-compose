# ============================================================
# Makefile — local (Mac/Ollama) y prod (servidor/vLLM+GPU)
# ============================================================

# --- Local (Mac) ---
LOCAL=docker compose -f docker-compose.yml -f docker-compose.local.yml
KEY ?= $(or $(LITELLM_KEY),cambiaLAclave)
SWITCHER_TOKEN ?= $(MODEL_SWITCHER_TOKEN)
SWITCHER_TOKEN := $(or $(SWITCHER_TOKEN),change_me)
SWITCHER_URL ?= http://127.0.0.1:9000

local-up:      ; $(LOCAL) up -d
local-web:     ; $(LOCAL) --profile webui up -d
local-down:    ; $(LOCAL) --profile webui down --remove-orphans
local-ps:      ; $(LOCAL) --profile webui ps -a
local-logs:    ; $(LOCAL) --profile webui logs -f --tail=200
local-pull:    ; $(LOCAL) --profile webui pull

# Ollama: descargar modelo (ejecutar una vez)
local-init:    ; docker exec ollama ollama pull qwen2.5:7b

# --- Producción (servidor con GPU) ---
PROD_DIR ?= /opt/ai/compose
PROD_COMPOSE=docker compose -f docker-compose.yml -f docker-compose.prod.yml
PROD=cd $(PROD_DIR) && $(PROD_COMPOSE)
PROD_MODEL_PROFILES=--profile qwen-fast --profile qwen-quality --profile deepseek --profile qwen-max

prod-bootstrap-models: ; $(PROD) $(PROD_MODEL_PROFILES) create vllm-fast vllm-quality vllm-deepseek vllm-qwen32b
prod-build-switcher:  ; $(PROD) --profile webui build model-switcher
prod-up:           ; $(PROD) --profile webui up -d
prod-qwen-fast:
	@$(MAKE) prod-up
	@$(MAKE) prod-bootstrap-models
	@$(MAKE) prod-switch MODEL=qwen-fast
prod-qwen-quality:
	@$(MAKE) prod-up
	@$(MAKE) prod-bootstrap-models
	@$(MAKE) prod-switch MODEL=qwen-quality
prod-deepseek:
	@$(MAKE) prod-up
	@$(MAKE) prod-bootstrap-models
	@$(MAKE) prod-switch MODEL=deepseek
prod-qwen-max:
	@$(MAKE) prod-up
	@$(MAKE) prod-bootstrap-models
	@$(MAKE) prod-switch MODEL=qwen-max
prod-down:         ; $(PROD) --profile qwen-fast --profile qwen-quality --profile deepseek --profile qwen-max --profile webui down
prod-ps:           ; $(PROD) ps
prod-logs:         ; $(PROD) logs -f --tail=200
prod-pull:         ; $(PROD) --profile qwen-fast --profile qwen-quality --profile deepseek --profile qwen-max --profile webui pull
prod-restart:      ; $(PROD) restart
prod-switch:       ; curl -s $(SWITCHER_URL)/switch -H "Authorization: Bearer $(SWITCHER_TOKEN)" -H "Content-Type: application/json" -d '{"model":"$(MODEL)"}' | jq
prod-status:       ; curl -s $(SWITCHER_URL)/status -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-list-models:  ; curl -s $(SWITCHER_URL)/models -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq
prod-stop-models:  ; curl -s $(SWITCHER_URL)/stop -H "Authorization: Bearer $(SWITCHER_TOKEN)" | jq

# --- Smoke tests (funcionan en ambos entornos) ---
models:             ; curl -s http://127.0.0.1:4000/v1/models -H "Authorization: Bearer $(KEY)" | jq
test-qwen-fast:     ; curl -s http://127.0.0.1:4000/v1/chat/completions \
                      -H "Authorization: Bearer $(KEY)" \
                      -H "Content-Type: application/json" \
                      -d '{"model":"qwen-fast","messages":[{"role":"user","content":"Di hola en castellano."}],"temperature":0.2}' \
                      | jq -r '.choices[0].message.content'
test-qwen-quality:  ; curl -s http://127.0.0.1:4000/v1/chat/completions \
                      -H "Authorization: Bearer $(KEY)" \
                      -H "Content-Type: application/json" \
                      -d '{"model":"qwen-quality","messages":[{"role":"user","content":"Di hola en castellano."}],"temperature":0.2}' \
                      | jq -r '.choices[0].message.content'
test-deepseek:      ; curl -s http://127.0.0.1:4000/v1/chat/completions \
                      -H "Authorization: Bearer $(KEY)" \
                      -H "Content-Type: application/json" \
                      -d '{"model":"deepseek-r1","messages":[{"role":"user","content":"Di hola en castellano."}],"temperature":0.2}' \
                      | jq -r '.choices[0].message.content'
test-qwen-max:      ; curl -s http://127.0.0.1:4000/v1/chat/completions \
                      -H "Authorization: Bearer $(KEY)" \
                      -H "Content-Type: application/json" \
                      -d '{"model":"qwen-max","messages":[{"role":"user","content":"Di hola en castellano."}],"temperature":0.2}' \
                      | jq -r '.choices[0].message.content'

# --- VPN (WireGuard) ---
vpn-up:    ; sudo wg-quick up somia-adam
vpn-down:  ; sudo wg-quick down somia-adam
vpn-status: ; sudo wg show

# --- SSH al servidor de producción ---
ssh:       ; ssh somia

.PHONY: local-up local-web local-down local-ps local-logs local-pull local-init \
        prod-bootstrap-models prod-build-switcher prod-up prod-qwen-fast prod-qwen-quality prod-deepseek prod-qwen-max prod-down prod-ps prod-logs prod-pull prod-restart \
        prod-switch prod-status prod-list-models prod-stop-models \
        models test-qwen-fast test-qwen-quality test-deepseek test-qwen-max vpn-up vpn-down vpn-status ssh
