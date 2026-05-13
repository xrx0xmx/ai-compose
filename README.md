# ai-compose

Stack de IA autoalojado para servir chat, imagen y TTS sobre una unica infraestructura Docker.

El despliegue combina:

- `Open WebUI` como interfaz de usuario.
- `LiteLLM` como gateway OpenAI-compatible.
- `vLLM` para modelos LLM locales.
- `ComfyUI` para generacion de imagen.
- `Matxa TTS` para sintesis en catalan.
- `admin-panel` propio para operacion, metricas y logs.
- `model-switcher` para arbitrar una sola GPU entre LLM y ComfyUI.

Este documento esta pensado como guia de traspaso al cliente final: que hay instalado, como se levanta, donde persisten los datos, que hace cada comando `make` y como se usa el panel admin.

## Resumen ejecutivo

- La operacion diaria se hace con `make`, no con `docker compose` manual.
- El stack de produccion esta pensado para un host Linux con Docker y GPU NVIDIA.
- `Open WebUI` queda publicado en `http://<host>:3000`.
- El panel admin queda publicado en `http://<host>/admin`.
- El workload principal de GPU trabaja en dos modos excluyentes:
  - `llm`: un unico `vllm-*` activo para chat.
  - `comfy`: `ComfyUI` activo y los LLM locales detenidos.
- `Matxa TTS` queda aparte de ese arbitraje principal y se integra en Open WebUI.

## Alcance real del repositorio

Hay dos variantes de despliegue, pero no tienen el mismo peso operativo:

- `docker-compose.prod.yml`: variante principal y soportada para servidor con GPU NVIDIA.
- `docker-compose.local.yml`: variante auxiliar para pruebas locales sin GPU, especialmente TTS/Catotron.

La guia de este `README` se centra en produccion, que es lo que se entrega al cliente final.

## Arquitectura

### Componentes funcionales

| Componente | Funcion | Puerto publicado | Tipo |
|---|---|---:|---|
| `admin-panel` | Panel de operacion, metricas, logs, cambio de modo/modelo | `80 -> 8080` | Imagen construida desde este repo |
| `open-webui` | Interfaz de usuario final | `3000 -> 8080` | Imagen externa |
| `litellm` | Gateway OpenAI-compatible hacia los backends | `4000 -> 4000` | Imagen externa |
| `model-switcher` | API interna para arrancar/parar modelos y alternar LLM/Comfy | `127.0.0.1:9000 -> 9000` | Imagen construida desde este repo |
| `postgres` | Base de datos de LiteLLM | sin publicar | Imagen externa |
| `vllm-fast` | Qwen 2.5 7B AWQ | `8001 -> 8000` | Imagen externa |
| `vllm-quality` | Qwen 2.5 14B AWQ | `8002 -> 8000` | Imagen externa |
| `vllm-deepseek` | DeepSeek-R1 Distill Qwen 14B AWQ | `8003 -> 8000` | Imagen externa |
| `vllm-qwen32b` | Qwen 2.5 32B AWQ | `8004 -> 8000` | Imagen externa |
| `vllm-deepseek32b` | DeepSeek-R1 Distill Qwen 32B AWQ | `8005 -> 8000` | Imagen externa |
| `comfyui` | Generacion de imagen | `8188 -> 8188` | Imagen externa |
| `matxa-backend-cuda` / `matxa-backend-cpu` | Backend TTS catalan | sin publicar | Imagen construida desde este repo |
| `catotron-cpu` | Motor TTS alternativo / soporte local | segun variante | Imagen externa |
| `matxa-adapter` | API OpenAI-compatible para TTS | `127.0.0.1:8012 -> 8002` | Imagen construida desde este repo |
| `docker-socket-proxy` | Proxy restringido al socket Docker | sin publicar | Imagen externa |

### Flujo por producto

#### Chat

1. El usuario entra en `Open WebUI`.
2. `Open WebUI` llama a `LiteLLM`.
3. `LiteLLM` reenvia al backend que el `model-switcher` haya dejado activo en ese momento.
4. El backend suele ser uno de los contenedores `vllm-*`.

#### Imagen

1. El operador activa modo `comfy`.
2. El `model-switcher` detiene `litellm` y los `vllm-*`.
3. Se arranca `ComfyUI`.
4. El usuario trabaja contra `http://<host>:8188`.

#### TTS

1. `Open WebUI` se configura para usar un endpoint OpenAI-compatible de TTS.
2. Ese endpoint es `matxa-adapter`.
3. `matxa-adapter` traduce la peticion al backend real (`matxa-backend` o `catotron` segun modelo).
4. El audio vuelve como WAV.

## Estructura del repositorio

```text
docker-compose.yml              Base comun: Postgres + LiteLLM + Open WebUI
docker-compose.prod.yml         Override principal de produccion
docker-compose.local.yml        Override auxiliar para local sin GPU
Makefile                        Fachada operativa principal
Makefile.ops                    Comandos de host (VPN/SSH/SCP)
.env.example                    Plantilla de variables
versions.lock                   Versiones/pines de imagen
scripts/ops.sh                  Logica real detras de `make`
control/                        Servicio model-switcher
admin/                          Panel admin FastAPI + SPA embebida
matxa-adapter/                  Adaptador OpenAI-compatible para TTS
matxa-backend/                  Wrapper reproducible del backend Matxa
docs/runbooks/matxa-tts.md      Runbook especifico de TTS
compatibility-matrix.md         Matriz de modelos y compatibilidad
```

## Como esta instalado realmente

El repositorio mezcla imagenes externas y servicios construidos localmente.

### Imagenes que se descargan

Las versiones quedan fijadas en [`versions.lock`](/Users/rubenortamagan/Documents/ai-compose-project/versions.lock):

- `postgres:16-alpine`
- `litellm/litellm`
- `ghcr.io/open-webui/open-webui`
- `tecnativa/docker-socket-proxy`
- `vllm/vllm-openai`
- `yanwk/comfyui-boot`
- `nvidia/cuda` para la build de Matxa CUDA

### Imagenes que construye este repo

#### `admin-panel`

- Dockerfile: [admin/Dockerfile](/Users/rubenortamagan/Documents/ai-compose-project/admin/Dockerfile)
- Base: `python:3.12-slim`
- Instala dependencias de [admin/requirements.txt](/Users/rubenortamagan/Documents/ai-compose-project/admin/requirements.txt)
- Ejecuta [admin/app.py](/Users/rubenortamagan/Documents/ai-compose-project/admin/app.py)

#### `model-switcher`

- Dockerfile: [control/Dockerfile](/Users/rubenortamagan/Documents/ai-compose-project/control/Dockerfile)
- Base: `python:3.11-slim`
- Copia las plantillas LiteLLM de cada modelo soportado
- Ejecuta [control/app.py](/Users/rubenortamagan/Documents/ai-compose-project/control/app.py)

#### `matxa-adapter`

- Dockerfile: [matxa-adapter/Dockerfile](/Users/rubenortamagan/Documents/ai-compose-project/matxa-adapter/Dockerfile)
- Base: `python:3.11-slim`
- Expone `/v1/audio/speech`, `/v1/audio/voices`, `/v1/models`, `/health` y `/ready`

#### `matxa-backend-cuda` / `matxa-backend-cpu`

- Dockerfile: [matxa-backend/Dockerfile](/Users/rubenortamagan/Documents/ai-compose-project/matxa-backend/Dockerfile)
- Clona el upstream `langtech-bsc/minimal-tts-api`
- Fija el commit `b0084b203100b83ace8dfd2fde09fd18eb875e18`
- Aplica un parche local para habilitar CUDA en la variante GPU
- Descarga/cachea modelos y artefactos en volumen persistente

## Requisitos del servidor

Para produccion hace falta:

- Linux con Docker Engine y plugin `docker compose`.
- Driver NVIDIA operativo en host.
- NVIDIA Container Toolkit operativo para que Docker pueda arrancar contenedores con GPU.
- Conectividad saliente a Internet en el primer arranque para descargar imagenes y modelos.
- Espacio persistente para caches, base de datos y datos de Open WebUI.

El repo no instala Docker ni el runtime NVIDIA por si mismo. Eso debe estar resuelto en el servidor antes del despliegue.

## Directorios persistentes en produccion

El despliegue asume estos directorios del host:

- `/opt/ai/compose/`
- `/opt/ai/postgres/`
- `/opt/ai/openwebui-data/`
- `/opt/ai/hf-cache/`
- `/opt/ai/comfyui-data/`
- `/opt/ai/matxa-cache/`

Uso de cada uno:

| Ruta host | Contenido |
|---|---|
| `/opt/ai/postgres/` | Datos de Postgres para LiteLLM |
| `/opt/ai/openwebui-data/` | Base SQLite, usuarios, chats y ficheros de Open WebUI |
| `/opt/ai/hf-cache/` | Cache de Hugging Face para modelos LLM y ComfyUI |
| `/opt/ai/comfyui-data/` | Datos persistentes de ComfyUI |
| `/opt/ai/matxa-cache/` | Modelos y cache del backend Matxa |

Punto importante: el panel `/admin` reutiliza la base de datos de Open WebUI. Si se pierde `/opt/ai/openwebui-data/`, se pierden usuarios, chats y tambien la fuente de autenticacion del admin panel.

## Variables de entorno

La configuracion central vive en `.env`.

Crear fichero:

```bash
cp .env.example .env
```

Variables obligatorias:

```bash
POSTGRES_PASSWORD=...
LITELLM_KEY=...
MODEL_SWITCHER_TOKEN=...
ADMIN_JWT_SECRET=...
```

Variables operativas habituales:

```bash
MODEL_SWITCHER_DEFAULT=qwen-fast
MATXA_PROFILE=matxa-cuda
MATXA_BACKEND_SERVICE=matxa-backend-cuda
MATXA_EXECUTION_PROVIDER=cuda
MATXA_ADAPTER_HOST_PORT=8012
MATXA_DEFAULT_MODEL=tts-1
MATXA_DEFAULT_VOICE=central-grau
```

Que hace cada una:

| Variable | Uso |
|---|---|
| `POSTGRES_PASSWORD` | Password de Postgres para LiteLLM |
| `LITELLM_KEY` | API key que usa Open WebUI y los smoke tests contra LiteLLM |
| `MODEL_SWITCHER_TOKEN` | Token que usan `scripts/ops.sh` y el admin panel para gobernar el switcher |
| `ADMIN_JWT_SECRET` | Firma de la sesion del panel admin |
| `MODEL_SWITCHER_DEFAULT` | Modelo por defecto si no se especifica uno al volver a modo `llm` |
| `MATXA_PROFILE` | Selecciona backend TTS `matxa-cuda` o `matxa-cpu` |
| `MATXA_BACKEND_SERVICE` | Servicio TTS concreto a rebuildar y levantar |
| `MATXA_EXECUTION_PROVIDER` | Fuerza `cuda`, `cpu` o `auto` en Matxa |
| `MATXA_ADAPTER_HOST_PORT` | Puerto host del adapter TTS para smoke tests |

`scripts/ops.sh` carga automaticamente `versions.lock` y `.env`. No hace falta exportar estas variables a mano para el uso diario.

## Primer despliegue en produccion

### 1. Preparar el host

Crear directorios persistentes:

```bash
mkdir -p /opt/ai/compose
mkdir -p /opt/ai/postgres
mkdir -p /opt/ai/openwebui-data
mkdir -p /opt/ai/hf-cache
mkdir -p /opt/ai/comfyui-data
mkdir -p /opt/ai/matxa-cache
```

Copiar el repositorio a `/opt/ai/compose` y preparar `.env`.

### 2. Descargar imagenes base

```bash
make pull
```

`make pull` no reconstruye codigo propio; solo descarga o actualiza imagenes externas declaradas en Compose.

### 3. Levantar el stack

```bash
make up
```

Que hace exactamente:

- arranca los servicios base (`postgres`, `litellm`, `model-switcher`, `open-webui`, `admin-panel`, TTS y auxiliares)
- crea los contenedores de `vllm-*` y `comfyui`
- no deja todos los modelos LLM arrancados a la vez

Importante: `make up` prepara los contenedores de modelos, pero el modo activo debe seleccionarse explicitamente cuando haga falta. En un despliegue nuevo, lo normal es fijar el primer modelo justo despues.

### 4. Activar el primer modelo LLM

```bash
make mode MODE=llm MODEL=qwen-fast
```

Alternativas validas:

```bash
make switch MODEL=qwen-fast
make switch MODEL=deepseek-r1-32b-awq
make switch MODEL=deepseek
```

El alias `deepseek` se resuelve a `deepseek-r1-local`.

### 5. Validar el sistema

```bash
make status
make test
make test-tts
make doctor
```

## URLs y publicacion

Publicacion actual del stack:

- `http://<host>/admin`
- `http://<host>:3000`
- `http://<host>:8188` cuando `ComfyUI` esta activo
- `http://127.0.0.1:9000` solo para control interno host-side
- `http://127.0.0.1:${MATXA_ADAPTER_HOST_PORT:-8012}/v1` solo para smoke tests host-side de TTS

El repositorio no incluye proxy inverso HTTPS ni certificados TLS. Si el cliente va a exponer el sistema a Internet, la terminacion TLS debe resolverse fuera de este repo.

## Comandos `make`

Regla de operacion: usa siempre `make` para levantar, parar, reiniciar o inspeccionar Docker en este proyecto.

### Comandos principales

| Comando | Que hace |
|---|---|
| `make up` | Arranca servicios base y crea contenedores de modelos/perfiles |
| `make down` | Baja todo el stack y elimina contenedores del compose |
| `make deploy` | Rebuilda imagenes locales clave, baja el stack y lo vuelve a levantar |
| `make pull` | Descarga/actualiza imagenes externas |
| `make ps` | Muestra estado de todos los contenedores del stack |
| `make logs TARGET=... TAIL=200` | Sigue logs del stack o de un servicio concreto |
| `make status` | Devuelve JSON resumido del estado del switcher |
| `make test` | Smoke test del modo activo (`llm` o `comfy`) |
| `make test-tts` | Smoke test de TTS via `matxa-adapter` |
| `make doctor` | Chequeo completo del sistema vivo |
| `make switch MODEL=<id>` | Cambia el modelo LLM activo |
| `make mode MODE=llm MODEL=<id>` | Fuerza modo LLM con un modelo concreto |
| `make mode MODE=comfy TTL=45` | Activa ComfyUI durante un TTL |

### Que rebuilda `make deploy`

`make deploy` recompila estos servicios:

- `admin-panel`
- `model-switcher`
- `matxa-adapter`
- el backend Matxa seleccionado por `MATXA_BACKEND_SERVICE`

Despues:

1. hace `make down`
2. hace `make up`

No ejecuta `pull` implicito. Si tambien quieres actualizar imagenes externas:

```bash
make pull
make deploy
```

### Como usar `make logs`

Ejemplos utiles:

```bash
make logs TARGET=all TAIL=200
make logs TARGET=litellm TAIL=200
make logs TARGET=vllm-fast TAIL=200
make logs TARGET=vllm-quality TAIL=200
make logs TARGET=vllm-deepseek TAIL=200
make logs TARGET=vllm-deepseek32b TAIL=200
make logs TARGET=vllm-qwen32b TAIL=200
make logs TARGET=comfyui TAIL=200
make logs TARGET=admin-panel TAIL=200
make logs TARGET=model-switcher TAIL=200
make logs TARGET=matxa-adapter TAIL=200
make logs TARGET=matxa-backend-cuda TAIL=200
make logs TARGET=matxa-backend-cpu TAIL=200
```

`TARGET` admite servicios del compose y tambien nombres de contenedor concretos.

### Que valida cada chequeo

#### `make status`

Devuelve, como minimo:

- si el `model-switcher` responde
- modo activo
- modelo activo
- modelo LiteLLM activo
- si hay un switch en curso
- ultimo error conocido

#### `make test`

Comportamiento segun modo:

- en `llm`: llama a `POST http://127.0.0.1:4000/v1/chat/completions`
- en `comfy`: llama a `GET http://127.0.0.1:8188/system_stats`

#### `make test-tts`

- llama a `POST http://127.0.0.1:${MATXA_ADAPTER_HOST_PORT:-8012}/v1/audio/speech`
- usa la voz `central-grau`
- valida que la respuesta sea un WAV legible

#### `make doctor`

Ejecuta:

- `make ps`
- `make status`
- `make test`
- `make test-tts`
- comprobacion HTTP de Open WebUI
- comprobacion HTTP de `/admin`
- comprobacion de ComfyUI si el modo activo es `comfy`

## Modelos soportados

Modelos LLM definidos hoy por el `model-switcher`:

| ID | Etiqueta | Contenedor | Uso esperado |
|---|---|---|---|
| `qwen-fast` | Qwen 2.5 7B | `vllm-fast` | Rapido / uso general |
| `qwen-quality` | Qwen 2.5 14B | `vllm-quality` | Mas calidad, menos concurrencia |
| `deepseek-r1-local` | DeepSeek-R1 14B local | `vllm-deepseek` | Razonamiento |
| `deepseek-r1-32b-awq` | DeepSeek-R1 32B AWQ | `vllm-deepseek32b` | Razonamiento de alta carga |
| `qwen-max` | Qwen 2.5 32B | `vllm-qwen32b` | Maxima calidad local |

Alias aceptado:

- `deepseek` -> `deepseek-r1-local`

Consulta complementaria: [compatibility-matrix.md](/Users/rubenortamagan/Documents/ai-compose-project/compatibility-matrix.md).

## Como funciona el cambio de modo y modelo

### Modo `llm`

Cuando se activa un modelo:

1. se detiene `litellm`
2. se detienen todos los `vllm-*`
3. si `ComfyUI` estaba activo, tambien se detiene
4. se arranca solo el contenedor del modelo elegido
5. se actualiza la configuracion activa de LiteLLM
6. se vuelve a arrancar `litellm`
7. se valida que el modelo aparezca en `/v1/models`

### Modo `comfy`

Cuando se activa `comfy`:

1. se detiene `litellm`
2. se detienen todos los `vllm-*`
3. se arranca `comfyui`
4. se registra un TTL de sesion

### TTL de ComfyUI

Ejemplo:

```bash
make mode MODE=comfy TTL=45
```

Esto deja el sistema en modo imagen durante 45 minutos. Para volver a chat:

```bash
make mode MODE=llm MODEL=qwen-fast
```

o, desde el admin panel, usando la accion de retorno a LLM.

## Panel admin

URL:

```text
http://<host>/admin
```

### Autenticacion

El panel admin no tiene una base de usuarios propia.

- Reutiliza la base SQLite de Open WebUI.
- Solo permite entrar a usuarios con rol `admin`.
- La sesion del panel se firma con `ADMIN_JWT_SECRET`.

En una instalacion nueva, primero hay que crear el administrador en Open WebUI. El panel `/admin` usara esas mismas credenciales.

### Que ofrece el panel

El panel incluye:

- estado general del sistema
- modelo y modo activos
- metricas basicas de Open WebUI y LiteLLM
- logs de contenedores permitidos
- cambio de modelo LLM
- activacion y liberacion de `ComfyUI`
- test de TTS con seleccion de motor y voz

### Secciones practicas

#### Estado

Muestra:

- si el switcher esta listo
- si hay cambio en curso
- ultimo error
- estado de `ComfyUI`
- acceso rapido a logs

#### Modelos

Permite:

- arrancar `qwen-fast`, `qwen-quality`, `deepseek-r1-local`, `deepseek-r1-32b-awq` o `qwen-max`
- activar `ComfyUI`
- volver desde `ComfyUI` a LLM

#### Logs

Permite inspeccionar logs sin entrar por SSH. Internamente lee el Docker API a traves de `docker-socket-proxy`, no del socket Docker crudo.

#### TTS

Permite:

- listar voces disponibles
- listar motores TTS disponibles
- sintetizar una frase de prueba
- descargar o reproducir el WAV resultante

## Open WebUI

URL:

```text
http://<host>:3000
```

Esta es la interfaz para el usuario final.

Configuracion relevante ya cableada por Compose:

- base URL interna de OpenAI-compatible: `http://litellm:4000/v1`
- API key: `LITELLM_KEY`

### Configuracion de TTS en Open WebUI

En el propio Open WebUI:

```text
Admin Panel -> Settings -> Audio -> Text-to-Speech
```

Configurar:

```text
TTS Engine:   OpenAI
API Base URL: http://matxa-adapter:8002/v1
API Key:      matxa-local
TTS Voice:    central-grau
TTS Model:    tts-1
```

## TTS en catalan

### Componentes

- `matxa-backend-cuda`: backend Matxa con runtime NVIDIA
- `matxa-backend-cpu`: fallback CPU
- `matxa-adapter`: capa OpenAI-compatible
- `catotron-cpu`: backend alternativo, usado sobre todo en local

### Modelos TTS expuestos por `matxa-adapter`

Segun configuracion, el adapter puede anunciar:

- `tts-1` -> Matxa
- `tts-catotron` -> Catotron

En produccion, lo habitual es exponer solo `tts-1`.

### Voces soportadas

- `balear-quim`
- `balear-olga`
- `central-grau`
- `central-elia`
- `nord-occidental-pere`
- `nord-occidental-emma`
- `valencia-lluc`
- `valencia-gina`

### Frase de referencia para smoke test

```text
La seva gerra sembla molt antiga i el viatge fou molt llarg.
```

Runbook detallado: [docs/runbooks/matxa-tts.md](/Users/rubenortamagan/Documents/ai-compose-project/docs/runbooks/matxa-tts.md).

## Operacion diaria recomendada

Arranque normal:

```bash
make up
make mode MODE=llm MODEL=qwen-fast
make doctor
```

Cambio a ComfyUI para una sesion temporal:

```bash
make mode MODE=comfy TTL=45
```

Vuelta a chat:

```bash
make mode MODE=llm MODEL=qwen-fast
```

Parada completa:

```bash
make down
```

## Copias de seguridad

Este repo no incluye una politica automatica de backup. Para conservar el sistema hay que respaldar, como minimo:

- `/opt/ai/postgres/`
- `/opt/ai/openwebui-data/`
- `/opt/ai/comfyui-data/` si se quieren preservar activos/workflows
- `/opt/ai/matxa-cache/` si se quiere evitar recacheo de modelos
- opcionalmente `/opt/ai/hf-cache/` para evitar redescargas grandes

Si solo se preserva la configuracion del repo pero no estos directorios, el sistema podra volver a levantarse, pero perdera datos de usuarios, chats y caches.

## Variante local

La variante local existe para pruebas sin GPU y no es la ruta principal de operacion del cliente.
Tampoco tiene el mismo nivel de automatizacion operativa que produccion.

Archivos:

- [docker-compose.local.yml](/Users/rubenortamagan/Documents/ai-compose-project/docker-compose.local.yml)

Casos de uso utiles:

- validacion tecnica local de componentes sin GPU
- pruebas locales de TTS

Targets disponibles:

```bash
make local-tts-up
make local-tts-down
```

Estos targets levantan o paran `catotron-cpu` y `matxa-adapter` para validar TTS en local.
El resto de la operacion diaria debe considerarse documentada y soportada en la variante de produccion.

## Troubleshooting rapido

### `make status` falla por token

Revisa que `MODEL_SWITCHER_TOKEN` en `.env` coincide con el valor cargado por el contenedor `model-switcher`.

### Open WebUI o admin no responden

```bash
make ps
make logs TARGET=open-webui TAIL=200
make logs TARGET=admin-panel TAIL=200
make doctor
```

### Un modelo no arranca

```bash
make logs TARGET=model-switcher TAIL=200
make logs TARGET=vllm-fast TAIL=200
make logs TARGET=vllm-quality TAIL=200
make logs TARGET=vllm-deepseek TAIL=200
make logs TARGET=vllm-deepseek32b TAIL=200
make logs TARGET=vllm-qwen32b TAIL=200
```

### ComfyUI no arranca por GPU

Comprobar en host:

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi
```

### Matxa TTS no responde

```bash
make logs TARGET=matxa-adapter TAIL=200
make logs TARGET=matxa-backend-cuda TAIL=200
make logs TARGET=matxa-backend-cpu TAIL=200
make test-tts
```

Revisar tambien:

- `MATXA_PROFILE`
- `MATXA_BACKEND_SERVICE`
- `MATXA_EXECUTION_PROVIDER`
- permisos sobre `/opt/ai/matxa-cache/`

## Operaciones host

Siguen expuestas via [`Makefile.ops`](/Users/rubenortamagan/Documents/ai-compose-project/Makefile.ops):

```bash
make vpn-up
make vpn-down
make vpn-status
make ssh
make scp-home SCP_SRC=. SCP_DEST=~/
```

## Ficheros de referencia

- [Makefile](/Users/rubenortamagan/Documents/ai-compose-project/Makefile)
- [scripts/ops.sh](/Users/rubenortamagan/Documents/ai-compose-project/scripts/ops.sh)
- [docker-compose.yml](/Users/rubenortamagan/Documents/ai-compose-project/docker-compose.yml)
- [docker-compose.prod.yml](/Users/rubenortamagan/Documents/ai-compose-project/docker-compose.prod.yml)
- [docker-compose.local.yml](/Users/rubenortamagan/Documents/ai-compose-project/docker-compose.local.yml)
- [docs/runbooks/matxa-tts.md](/Users/rubenortamagan/Documents/ai-compose-project/docs/runbooks/matxa-tts.md)
- [compatibility-matrix.md](/Users/rubenortamagan/Documents/ai-compose-project/compatibility-matrix.md)
