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

WAIT_TIMEOUT ?= 300

define wait-healthy
	@echo "⏳ Waiting for $(1) to be healthy..."
	@elapsed=0; \
	while [ $$elapsed -lt $(WAIT_TIMEOUT) ]; do \
		status=$$(docker inspect --format='{{.State.Health.Status}}' $(1) 2>/dev/null); \
		if [ "$$status" = "healthy" ]; then \
			echo "✅ $(1) is healthy and ready!"; \
			exit 0; \
		fi; \
		sleep 5; \
		elapsed=$$((elapsed + 5)); \
		echo "⏳ Waiting for $(1) to be healthy... ($${elapsed}s)"; \
	done; \
	echo "❌ Timeout: $(1) did not become healthy within $(WAIT_TIMEOUT)s"; \
	exit 1
endef

prod-qwen-fast:    ; ln -sf /opt/ai/compose/litellm-config.qwen-fast.yml /opt/ai/compose/litellm-active.yml && $(PROD) --profile qwen-fast --profile webui up -d
	$(call wait-healthy,vllm-fast)
prod-qwen-quality: ; ln -sf /opt/ai/compose/litellm-config.qwen-quality.yml /opt/ai/compose/litellm-active.yml && $(PROD) --profile qwen-quality --profile webui up -d
	$(call wait-healthy,vllm-quality)
prod-deepseek:     ; ln -sf /opt/ai/compose/litellm-config.deepseek.yml /opt/ai/compose/litellm-active.yml && $(PROD) --profile deepseek --profile webui up -d
	$(call wait-healthy,vllm-deepseek)
prod-qwen-max:     ; ln -sf /opt/ai/compose/litellm-config.qwen-max.yml /opt/ai/compose/litellm-active.yml && $(PROD) --profile qwen-max --profile webui up -d
	$(call wait-healthy,vllm-qwen32b)
prod-down:         ; $(PROD) --profile qwen-fast --profile qwen-quality --profile deepseek --profile qwen-max --profile webui down
prod-ps:           ; $(PROD) ps
prod-logs:         ; $(PROD) logs -f --tail=200
prod-pull:         ; $(PROD) --profile qwen-fast --profile qwen-quality --profile deepseek --profile qwen-max --profile webui pull
prod-restart:      ; $(PROD) restart
prod-switch:       ; sudo /opt/ai/compose/scripts/switch-model.sh switch $(MODEL)
prod-status:       ; /opt/ai/compose/scripts/switch-model.sh status
prod-list-models:  ; /opt/ai/compose/scripts/switch-model.sh list
prod-stop-models:  ; sudo /opt/ai/compose/scripts/switch-model.sh stop

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
        prod-qwen-fast prod-qwen-quality prod-deepseek prod-qwen-max prod-down prod-ps prod-logs prod-pull prod-restart \
        prod-switch prod-status prod-list-models prod-stop-models \
        models test-qwen-fast test-qwen-quality test-deepseek test-qwen-max vpn-up vpn-down vpn-status ssh
