# ai-compose

Stack de servidor con GPU para servir modelos open source con:
- LiteLLM
- vLLM
- ComfyUI
- Open WebUI
- Matxa TTS para català
- panel admin propio
- model switcher para arbitrar una sola GPU entre LLM y ComfyUI

## Estructura

```text
docker-compose.yml          # Base: LiteLLM + Open WebUI
docker-compose.local.yml    # Override local: Ollama (Mac, sin GPU)
docker-compose.prod.yml     # Override prod: vLLM + ComfyUI + NVIDIA GPU
.env.example                # Plantilla de credenciales y defaults
litellm-config.yml          # Config LiteLLM -> vLLM (produccion)
litellm-config.local.yml    # Config LiteLLM -> Ollama (local)
Makefile                    # Fachada minima para operar produccion
Makefile.ops                # VPN / SSH
versions.lock               # Versiones de imagen consumidas por compose
control/                    # API HTTP para cambiar modelos y modo llm/comfy
control/Dockerfile          # Imagen del model switcher
matxa-backend/              # Wrapper reproducible de minimal-tts-api (BSC)
matxa-adapter/              # Adapter OpenAI-compatible para TTS
scripts/ops.sh              # Logica operativa real
compatibility-matrix.md     # Matriz de compatibilidad de modelos/runtime
docs/runbooks/matxa-tts.md  # Configuracion y smoke tests de Matxa
```

## Regla operativa

Usa siempre `make` para operaciones de Docker en este proyecto.

La operacion de produccion queda reducida a estos comandos:

```bash
make up
make down
make deploy
make ps
make logs TARGET=all TAIL=200
make status
make test
make test-tts
make switch MODEL=qwen-fast
make mode MODE=comfy TTL=45
make mode MODE=llm MODEL=qwen-fast
make doctor
make pull
```

## `.env`

`.env` sigue siendo la unica base de credenciales y defaults.

Variables esperadas:

```bash
POSTGRES_PASSWORD=...
LITELLM_KEY=...
MODEL_SWITCHER_TOKEN=...
ADMIN_JWT_SECRET=...
MODEL_SWITCHER_DEFAULT=qwen-fast   # opcional; fallback a qwen-fast
MATXA_PROFILE=matxa-cuda           # opcional; matxa-cuda o matxa-cpu
MATXA_BACKEND_SERVICE=matxa-backend-cuda
MATXA_EXECUTION_PROVIDER=cuda      # opcional; cpu, cuda o auto
```

Para crear uno nuevo:

```bash
cp .env.example .env
$EDITOR .env
```

No hace falta pasar `MODEL_SWITCHER_TOKEN` o `LITELLM_KEY` por CLI para uso normal.
`make` y `scripts/ops.sh` cargan `.env` automaticamente.

## Produccion

Directorios del servidor:
- `/opt/ai/compose/`
- `/opt/ai/hf-cache/`
- `/opt/ai/postgres/`
- `/opt/ai/openwebui-data/`
- `/opt/ai/comfyui-data/`
- `/opt/ai/matxa-cache/`

Publicacion actual directa por puertos:
- `http://<host>/admin`
- `http://<host>:3000`
- `http://<host>:8188` cuando ComfyUI esta activo
- `http://127.0.0.1:${MATXA_ADAPTER_HOST_PORT:-8012}/v1` para smoke tests host-side de Matxa TTS

### Flujo diario

Levantar stack:

```bash
make up
```

Parar stack:

```bash
make down
```

Ver contenedores:

```bash
make ps
```

Ver logs:

```bash
make logs TARGET=all TAIL=200
make logs TARGET=litellm TAIL=200
make logs TARGET=vllm-fast TAIL=200
make logs TARGET=comfyui TAIL=200
make logs TARGET=matxa-adapter TAIL=200
make logs TARGET=matxa-backend-cuda TAIL=200
make logs TARGET=matxa-backend-cpu TAIL=200
```

### Deploy manual

`deploy` no hace `pull` implicito.
Rebuilda `admin-panel`, `model-switcher`, el backend Matxa seleccionado por `MATXA_BACKEND_SERVICE`, y `matxa-adapter`, baja el stack y lo vuelve a levantar.

```bash
make deploy
```

Si quieres actualizar imagenes externas de forma explicita antes:

```bash
make pull
make deploy
```

### Estado y smoke tests

Estado actual del sistema:

```bash
make status
```

Devuelve al menos:
- si el `model-switcher` responde
- modo activo
- modelo activo si esta en LLM
- ultimo error conocido si existe

Smoke test real segun modo activo:

```bash
make test
make test-tts
```

- en `llm`: llama a `POST http://127.0.0.1:4000/v1/chat/completions`
- en `comfy`: llama a `GET http://127.0.0.1:8188/system_stats`
- `make test-tts`: llama a `POST http://127.0.0.1:${MATXA_ADAPTER_HOST_PORT:-8012}/v1/audio/speech` y valida que la respuesta sea un WAV legible

Chequeo opcional completo del sistema vivo:

```bash
make doctor
```

`doctor` ejecuta:
- `docker compose ps`
- `make status`
- `make test`
- `make test-tts`
- comprobacion HTTP de Open WebUI en `:3000`
- comprobacion HTTP de `/admin`
- comprobacion de Comfy solo si el modo activo es `comfy`

## TTS en català

La integracion Matxa se despliega solo en produccion en este repo actual.

- `matxa-backend-cuda` y `matxa-backend-cpu` son variantes explicitas seleccionadas con `MATXA_PROFILE`.
- `matxa-adapter` expone `POST /v1/audio/speech`, `GET /v1/audio/voices`, `GET /v1/models` y `GET /health`.
- Open WebUI debe configurarse con base URL interna `http://matxa-adapter:8002/v1`.

Configuracion recomendada en Open WebUI:

```text
Admin Panel -> Settings -> Audio -> Text-to-Speech

TTS Engine:   OpenAI
API Base URL: http://matxa-adapter:8002/v1
API Key:      matxa-local
TTS Voice:    central-grau
TTS Model:    tts-1
```

La frase de referencia para pruebas es:

```text
La seva gerra sembla molt antiga i el viatge fou molt llarg.
```

Consulta el runbook detallado en `docs/runbooks/matxa-tts.md`.

## Modo y modelos

Cambiar modelo LLM:

```bash
make switch MODEL=qwen-fast
make switch MODEL=deepseek
```

Activar ComfyUI con TTL:

```bash
make mode MODE=comfy TTL=45
```

Volver a LLM:

```bash
make mode MODE=llm MODEL=qwen-fast
```

Si no pasas `MODEL` al volver a LLM, se usa:
- `MODEL_SWITCHER_DEFAULT` si esta definido
- `qwen-fast` si no lo esta

## Modelo mental del sistema

El `model-switcher` gestiona dos modos excluyentes en una sola GPU:
- `llm`: LiteLLM + un unico `vllm-*` activo
- `comfy`: ComfyUI activo, `litellm` y `vllm-*` detenidos

El panel `/admin` y Open WebUI dependen del mismo estado de switch.

## Troubleshooting rapido

### `make status` devuelve token invalido

Comprueba que el valor de `MODEL_SWITCHER_TOKEN` en `.env` coincide con el que usa el contenedor en ejecucion.
Luego recarga shell si hace falta:

```bash
set -a
source ./.env
set +a
```

### ComfyUI no arranca por GPU

Comprueba en host:

```bash
nvidia-smi
```

Comprueba runtime Docker:

```bash
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi
```

### Open WebUI o admin no responden

```bash
make ps
make logs TARGET=open-webui TAIL=200
make logs TARGET=admin-panel TAIL=200
make doctor
```

### Matxa TTS no responde o arranca en CPU

```bash
make logs TARGET=matxa-backend-cuda TAIL=200
make logs TARGET=matxa-backend-cpu TAIL=200
make logs TARGET=matxa-adapter TAIL=200
make test-tts
```

Comprueba tambien:

- `MATXA_PROFILE`, `MATXA_BACKEND_SERVICE` y `MATXA_EXECUTION_PROVIDER` en `.env`
- que `/opt/ai/matxa-cache/` exista y sea escribible
- `nvidia-smi` en host si esperas usar CUDA

## Operaciones host

Siguen disponibles desde `Makefile.ops`:

```bash
make vpn-up
make vpn-down
make vpn-status
make ssh
make scp-home SCP_SRC=. SCP_DEST=~/
```
