---
title: feat: Integrar DeepSeek-V4 configurable desde admin
type: feat
status: active
date: 2026-04-28
deepened: 2026-04-28
---

# feat: Integrar DeepSeek-V4 configurable desde admin

## Resumen

Este plan incorpora `DeepSeek-V4 Preview` en el stack actual para que sea seleccionable desde el panel admin, manteniendo LiteLLM como punto único de acceso para Open WebUI y separando claramente los modelos locales vLLM de los modelos remotos por API.

## Objetivo

Permitir activar desde admin los modelos remotos:

- `deepseek-v4-flash`
- `deepseek-v4-pro`

sin romper el flujo actual de cambio de modo/modelo y evitando confusión con el modelo local existente (`DeepSeek-R1-Distill-Qwen-14B-AWQ`).

## Problema actual

- El switcher (`control/app.py`) está orientado a modelos locales con contenedor propio.
- El admin mantiene catálogo duplicado y parcialmente hardcodeado.
- El contrato admin ↔ control no está completamente alineado (`/mode` vs `/models` y `/switch`).
- El perfil DeepSeek actual es local (R1 distill), no V4 oficial.

## Requisitos

- R1. El admin debe listar y activar `deepseek-v4-flash` y `deepseek-v4-pro`.
- R2. El control-plane debe soportar modelos de tipo remoto (OpenAI-compatible) además de locales vLLM.
- R3. LiteLLM debe arrancar con configuración coherente para V4 y validar conectividad básica.
- R4. El sistema debe fallar con error explícito si falta `DEEPSEEK_API_KEY` al activar V4.
- R5. Debe mantenerse compatibilidad con modos/modelos locales existentes.
- R6. Debe existir una única fuente de verdad del catálogo mostrado en admin.

## Decisiones técnicas

1. **Integración V4 por API oficial (no self-host V4).**
   El stack actual está optimizado para vLLM local en GPU limitada; V4 se integra de forma remota vía LiteLLM.

2. **Tipado explícito de backend de modelo.**
   Añadir `kind` con valores `local_vllm` y `remote_openai_compatible` para gobernar el flujo de activación.

3. **Catálogo centralizado en control-plane.**
   `GET /models` devuelve metadatos completos para renderizado/admin sin duplicar IDs.

4. **Secrets fuera del admin (fase 1).**
   `DEEPSEEK_API_KEY` y `DEEPSEEK_API_BASE` se gestionan por `.env` + compose.

## Alcance

### Incluye

- Actualización del contrato de endpoints entre admin y control.
- Soporte de modelos remotos DeepSeek V4 en catálogo y activación.
- Ajustes de configuración LiteLLM para `deepseek-v4-flash` y `deepseek-v4-pro`.
- Validaciones y mensajes de error operativos.
- Tests unitarios y smoke tests de flujo de cambio.

### No incluye (fase 1)

- Gestión de claves API desde la UI de admin.
- Parámetros avanzados V4 (p. ej. perfiles de reasoning/effort).
- Orquestación multi-proveedor compleja o balanceo por coste/latencia.

## Unidades de implementación

- [ ] **Unidad 1: Alinear contrato admin ↔ control**

  **Meta:** Normalizar endpoints y payloads para estado/cambio de modelo.

  **Archivos:**
  - `control/app.py`
  - `admin/app.py`
  - `scripts/ops.sh` (si aplica a comandos de conmutación)

  **Cambios esperados:**
  - Mantener endpoints canónicos (`/models`, `/status`, `/switch`) y añadir compatibilidad retro si procede.
  - Estandarizar respuesta con campos: `active_model`, `available_models`, `kind`, `provider`, `requires_api_key`.

- [ ] **Unidad 2: Extender registro de modelos con tipo remoto**

  **Meta:** Soportar activación diferenciada por tipo de backend.

  **Archivos:**
  - `control/app.py`

  **Cambios esperados:**
  - Añadir entradas para `deepseek-v4-flash` y `deepseek-v4-pro`.
  - Introducir flujo de activación para `remote_openai_compatible` sin arrancar contenedor `vllm-*`.
  - Mantener flujo actual para `local_vllm`.

- [ ] **Unidad 3: Configuración LiteLLM para DeepSeek V4**

  **Meta:** Garantizar enrutado correcto y verificación básica al activar V4.

  **Archivos:**
  - `litellm-config*.yml` (según estrategia del repo)
  - `docker-compose.prod.yml`
  - `.env.example`

  **Cambios esperados:**
  - Mapear modelos `deepseek-v4-flash` y `deepseek-v4-pro` con proveedor DeepSeek/OpenAI-compatible.
  - Inyectar `DEEPSEEK_API_KEY` y `DEEPSEEK_API_BASE`.
  - Añadir validación previa para evitar activación “silenciosa” sin credenciales.

- [ ] **Unidad 4: UI admin configurable sin hardcodes de catálogo**

  **Meta:** Que el panel renderice desde el catálogo del control-plane.

  **Archivos:**
  - `admin/app.py`

  **Cambios esperados:**
  - Consumir metadatos dinámicos (`id`, `label`, `kind`, `provider`, `dynamic`, `requires_api_key`).
  - Mostrar badges `local`/`remote` y estado de requisito de API key.
  - Renombrar el DeepSeek local para evitar ambigüedad (p. ej. `deepseek-r1-local`).

- [ ] **Unidad 5: Tests y smoke tests operativos**

  **Meta:** Cubrir regresión y flujo extremo a extremo mínimo.

  **Archivos:**
  - `tests/...` (control/admin/ops)

  **Casos mínimos:**
  - Activar modelo local sigue funcionando.
  - Activar `deepseek-v4-flash` y `deepseek-v4-pro` no intenta levantar `vllm-*`.
  - Error claro si falta `DEEPSEEK_API_KEY`.
  - Verificación de disponibilidad en `/v1/models` tras switch.

## Criterios de aceptación

- Desde admin se pueden seleccionar `deepseek-v4-flash` y `deepseek-v4-pro`.
- El switch se completa sin dependencia de contenedor de inferencia local para V4.
- LiteLLM expone el modelo activo esperado y responde a una prueba simple de chat/completion.
- El DeepSeek local existente permanece operativo y claramente identificado.

## Riesgos y mitigaciones

- **Riesgo:** divergencia entre contrato frontend-backend.
  - **Mitigación:** contrato único en `/models` + tests de contrato.

- **Riesgo:** activaciones fallidas por secretos no configurados.
  - **Mitigación:** validación previa + mensaje de error accionable.

- **Riesgo:** confusión operativa entre DeepSeek local y DeepSeek V4 remoto.
  - **Mitigación:** naming explícito y badges visuales en admin.

## Plan de despliegue

1. Deploy backend control + admin con soporte de catálogo tipado.
2. Cargar secretos `DEEPSEEK_API_KEY`/`DEEPSEEK_API_BASE` en entorno.
3. Reiniciar stack con objetivos de `Makefile`.
4. Ejecutar smoke test de switch a `deepseek-v4-flash`.
5. Habilitar `deepseek-v4-pro` como opción adicional en producción.

## Rollback

- Volver a catálogo anterior deshabilitando entradas `deepseek-v4-*`.
- Mantener disponibles perfiles locales vLLM existentes.
- Revertir configuración LiteLLM a perfiles previos si hay degradación.
