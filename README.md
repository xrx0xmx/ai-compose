# ai-compose (LiteLLM + vLLM/Ollama + Open WebUI)

## Estructura

```
docker-compose.yml          # Base: LiteLLM + Open WebUI
docker-compose.local.yml    # Override local: Ollama (Mac, sin GPU)
docker-compose.prod.yml     # Override prod: vLLM + NVIDIA GPU
litellm-config.yml          # Config LiteLLM → vLLM (producción)
litellm-config.local.yml    # Config LiteLLM → Ollama (local)
Makefile                    # Atajos local-* y prod-*
```

## Probar en local (Mac)

```bash
make local-up        # Arranca LiteLLM + Ollama
make local-init      # Descarga qwen2.5:7b en Ollama (solo la primera vez)
make test            # Smoke test
make local-web       # Añade Open WebUI → http://localhost:3000
make local-down      # Para todo
```

## Producción (servidor con GPU)

Directorios en el servidor (propiedad de aiservices:aiservices):
- `/opt/ai/compose/`         — este proyecto
- `/opt/ai/hf-cache/`        — cache HuggingFace compartida
- `/opt/ai/litellm-db/`      — SQLite de LiteLLM
- `/opt/ai/openwebui-data/`  — datos de Open WebUI

```bash
make prod-fast       # LiteLLM + vLLM Qwen 7B
make prod-quality    # LiteLLM + vLLM Qwen 14B AWQ
make prod-web        # Añade Open WebUI
make prod-down       # Para todo
```

## Smoke tests (ambos entornos)

```bash
make models          # Lista modelos en LiteLLM
make test            # Chat completion contra qwen-fast
```
