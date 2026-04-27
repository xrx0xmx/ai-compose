# Matxa TTS Runbook

## Objetivo

Levantar y validar la integracion de TTS en català basada en Matxa sin cambiar Open WebUI.

## Componentes

- `matxa-backend`: wrapper del upstream `langtech-bsc/minimal-tts-api`
- `matxa-adapter`: proxy OpenAI-compatible para `POST /v1/audio/speech`
- `open-webui`: consumidor final usando `http://matxa-adapter:8002/v1`

## Variables de entorno relevantes

En `.env`:

```bash
MATXA_UPSTREAM_REF=b0084b203100b83ace8dfd2fde09fd18eb875e18
MATXA_PROFILE=matxa-cuda
MATXA_BACKEND_SERVICE=matxa-backend-cuda
MATXA_EXECUTION_PROVIDER=cuda
MATXA_EXECUTION_PROVIDER_CPU=cpu
MATXA_CUDA_VISIBLE_DEVICES=0
MATXA_ORT_CUDA_VISIBLE_DEVICES=0
MATXA_ADAPTER_HOST_PORT=8012
MATXA_DEFAULT_MODEL=tts-1
MATXA_DEFAULT_VOICE=central-grau
MATXA_REQUEST_TIMEOUT_SECONDS=120
MATXA_HEALTH_TIMEOUT_SECONDS=1.0
```

Valores habituales:

- `MATXA_PROFILE=matxa-cuda`: usa `matxa-backend-cuda` con runtime NVIDIA.
- `MATXA_PROFILE=matxa-cpu`: usa `matxa-backend-cpu` sin requerir runtime NVIDIA.
- `MATXA_EXECUTION_PROVIDER=cuda`: falla rapido si el contenedor no ve `CUDAExecutionProvider`.
- `MATXA_EXECUTION_PROVIDER=auto`: usa CUDA cuando exista y CPU si no.
- `MATXA_EXECUTION_PROVIDER_CPU=cpu`: fuerza la variante CPU a mantenerse fuera de CUDA.

## Directorios persistentes

- `/opt/ai/matxa-cache/`

Dentro del contenedor se monta como `/cache` y guarda:

- cache de Hugging Face
- `matxa_multiaccent_wavenext_e2e.onnx`
- `config.yaml`
- `spk_to_id_3.json`

## Despliegue

Usa siempre los objetivos del `Makefile`:

```bash
make up
make ps
make logs TARGET=matxa-backend-cuda TAIL=200
make logs TARGET=matxa-backend-cpu TAIL=200
make logs TARGET=matxa-adapter TAIL=200
```

Si cambias codigo del backend o adapter:

```bash
make deploy
```

## Smoke test host-side

```bash
make test-tts
```

El comando:

- llama a `POST http://127.0.0.1:${MATXA_ADAPTER_HOST_PORT:-8012}/v1/audio/speech`
- usa la voz `central-grau`
- valida que la respuesta sea un WAV legible

Frase de referencia:

```text
La seva gerra sembla molt antiga i el viatge fou molt llarg.
```

## Configuracion en Open WebUI

En el panel de admin:

```text
Settings -> Audio -> Text-to-Speech
```

Usa:

```text
TTS Engine:   OpenAI
API Base URL: http://matxa-adapter:8002/v1
API Key:      matxa-local
TTS Voice:    central-grau
TTS Model:    tts-1
```

## Voces soportadas

- `balear-quim`
- `balear-olga`
- `central-grau`
- `central-elia`
- `nord-occidental-pere`
- `nord-occidental-emma`
- `valencia-lluc`
- `valencia-gina`

## Comandos utiles

```bash
make logs TARGET=matxa-backend-cuda TAIL=200
make logs TARGET=matxa-backend-cpu TAIL=200
make logs TARGET=matxa-adapter TAIL=200
make doctor
```

## Troubleshooting

### El adapter devuelve 502

- revisa `make logs TARGET=matxa-adapter TAIL=200`
- revisa `make logs TARGET=$MATXA_BACKEND_SERVICE TAIL=200`
- comprueba que `matxa-backend` este healthy en `make ps`

### El backend arranca en CPU cuando esperabas CUDA

- confirma `MATXA_PROFILE=matxa-cuda`
- confirma `MATXA_EXECUTION_PROVIDER=cuda` o `auto`
- ejecuta `nvidia-smi` en host
- revisa si otros servicios estan agotando VRAM

### Hay contencion de VRAM con otros workloads

Opciones de menor a mayor impacto:

1. Ajustar `MATXA_EXECUTION_PROVIDER=auto` para tolerar fallback a CPU cuando CUDA no este disponible.
2. Cambiar temporalmente a `MATXA_PROFILE=matxa-cpu` para priorizar LLM o ComfyUI en hosts sin runtime NVIDIA o bajo contencion.
3. Revisar reparto de GPU con los perfiles `vllm-*` si la carga TTS pasa a ser sostenida.

## Licencia y distribucion

- Matxa se usa aqui como dependencia externa con restricciones de licencia segun la documentacion del requerimiento original.
- Antes de redistribuir o usar comercialmente, revisa la licencia efectiva del upstream y valida el caso con BSC.
