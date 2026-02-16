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

prod-fast:     ; $(PROD) --profile fast up -d
prod-quality:  ; $(PROD) --profile quality up -d
prod-web:      ; $(PROD) --profile fast --profile webui up -d
prod-all:      ; $(PROD) --profile fast --profile webui up -d
prod-down:     ; $(PROD) --profile fast --profile quality --profile webui down
prod-ps:       ; $(PROD) ps
prod-logs:     ; $(PROD) logs -f --tail=200
prod-pull:     ; $(PROD) --profile fast --profile quality --profile webui pull
prod-restart:  ; $(PROD) restart

# --- Smoke tests (funcionan en ambos entornos) ---
models:  ; curl -s http://127.0.0.1:4000/v1/models -H "Authorization: Bearer $(KEY)" | jq
test:    ; curl -s http://127.0.0.1:4000/v1/chat/completions \
           -H "Authorization: Bearer $(KEY)" \
           -H "Content-Type: application/json" \
           -d '{"model":"qwen-fast","messages":[{"role":"user","content":"Di hola en castellano."}],"temperature":0.2}' \
           | jq -r '.choices[0].message.content'

.PHONY: local-up local-web local-down local-ps local-logs local-pull local-init \
        prod-fast prod-quality prod-web prod-all prod-down prod-ps prod-logs prod-pull prod-restart \
        models test
