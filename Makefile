# ============================================================
# Makefile — local (Mac/Ollama) y prod (servidor/vLLM+GPU)
# ============================================================

# --- Local (Mac) ---
LOCAL=docker compose -f docker-compose.yml -f docker-compose.local.yml
KEY ?= cambiaLAclave

local-up:      ; $(LOCAL) up -d
local-web:     ; $(LOCAL) --profile webui up -d
local-down:    ; $(LOCAL) --profile webui down --remove-orphans
local-ps:      ; $(LOCAL) --profile webui ps -a
local-logs:    ; $(LOCAL) --profile webui logs -f --tail=200
local-pull:    ; $(LOCAL) --profile webui pull

# Ollama: descargar modelo (ejecutar una vez)
local-init:    ; docker exec ollama ollama pull qwen2.5:7b

# --- Producción (servidor con GPU) ---
PROD=cd /opt/ai/compose && docker compose -f docker-compose.yml -f docker-compose.prod.yml

prod-qwen-fast:    ; ln -sf /opt/ai/compose/litellm-config.qwen-fast.yml /opt/ai/compose/litellm-active.yml && $(PROD) --profile qwen-fast --profile webui up -d
prod-qwen-quality: ; ln -sf /opt/ai/compose/litellm-config.qwen-quality.yml /opt/ai/compose/litellm-active.yml && $(PROD) --profile qwen-quality --profile webui up -d
prod-deepseek:     ; ln -sf /opt/ai/compose/litellm-config.deepseek.yml /opt/ai/compose/litellm-active.yml && $(PROD) --profile deepseek --profile webui up -d
prod-down:         ; $(PROD) --profile qwen-fast --profile qwen-quality --profile deepseek --profile webui down
prod-ps:           ; $(PROD) ps
prod-logs:         ; $(PROD) logs -f --tail=200
prod-pull:         ; $(PROD) --profile qwen-fast --profile qwen-quality --profile deepseek --profile webui pull
prod-restart:      ; $(PROD) restart

# --- Smoke tests (funcionan en ambos entornos) ---
MODEL ?= qwen-fast
models:  ; curl -s http://127.0.0.1:4000/v1/models -H "Authorization: Bearer $(KEY)" | jq
test:    ; curl -s http://127.0.0.1:4000/v1/chat/completions \
           -H "Authorization: Bearer $(KEY)" \
           -H "Content-Type: application/json" \
           -d '{"model":"$(MODEL)","messages":[{"role":"user","content":"Di hola en castellano."}],"temperature":0.2}' \
           | jq -r '.choices[0].message.content'

# --- VPN (WireGuard) ---
vpn-up:    ; sudo wg-quick up somia-adam
vpn-down:  ; sudo wg-quick down somia-adam
vpn-status: ; sudo wg show

# --- SSH al servidor de producción ---
ssh:       ; ssh somia

.PHONY: local-up local-web local-down local-ps local-logs local-pull local-init \
        prod-qwen-fast prod-qwen-quality prod-deepseek prod-down prod-ps prod-logs prod-pull prod-restart \
        models test vpn-up vpn-down vpn-status ssh
