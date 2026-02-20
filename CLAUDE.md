# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Docker Compose infrastructure for serving Qwen LLMs via an OpenAI-compatible API gateway. Two deployment modes:
- **Local (Mac)**: Ollama as inference backend (no GPU required)
- **Production (server)**: vLLM with NVIDIA GPU (Debian + RTX 6000, 24GB VRAM)

## Architecture

```
Clients (port 4000) → LiteLLM Proxy → Ollama (local, port 11434)
                                     → vllm-fast  (prod, Qwen 7B,     port 8001)
                                     → vllm-quality (prod, Qwen 14B-AWQ, port 8002)
                                     → vllm-deepseek (prod, DeepSeek-R1 32B-AWQ, port 8003)
                                     → vllm-qwen32b (prod, Qwen 32B-AWQ,      port 8004)
Open WebUI (port 3000) → LiteLLM
```

## File Structure

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Base services: LiteLLM + Open WebUI |
| `docker-compose.local.yml` | Local override: adds Ollama, uses `./data/` for volumes |
| `docker-compose.prod.yml` | Prod override: adds vLLM services with GPU, uses `/opt/ai/` paths |
| `litellm-config.qwen-fast.yml` | Model routing for prod qwen-fast profile |
| `litellm-config.qwen-quality.yml` | Model routing for prod qwen-quality profile |
| `litellm-config.deepseek.yml` | Model routing for prod deepseek profile |
| `litellm-config.qwen-max.yml` | Model routing for prod qwen-max profile |
| `litellm-config.local.yml` | Model routing for local (Ollama backend) |
| `Makefile` | `local-*` and `prod-*` targets |

## Commands

### Local (Mac)
```
make local-up       # Start LiteLLM + Ollama
make local-init     # Pull qwen2.5:7b into Ollama (first time only)
make local-web      # Add Open WebUI
make local-down     # Stop everything
make local-ps       # Container status
make local-logs     # Tail logs
```

### Production (GPU server)
```
make prod-qwen-fast    # Start all: infra + vLLM Qwen 7B + Web UI
make prod-qwen-quality # Start all: infra + vLLM Qwen 14B AWQ + Web UI
make prod-deepseek     # Start all: infra + vLLM DeepSeek-R1 32B AWQ + Web UI
make prod-qwen-max     # Start all: infra + vLLM Qwen 32B AWQ + Web UI
make prod-down         # Stop everything
```

### Smoke tests (both environments)
```
make models             # List models via LiteLLM API
make test-qwen-fast     # Chat completion against qwen-fast
make test-qwen-quality  # Chat completion against qwen-quality
make test-deepseek      # Chat completion against deepseek-r1
make test-qwen-max      # Chat completion against qwen-max
```

`KEY=mykey make test-qwen-fast` overrides the default master key.

## Production GPU Config

- **vllm-fast**: Qwen 2.5-7B-AWQ, 55% GPU (≈13GB), 4 concurrent seqs, 4096 tokens max
- **vllm-quality**: Qwen 2.5-14B-AWQ, 85% GPU (≈20GB), 1 concurrent seq, 3072 tokens max
- **vllm-deepseek**: DeepSeek-R1-Distill-Qwen-14B-AWQ, 95% GPU (≈20.9GB), 1 concurrent seq, 2048 tokens max
- **vllm-qwen32b**: Qwen 2.5-32B-AWQ, 95% GPU (≈20.9GB), 1 concurrent seq, 2048 tokens max
- Profiles are mutually exclusive — run one at a time on the 24GB RTX 6000

## Server Directory Layout

| Path | Purpose |
|------|---------|
| `/opt/ai/compose/` | This project |
| `/opt/ai/hf-cache/` | Shared HuggingFace model cache |
| `/opt/ai/litellm-db/` | LiteLLM SQLite database |
| `/opt/ai/openwebui-data/` | Open WebUI persistent data |
