# ai-compose (LiteLLM + vLLM + ComfyUI + Open WebUI)

## Estructura

```
docker-compose.yml          # Base: LiteLLM + Open WebUI
docker-compose.local.yml    # Override local: Ollama (Mac, sin GPU)
docker-compose.prod.yml     # Override prod: vLLM + ComfyUI + NVIDIA GPU
litellm-config.yml          # Config LiteLLM → vLLM (producción)
litellm-config.local.yml    # Config LiteLLM → Ollama (local)
Makefile                    # Atajos local-* y prod-*
Makefile.ops                # Comandos operativos (VPN/SSH)
control/                    # API HTTP para cambiar modelos y modo llm/comfy
control/Dockerfile          # Imagen del model switcher
```

## Regla operativa

Usa siempre `make` para operaciones de Docker en este proyecto.

## Producción (servidor con GPU)

Directorios en el servidor (propiedad de aiservices:aiservices):
- `/opt/ai/compose/`         — este proyecto
- `/opt/ai/hf-cache/`        — cache HuggingFace compartida
- `/opt/ai/postgres/`        — datos de Postgres
- `/opt/ai/openwebui-data/`  — datos de Open WebUI
- `/opt/ai/comfyui-data/`    — datos persistentes de ComfyUI

Imagen de ComfyUI (override opcional):
- default: `yanwk/comfyui-boot:cu126-slim`
- override en `.env`: `COMFYUI_IMAGE=<tu_imagen>`

### Inicialización recomendada (una vez)

```bash
cd /opt/ai/compose
make prod-bootstrap-models
```

Esto crea los contenedores `vllm-*` para evitar errores `container not found` al hacer switch.
También crea `comfyui` para permitir el modo imagen bajo demanda.

### Arranque base

```bash
make prod-init
```

Esto levanta servicios base y crea contenedores de modelos/comfy.

### Modo de carga (exclusión estricta GPU)

`model-switcher` gestiona dos modos excluyentes en una sola GPU:
- `llm` (default): LiteLLM + un único `vllm-*` activo.
- `comfy`: ComfyUI activo, `litellm` + `vllm-*` detenidos.

Comandos:

```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-mode-status
MODEL_SWITCHER_TOKEN=tu_token_seguro COMFY_TTL=45 make prod-comfy-on
MODEL_SWITCHER_TOKEN=tu_token_seguro MODEL=qwen-fast make prod-comfy-off
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-llm-priority
```

`prod-comfy-on` aplica lease con TTL (default 45 min, máximo 90). Al expirar, vuelve automáticamente a `llm` con `qwen-fast` (o `MODEL_SWITCHER_DEFAULT`).

### Reconciliación de arranque (startup)

Al iniciar `model-switcher`, se ejecuta una reconciliación bloqueante del estado persistido:
- Si el modo persistido es `llm`, valida modelo objetivo (`active_model` o fallback a `MODEL_SWITCHER_DEFAULT`) y deja consistente `LiteLLM + un único vllm-*`.
- Si el modo persistido es `comfy`, valida `comfyui` activo y `litellm` detenido.
- Si faltan contenedores creados, el error queda registrado en `last_error` y en `switch.error` con mensaje accionable (`make prod-bootstrap-models`).

## Model switcher (control desde Open WebUI)

Permite cambiar modelo desde un Tool OpenAPI en Open WebUI.
Tambien expone UI minima de administracion en `GET /admin`.

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
- Para evitar timeouts en chat, llamar `POST /switch` con `wait_for_ready=false` y mostrar al usuario `switch_id` + `state_text`.
- Hacer polling de `GET /status` cada 2-5s y leer `switch.state_text` y `switch.ready`.

### 4) Prueba rápida desde host

```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-list-models
MODEL_SWITCHER_TOKEN=tu_token_seguro MODEL=qwen-fast make prod-switch
MODEL_SWITCHER_TOKEN=tu_token_seguro MODEL=qwen-fast make prod-switch-async
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-status
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-mode-status
make prod-admin-url
```

## Endpoints del switcher

- `GET /health`
- `GET /healthz/ready` (solo `ready` cuando el modo activo es `llm`)
- `GET /admin` (UI minima para operaciones de modo)
- `GET /models`
- `GET /status`
- `GET /mode`
- `POST /switch` body: `{"model":"qwen-fast|qwen-quality|deepseek|qwen-max","wait_for_ready":true|false}`
- `POST /mode/switch` body: `{"mode":"llm|comfy","model":"qwen-fast|qwen-quality|deepseek|qwen-max"(solo llm),"ttl_minutes":45(solo comfy),"wait_for_ready":true|false}`
- `POST /mode/release`
- `POST /stop`

`POST /switch`:
- `wait_for_ready=true` (default): respuesta bloqueante hasta éxito/error con estado final.
- `wait_for_ready=false`: respuesta rápida `202` con `status` (`accepted|in_progress`), `switch_id`, `to_model`, `state_text`, `poll_endpoint`.

`GET /status` incluye además un bloque `switch` con:
- `id`, `state` (`queued|running|success|failed|rolled_back`), `from_model`, `to_model`
- `current_step`, `state_text`, `started_at`, `updated_at`, `finished_at`
- `duration_ms`, `error`, `steps`, `ready`
- `active_litellm_model` (id real para llamadas a LiteLLM, ej. `deepseek-r1`)

`GET /mode` devuelve:
- modo activo (`llm|comfy`) y lease de ComfyUI (`expires_at`, `remaining_seconds`, `expired`)
- estado de `litellm`, `comfyui`, `running_models` y switch en curso

## Publicación de ComfyUI (Nginx host)

ComfyUI se expone solo en loopback (`127.0.0.1:8188`). Publica `/comfy/` desde Nginx del host hacia ese puerto y protege con autenticación + rate limit + timeouts altos.

Para canal de control directo, publica tambien `http://127.0.0.1:9000/admin` detras de Nginx con auth (Basic/Auth proxy o VPN).  
La UI `/admin` pide token `MODEL_SWITCHER_TOKEN` y usa:
- `POST /mode/switch` para `llm|comfy`
- `POST /mode/release` para preemption inmediata a LLM

## Runbook de recuperación rápida

Caso: sistema en `comfy` y chat LLM no disponible.

1. Verifica modo:
```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-mode-status
```

2. Inspecciona estado detallado (incluye `active_litellm_model`):
```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-status
```

3. Recupera prioridad LLM (forzado):
```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-llm-priority
```

4. Confirma estado:
```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-mode-status
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-test
```

Caso: `deepseek` activo pero LiteLLM devuelve `Cannot connect to host vllm-deepseek`.

1. Inspecciona estado y logs:
```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-mode-status
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-status
make prod-logs SERVICE=litellm TAIL=200
make prod-logs SERVICE=vllm-deepseek TAIL=200
```

2. Si faltan contenedores (`containers.deepseek.exists=false` o `comfyui`/modelos inexistentes), crea bootstrap:
```bash
make prod-bootstrap-models
```

3. Fuerza prioridad LLM y activa DeepSeek:
```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-llm-priority
MODEL_SWITCHER_TOKEN=tu_token_seguro MODEL=deepseek make prod-switch
```

4. Verifica respuesta con el id real de LiteLLM (`deepseek-r1`):
```bash
curl -sf http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer <LITELLM_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-r1","messages":[{"role":"user","content":"ping"}],"temperature":0}' | jq
```

## Test único de salud

```bash
make prod-test
```

`prod-test` autodetecta el modo activo:
- En `llm`, ejecuta `POST /v1/chat/completions` contra LiteLLM con `active_litellm_model` (o fallback por mapeo `/models`).
- En `comfy`, ejecuta `GET /system_stats` contra ComfyUI.

En ambos casos imprime la llamada que ha usado para verificar.

## Comandos operativos (VPN/SSH)

Los comandos se han movido a `Makefile.ops` y se incluyen automáticamente desde `Makefile`.
Se siguen ejecutando igual:

```bash
make vpn-up
make vpn-down
make vpn-status
make ssh
```
