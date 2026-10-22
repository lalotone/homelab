# vLLM Qwen3.8-27B, squeezed to share the GPUs with the voice assistant

> **Superseded.** Measured on heavy1, this overlay left only ~2.2 GiB per card, below the voice's ~2.6-3.25 GiB, so
> the voice didn't fit. The GPU voice now runs next to the llama.cpp 27B instead
> ([`k8s/llama.cpp/qwen38-27b`](../../../llama.cpp/qwen38-27b/) + [`k8s/esp32-homeai/with-gpu`](../../../esp32-homeai/with-gpu/)),
> with vLLM scaled to 0. Kept for reference.

An overlay on [`../deploy`](../deploy) that leaves ~3.2 GiB free on each card for one GPU share of
[`k8s/esp32-homeai`](../../../esp32-homeai/) (Rocky's voice, Qwen3-TTS 0.6B in bf16). Everything else about vLLM stays
the same. **It costs most of the context window**, so pick it only while the voice assistant needs the GPU.

```bash
kubectl apply -k .            # voice-friendly vLLM
kubectl apply -k ../deploy    # back to the full-context vLLM (rollback)
```

## What changes

| Flag | `../deploy` | `with-voice` | Why |
|---|---|---|---|
| `--gpu-memory-utilization` | 0.91 | **0.77** | Leaves ~3.2 GiB/card for the voice pod |
| `--max-model-len` | 98304 | **16384** | Only ~0.44 GiB of KV cache is left (~22k tokens, fp8) |
| `--max-num-batched-tokens` | 2048 | **512** | Peak activations ~0.96 → ~0.3 GiB. Long prompts prefill in smaller chunks (slower) |
| `VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS` | (default 1) | **0** | Budget CUDA graphs at the measured 0.17 GiB instead of the 0.77 GiB estimate |

## Memory budget per RTX 5060 Ti

Measured from vLLM's own startup log on heavy1 (2026-10-20, `../deploy` settings):

| GiB per card (15.47 usable) | `../deploy` | `with-voice` |
|---|---|---|
| In use before vLLM starts (driver/CUDA) | 0.34 | 0.34 |
| vLLM weights + runtime (NVFP4, TP=2) | 11.00 | 11.00 |
| vLLM peak activations | 0.96 | ~0.30 |
| vLLM CUDA graphs | 0.17 (0.77 budgeted) | 0.17 |
| vLLM KV cache (fp8) | 2.12 → 107,603 tokens | ~0.44 → ~22,000 tokens |
| **Free for other pods** | **~1.05** | **~3.2** |
| Voice pod (Qwen3-TTS bf16 + CUDA context + work memory) | doesn't fit | ~3.25 (estimate) |

The voice pod's 3.25 GiB is an estimate from its weights (1,085M params, ~2.0-2.3 GiB in bf16) plus CUDA context and
working memory; it hasn't been measured on the GPU yet. **The margin is thin (~0 GiB).** If either pod hits CUDA
OOM, lower `--gpu-memory-utilization` by 0.01-0.02 and `--max-model-len` with it (~50k tokens per GiB of KV).

## Order and checks

1. Enable time-slicing first (`k8s/nvidia-operator/time-slicing`) and make sure `nvidia-smi` works on heavy1
   (reboot after NVIDIA driver upgrades).
2. Apply this overlay and **let vLLM start first**: at startup it checks that `utilization × total` is free, then
   claims it.
3. Check its log for the new budget:

   ```bash
   kubectl -n llm logs deploy/vllm-qwen38 | grep -E "Available KV cache memory|GPU KV cache size"
   # expect roughly: Available KV cache memory: ~0.4 GiB / GPU KV cache size: ~20,000 tokens, concurrency ≥ 1.0x
   kubectl -n llm exec deploy/vllm-qwen38 -- nvidia-smi -L    # two different GPUs (TP=2)
   ```

4. Then deploy the voice pod on a GPU share: `kubectl apply -k ../../../esp32-homeai/with-gpu`, with its
   `LLM_BASE_URL` pointed at vLLM's ClusterIP instead of llama.cpp's.
5. Check real usage on heavy1: `nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv`.

## Alternatives that keep the 98k context

- **Voice on heavy1's CPU** (Ryzen 5 5600G): the default `k8s/esp32-homeai/deploy`. vLLM stays untouched. Answers
  take tens of seconds instead of a few (~48 s measured for a 3-sentence answer).
- **llama.cpp as the brain**: `k8s/llama.cpp/qwen38-27b` leaves ~6-7 GiB/card, enough for the GPU voice. This is the
  current setup.
- **A smaller LLM** for the voice assistant, or a second, small GPU dedicated to it.
