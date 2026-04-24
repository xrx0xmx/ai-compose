# ai-compose (LiteLLM + vLLM + ComfyUI + Open WebUI)

## Estructura

```
docker-compose.yml          # Base: LiteLLM + Open WebUI
docker-compose.local.yml    # Override local: Ollama (Mac, sin GPU)
docker-compose.prod.yml     # Override prod: vLLM + ComfyUI + NVIDIA GPU
.env.example                # Plantilla de variables requeridas en prod
litellm-config.yml          # Config LiteLLM → vLLM (producción)
litellm-config.local.yml    # Config LiteLLM → Ollama (local)
Makefile                    # Atajos local-* y prod-*
Makefile.ops                # Comandos operativos (VPN/SSH)
versions.lock               # Lock de versiones de imagen consumido por Makefile
control/                    # API HTTP para cambiar modelos y modo llm/comfy
control/Dockerfile          # Imagen del model switcher
ops/nginx/                  # Config edge proxy Nginx (80/443)
scripts/prod_test_auto.sh   # Batería automática de validación en producción
compatibility-matrix.md     # Matriz de compatibilidad de modelos/runtime
```

## Regla operativa

Usa siempre `make` para operaciones de Docker en este proyecto.

Las versiones de imagen se gobiernan desde `versions.lock` (incluido por `Makefile`).

## Comandos simplificados (recomendados)

```bash
# Arranque por bloques
make up MODE=all
make up MODE=infra
make up MODE=models

# Parada / reset
make down
make purge CONFIRM=YES SCOPE=project
# make purge CONFIRM=YES SCOPE=host   # destructivo a nivel host

# Logs
make logs TARGET=all TAIL=200
make logs TARGET=litellm TAIL=200
make logs TARGET=vllm-<id-dinamico> TAIL=200

# Control independiente de servicio/contenedor
make start TARGET=admin-panel
make stop TARGET=admin-panel
```

## Versionado de imágenes

En producción, `LITELLM_IMAGE` y `OPENWEBUI_IMAGE` deben ir fijadas por digest (`@sha256`).

Flujo recomendado para refrescar digests en `versions.lock`:

```bash
docker buildx imagetools inspect litellm/litellm:v1.81.15
docker buildx imagetools inspect ghcr.io/open-webui/open-webui:main
# copiar el campo "Digest:" a versions.lock como imagen@sha256:...
```

Después valida el lock antes de desplegar:

```bash
make prod-image-lock-check
make prod-upgrade-precheck
make prod-upgrade-canary
MODEL_SWITCHER_TOKEN=tu_token_seguro LITELLM_KEY=<LITELLM_KEY> make prod-upgrade-verify
```

## Entorno requerido (producción)

Usa `.env.example` como plantilla y define todos los secretos:

```bash
cp .env.example .env
$EDITOR .env
```

Validación de entorno:

```bash
make prod-preflight-env
```

`prod-preflight-env` falla si falta una variable requerida, si hay placeholders inseguros o si la entropía mínima no se cumple.
`MODEL_SWITCHER_ADMIN_TOKEN` queda deprecado y no se usa en flujo operativo.

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

Passthrough GPU para ComfyUI (producción):
- `comfyui` usa `runtime: nvidia` + `gpus: all`
- variables NVIDIA explícitas: `NVIDIA_VISIBLE_DEVICES=all` y `NVIDIA_DRIVER_CAPABILITIES=compute,utility`

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

### Pre-deploy recomendado (week1 gate)

```bash
cp .env .env.backup.$(date +%Y%m%d%H%M%S)
cp versions.lock versions.lock.backup.$(date +%Y%m%d%H%M%S)
make prod-preflight-env
make prod-image-lock-check
```

Si cambias `docker-compose.prod.yml` (imagen/env/runtime GPU), recrea contenedores:

```bash
make prod-down
make prod-init
```

### Upgrade canary (automatizado)

```bash
make prod-upgrade-precheck
make prod-upgrade-canary
MODEL_SWITCHER_TOKEN=tu_token_seguro LITELLM_KEY=<LITELLM_KEY> make prod-upgrade-verify
```

Atajo todo-en-uno:

```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro LITELLM_KEY=<LITELLM_KEY> make prod-upgrade-promote
```

Rollback:

```bash
make prod-upgrade-rollback ROLLBACK_REF=<git-ref-estable>
```

### Preflight GPU antes de activar ComfyUI

```bash
make prod-gpu-preflight
MODEL_SWITCHER_TOKEN=tu_token_seguro COMFY_TTL=45 make prod-comfy-on-safe
```

`prod-comfy-on-safe` ejecuta primero el preflight (host + Docker) y solo si pasa activa modo `comfy`.

### Troubleshooting: `Found no NVIDIA driver on your system`

Si en `make prod-logs SERVICE=comfyui TAIL=120` aparece este error:

```text
RuntimeError: Found no NVIDIA driver on your system
```

Valida en este orden:

1. Host (fuera de Docker):
```bash
lspci | grep -Ei 'nvidia|vga|3d'
nvidia-smi
lsmod | grep nvidia
```

2. Runtime NVIDIA en Docker:
```bash
docker info | grep -E 'Runtimes|Default Runtime'
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi
```

3. Si Docker no ve GPU dentro del contenedor:
```bash
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi
```

4. Reintenta ComfyUI desde el flujo soportado:
```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro COMFY_TTL=45 make prod-comfy-on-safe
make prod-logs SERVICE=comfyui TAIL=120
make prod-test
```

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
MODEL_SWITCHER_TOKEN=tu_token_seguro HF_URL=https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-AWQ make prod-register-model
MODEL_SWITCHER_TOKEN=tu_token_seguro MODEL=<id-dinamico> make prod-unregister-model
MODEL_SWITCHER_TOKEN=tu_token_seguro MODEL=qwen-fast make prod-switch
MODEL_SWITCHER_TOKEN=tu_token_seguro MODEL=qwen-fast make prod-switch-async
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-status
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-mode-status
MODEL_SWITCHER_TOKEN=tu_token_seguro LITELLM_KEY=<LITELLM_KEY> make prod-test-auto
make prod-admin-url
```

## Endpoints del switcher

- `GET /health`
- `GET /healthz/ready` (solo `ready` cuando el modo activo es `llm`)
- `GET /admin` (UI minima para operaciones de modo)
- `GET /models`
- `POST /models/register` body: `{"huggingface_url":"https://huggingface.co/org/repo","model_id":"opcional","litellm_model":"opcional","quantization":"opcional","gpu_memory_utilization":0.9,"max_model_len":4096,"max_num_seqs":1,"trust_remote_code":false,"tokenizer":"opcional","revision":"opcional","dtype":"opcional","vllm_image":"opcional","extra_args":["--enforce-eager"]}`
- `DELETE /models/{model_id}` (solo dinámicos)
- `GET /status`
- `GET /mode`
- `POST /switch` body: `{"model":"<id-en-/models>","wait_for_ready":true|false}`
- `POST /mode/switch` body: `{"mode":"llm|comfy","model":"<id-en-/models>"(solo llm),"ttl_minutes":45(solo comfy),"wait_for_ready":true|false}`
- `POST /mode/release`
- `POST /stop`

`POST /models/register`:
- acepta URL de Hugging Face (`huggingface.co/org/repo` o `org/repo`)
- genera template LiteLLM automáticamente
- registra el modelo en el switcher y crea contenedor vLLM dinámico on-demand
- `trust_remote_code=true` requiere política habilitada y repo en allowlist (`MODEL_SWITCHER_TRUSTED_REPOS`)

Ejemplo con `trust_remote_code`:

```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro \
HF_URL=https://huggingface.co/org/repo \
MODEL_ID=modelo-avanzado \
TRUST_REMOTE_CODE=true \
TOKENIZER=org/tokenizer-base \
REVISION=main \
make prod-register-model
```

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

## Publicación por edge proxy (80/443 only)

El stack productivo expone solo:
- `https://<host>/` -> OpenWebUI
- `https://<host>/admin` -> panel admin
- `https://<host>/comfy/` -> ComfyUI (protegido por sesión admin)

Rutas internas del panel admin (en edge):
- `/admin-api/*` -> proxy a API backend del admin-panel
- `/admin-auth/*` -> login/sesión y chequeo de auth para `comfy`

Puertos backend **no expuestos públicamente**: `3000`, `4000`, `8001-8004`, `8188`.

`model-switcher` queda en loopback (`127.0.0.1:9000`) para runbook operativo.

Pasos:

```bash
make prod-down
make prod-init
```

Validaciones rápidas:

```bash
# valida puertos abiertos/cerrados en host
make prod-ports-audit

# valida rutas del edge proxy
make prod-proxy-check
```

La UI `/admin` autentica con credenciales admin de Open WebUI (cookie `admin_jwt`) y usa:
- `POST /mode/switch` para `llm|comfy`
- `POST /mode/release` para preemption inmediata a LLM
- `GET /status` + `GET /models` para panel unificado `Estado`, `Modelos IA` y `Data`
- Logs integrados en `Estado` (seleccion de contenedor + auto refresh)
- `Data` muestra solo agregados anonimos (`tokens`, `chats`, `usuarios`, `mensajes`)

Para habilitar métricas de tokens en `Data`, define `LITELLM_KEY` (si LiteLLM protege `/metrics`).

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

4. Verifica salud final del plano LLM:
```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro make prod-test
```

## Test único de salud

```bash
make prod-test
```

`prod-test` autodetecta el modo activo:
- En `llm`, valida `GET /healthz/ready` del `model-switcher`.
- En `comfy`, valida `comfyui.status=running` y que no esté `unhealthy` vía `GET /status`.

En ambos casos imprime la llamada que ha usado para verificar.

## Batería automática (canary gate)

```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro LITELLM_KEY=<LITELLM_KEY> make prod-test-auto
```

Modo extensivo (incluye drill de fallo/rollback controlado):

```bash
MODEL_SWITCHER_TOKEN=tu_token_seguro LITELLM_KEY=<LITELLM_KEY> make prod-test-auto-ext
```

Artefactos:
- `artifacts/prod-test-auto-<timestamp>.log`
- `artifacts/prod-test-auto-<timestamp>.json`

## Comandos operativos (VPN/SSH)

Los comandos se han movido a `Makefile.ops` y se incluyen automáticamente desde `Makefile`.
Se siguen ejecutando igual:

```bash
make vpn-up
make vpn-down
make vpn-status
make ssh
```
