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
deploy/                     # Systemd + env del model switcher
scripts/switch-model.sh     # Script de switch (host)
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

## Model switcher (control desde Open WebUI)

Permite que el admin de Open WebUI cambie el modelo activo sin SSH.

### 1) Preparar usuario y permisos (sudoers)

```bash
sudo useradd -m -s /bin/bash aiswitch
sudo usermod -aG docker aiswitch
echo 'aiswitch ALL=(root) NOPASSWD: /opt/ai/compose/scripts/switch-model.sh *' | sudo tee /etc/sudoers.d/ai-model-switcher
```

### 2) Dependencias Python

```bash
python3 -m pip install -r /opt/ai/compose/control/requirements.txt
```

### 3) Config env del servicio

```bash
cp /opt/ai/compose/deploy/model-switcher.env.example /opt/ai/compose/deploy/model-switcher.env
sudo sed -i 's/change_me/tu_token_seguro/' /opt/ai/compose/deploy/model-switcher.env
```

### 4) Instalar systemd

```bash
sudo cp /opt/ai/compose/deploy/model-switcher.service /etc/systemd/system/model-switcher.service
sudo systemctl daemon-reload
sudo systemctl enable --now model-switcher
sudo systemctl status model-switcher
```

### 5) Configurar Open WebUI (admin)

- URL OpenAPI: `http://host.docker.internal:9000/openapi.json`
- Header: `Authorization: Bearer tu_token_seguro`
- Restringir el Tool a usuarios admin.

Nota: el servicio escucha en `0.0.0.0` para que el contenedor de Open WebUI pueda acceder. Si quieres limitar acceso, filtra el puerto 9000 con firewall y deja el token.

### 6) Prueba rapida

```bash
curl -s http://127.0.0.1:9000/status -H "Authorization: Bearer tu_token_seguro"
curl -s http://127.0.0.1:9000/switch -H "Authorization: Bearer tu_token_seguro" -H "Content-Type: application/json" -d '{"model":"qwen-fast"}'
```

## Smoke tests (ambos entornos)

```bash
make models          # Lista modelos en LiteLLM
make test            # Chat completion contra qwen-fast
```
