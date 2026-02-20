# ai-compose (LiteLLM + vLLM/Ollama + Open WebUI + Model Switcher)

## Estructura

```
docker-compose.yml          # Base: LiteLLM + Open WebUI
docker-compose.local.yml    # Override local: Ollama (Mac, sin GPU)
docker-compose.prod.yml     # Override prod: vLLM + NVIDIA GPU + model-switcher + gateway
litellm-config.local.yml    # Config LiteLLM → Ollama (local)
litellm-config.qwen-*.yml   # Configs LiteLLM por perfil de producción
model-switcher/             # API + UI para cambio transaccional de modelo
Makefile                    # Atajos local-* y prod-*
```

## Probar en local (Mac)

```bash
make local-up        # Arranca LiteLLM + Ollama
make local-init      # Descarga qwen2.5:7b en Ollama (solo la primera vez)
make local-web       # Añade Open WebUI → http://localhost:3000
make local-down      # Para todo
```

## Producción (servidor con GPU)

Directorios en el servidor (propiedad de aiservices:aiservices):
- `/opt/ai/compose/`         — este proyecto
- `/opt/ai/hf-cache/`        — cache HuggingFace compartida
- `/opt/ai/postgres/`        — datos de Postgres
- `/opt/ai/openwebui-data/`  — datos de Open WebUI

Configura el token admin para el panel de switch:

```bash
export MODEL_SWITCHER_ADMIN_TOKEN='cambia-este-token-admin'
```

Arranque por perfil de modelo:

```bash
make prod-qwen-fast
make prod-qwen-quality
make prod-deepseek
make prod-qwen-max
make prod-down
```

Con perfil `webui` activo:
- `http://localhost:3000/`           → Open WebUI
- `http://localhost:3000/ops/models` → Panel admin de cambio de modelo

## Switch de modelo con 1 GPU

- Catálogo dinámico desde labels `ai.model.*` en `docker-compose.prod.yml`.
- Flujo seguro: stop inferencia + LiteLLM, swap de `litellm-active.yml`, up del modelo destino, healthcheck, up LiteLLM.
- Rollback automático al modelo previo si una etapa falla.
- Estado persistido en `/opt/ai/compose/.active-model.json`.
- Auditoría append-only en `/opt/ai/compose/model-switcher-audit.log`.

## Smoke tests

```bash
make models
make test-qwen-fast
make test-qwen-quality
make test-deepseek
make test-qwen-max
```
