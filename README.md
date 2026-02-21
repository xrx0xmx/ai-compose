# ai-compose (LiteLLM + vLLM/Ollama + Open WebUI)

## Estructura

```
docker-compose.yml          # Base: LiteLLM + Open WebUI
docker-compose.local.yml    # Override local: Ollama (Mac, sin GPU)
docker-compose.prod.yml     # Override prod: vLLM + NVIDIA GPU
litellm-config.yml          # Config LiteLLM → vLLM (producción)
litellm-config.local.yml    # Config LiteLLM → Ollama (local)
Makefile                    # Atajos local-* y prod-*
control/                    # API HTTP para cambiar modelos
control/Dockerfile          # Imagen del model switcher
```

## Regla operativa

Usa siempre `make` para operaciones de Docker en este proyecto.

## Probar en local (Mac)

```bash
make local-up
make local-init
make local-web
make local-down
```

## Producción (servidor con GPU)

Directorios en el servidor (propiedad de aiservices:aiservices):
- `/opt/ai/compose/`         — este proyecto
- `/opt/ai/hf-cache/`        — cache HuggingFace compartida
- `/opt/ai/postgres/`        — datos de Postgres
- `/opt/ai/openwebui-data/`  — datos de Open WebUI

### Inicialización recomendada (una vez)

```bash
cd /opt/ai/compose
make prod-bootstrap-models
```

Esto crea los contenedores `vllm-*` para evitar errores `container not found` al hacer switch.

### Arranque por perfil

```bash
make prod-qwen-fast
make prod-qwen-quality
make prod-deepseek
make prod-qwen-max
make prod-down
```

## Model switcher (control desde Open WebUI)

Permite cambiar modelo desde un Tool OpenAPI en Open WebUI.

### 1) Configurar token y defaults (host)

```bash
cd /opt/ai/compose
printf "MODEL_SWITCHER_TOKEN=tu_token_seguro\nMODEL_SWITCHER_DEFAULT=qwen-fast\n" >> .env
```

### 2) Verificar estado

```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-status
```

### 3) Configurar Tool en Open WebUI (admin)

- URL OpenAPI: `http://model-switcher:9000/openapi.json`
- Header: `Authorization: Bearer tu_token_seguro`
- Restringir el Tool a usuarios admin.

### 4) Prueba rápida desde host

```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-list-models
MODEL_SWITCHER_TOKEN=tu_token_seguro MODEL=qwen-fast make prod-switch
```

## Endpoints del switcher

- `GET /health`
- `GET /healthz/ready`
- `GET /models`
- `GET /status`
- `POST /switch` body: `{"model":"qwen-fast|qwen-quality|deepseek|qwen-max"}`
- `POST /stop`

`POST /switch` devuelve estado extendido: `status`, `from_model`, `to_model`, `steps`, `duration_ms`, `running_models`, `active_model`, `containers`.

## Smoke tests

```bash
make models
make test-qwen-fast
make test-qwen-quality
make test-deepseek
make test-qwen-max
```
