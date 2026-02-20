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
make prod-qwen-fast       # LiteLLM + vLLM Qwen 7B
make prod-qwen-quality    # LiteLLM + vLLM Qwen 14B AWQ
make prod-web        # Añade Open WebUI
make prod-down       # Para todo
```

## Model switcher (control desde Open WebUI)

Permite que el admin de Open WebUI cambie el modelo activo sin SSH, **todo dentro de Docker**.

Nota: el switcher solo puede **start/stop** contenedores ya creados. La primera vez, crea los contenedores de cada perfil con `make prod-qwen-fast`, `make prod-qwen-quality`, etc.

### 1) Configurar token (en el host)

```bash
cd /opt/ai/compose
printf "MODEL_SWITCHER_TOKEN=tu_token_seguro\nMODEL_SWITCHER_DEFAULT=qwen-fast\n" >> .env
```

### 2) Arrancar servicios (incluye model-switcher)

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile qwen-fast up -d
```

### 3) Configurar Open WebUI (admin)

- URL OpenAPI: `http://model-switcher:9000/openapi.json`
- Header: `Authorization: Bearer tu_token_seguro`
- Restringir el Tool a usuarios admin.

### 4) Prueba rápida (desde el host)

```bash
curl -s http://127.0.0.1:9000/status -H "Authorization: Bearer tu_token_seguro"
curl -s http://127.0.0.1:9000/switch -H "Authorization: Bearer tu_token_seguro" -H "Content-Type: application/json" -d '{"model":"qwen-fast"}'
```

Notas de seguridad:
- El switcher **no expone puertos públicos** (solo `127.0.0.1`).
- Usa `docker-socket-proxy` con permisos mínimos (start/stop/inspect) en red interna.

## Smoke tests (ambos entornos)

```bash
make models          # Lista modelos en LiteLLM
make test            # Chat completion contra qwen-fast
```
