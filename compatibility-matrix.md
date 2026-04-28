# Compatibility Matrix

## Goal
Track model/runtime compatibility outcomes for this stack (`LiteLLM + vLLM + model-switcher`).

## Static Baseline

| Model ID | HF Repo | Quantization | Expected Runtime | Status |
|---|---|---|---|---|
| `qwen-fast` | `Qwen/Qwen2.5-7B-Instruct-AWQ` | AWQ | `vllm-fast` | Compatible |
| `qwen-quality` | `Qwen/Qwen2.5-14B-Instruct-AWQ` | AWQ | `vllm-quality` | Compatible |
| `deepseek` | `casperhansen/deepseek-r1-distill-qwen-14b-awq` | AWQ | `vllm-deepseek` | Compatible |
| `deepseek-r1-32b-awq` | `casperhansen/deepseek-r1-distill-qwen-32b-awq` | AWQ | `vllm-deepseek32b` | Planned |
| `qwen-max` | `Qwen/Qwen2.5-32B-Instruct-AWQ` | AWQ | `vllm-qwen32b` | Compatible (VRAM dependent) |

## Dynamic Cases Observed

| HF Repo | Mode | Status | Notes |
|---|---|---|---|
| `unsloth/DeepSeek-R1-Distill-Qwen-7B-GGUF` | dynamic | Not recommended | GGUF with vLLM is experimental and may require additional tokenizer/format handling. |
| `QuantTrio/DeepSeek-V3.2-AWQ` | dynamic | Incompatible (current runtime) | Fails with `model_type=deepseek_v32` not recognized by current `transformers` in runtime image. |

## Policy

1. Prefer AWQ/GPTQ for production.
2. Use dynamic registration with `trust_remote_code=true` only for trusted allowlisted repos.
3. Run `make prod-test-auto` before promoting any new runtime or model profile.
