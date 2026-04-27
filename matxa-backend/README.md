# matxa-backend

Wrapper reproducible para `langtech-bsc/minimal-tts-api` fijado al commit `b0084b203100b83ace8dfd2fde09fd18eb875e18`.

## Objetivo

- Mantener el contrato HTTP upstream (`/api/tts`, `/health`, `/v1/models`, `/v1/audio/speech`).
- Permitir dos variantes de runtime:
  - `MATXA_RUNTIME=cpu`
  - `MATXA_RUNTIME=cuda` (recomendada)
- Cachear modelos y descargas en un volumen persistente para evitar descargas en cada restart.

## Variables relevantes

- `MATXA_RUNTIME`: `cpu` o `cuda` en build time.
- `MATXA_EXECUTION_PROVIDER`: `auto`, `cpu` o `cuda`.
- `CUDA_VISIBLE_DEVICES`: GPU visible para ONNX Runtime.
- `ORT_CUDA_VISIBLE_DEVICES`: override opcional para pruebas de contención.
- `MATXA_MODEL_DIR`: directorio persistente de modelos dentro del contenedor.

## Notas

- El parche local solo toca la selección del execution provider; no cambia el contrato HTTP upstream.
- El entrypoint descarga los artefactos si faltan y luego enlaza los ficheros esperados por `app.py`.
