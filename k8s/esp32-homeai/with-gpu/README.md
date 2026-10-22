# Voice assistant, TTS on the GPU

An overlay on [`..`](../) that moves Qwen3-TTS off the CPU onto a GPU share, and points the LLM at the
[llama.cpp 27B](../../llama.cpp/qwen38-27b/) instead of vLLM.

```bash
kubectl apply -k .              # GPU voice (llama.cpp brain + TTS on a GPU share)
kubectl apply -k ../deploy      # back to the CPU voice (rollback)
```

## What changes

| Field | `../deploy` (CPU) | `with-gpu` | Why |
|---|---|---|---|
| `runtimeClassName` | (none) | `nvidia` | schedule onto a GPU pod |
| `resources.*.nvidia.com/gpu` | (none) | `1` | one of the 4 time-sliced shares per card |
| image | `:cuda` digest | `:cuda-tools` digest | current `app/` + faster-qwen3-tts (CUDA-graph TTS) + shazamio |
| `TTS_DEVICE` | `cpu` | `cuda` | Qwen3-TTS in bf16 on the GPU |
| `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | `cpu` / `int8` | `cuda` / `float16` | STT 1.5 s → 0.1 s (measured), <1 GB VRAM |
| `LLM_BASE_URL` | vLLM ClusterIP | llama.cpp ClusterIP (`10.105.13.250`, pinned) | the brain is now llama.cpp |

Whisper shares the TTS's single GPU share (same process, same card), so it costs memory but no extra share.

## TTS engine: faster-qwen3-tts

Plain qwen_tts left the GPU idle ~75% of the time (one small kernel at a time, ~16 forward passes per 1/12 s of
audio), at 0.72x realtime. [faster-qwen3-tts](https://github.com/andimarafioti/faster-qwen3-tts) captures each decode
step as one CUDA graph: **2.8x realtime** on the 5060 Ti, ~+0.4 GiB VRAM. Measured on 2026-10-22:

| | plain qwen_tts | faster-qwen3-tts |
|---|---|---|
| STT | 0.1 s | 0.1 s |
| LLM | ~1.3-2 s | ~1.3-2 s |
| `/talk` total | ~8 s for 4.8 s of speech, 14.6 s for 9.5 s | **~4-5.5 s for 7-10 s**, ~7 s for 12.7 s |
| voice pod VRAM | 4.2 GiB | 4.9 GiB (card at 14.4 / 16.3 GB) |

`:cuda-tools` is a thin layer over the `:cuda` digest (`api/Dockerfile.thin` in the esp32-homeai repo): it swaps
qwen-tts for qwen-tts-hf + faster-qwen3-tts 0.5.4, pins transformers 5.15.1 (newer fails with
`MimiConfig ... rope_theta`), adds shazamio, and copies the repo's `app/`. Build and pin steps: [../README.md](../README.md).

Rollback:

```bash
kubectl -n voice rollout undo deploy/esp32-homeai-api   # previous ReplicaSet
```

then restore the previous digest in `kustomization.yaml` so the next apply doesn't bring it back. `TTS_ENGINE=qwen`
switches the same image back to plain qwen_tts.

## Why it only fits next to llama.cpp, not vLLM

Time-slicing shares **compute, not memory** — every pod on a physical card must fit in its 16 GB together. vLLM grabs
~0.91 of each card (leaving ~1-3 GB), so a TTS pod (~3.25 GiB) doesn't fit. llama.cpp only allocates its weights plus
the requested KV cache (~9 GiB/card), leaving ~6-7 GB — enough for TTS. Details and the measured budget live in
[../../llama.cpp/qwen38-27b/README.md](../../llama.cpp/qwen38-27b/README.md).

## Setup

1. Enable time-slicing (`k8s/nvidia-operator/time-slicing`) and confirm `nvidia-smi` works on heavy1.
2. Make llama.cpp the brain and scale vLLM to 0 (see that folder's README).
3. Optional: create the home-location ConfigMap and the Telegram Secret ([../README.md](../README.md)).
4. Apply the overlay: `kubectl apply -k .`. `LLM_BASE_URL` is llama.cpp's ClusterIP, pinned in its `service.yaml`,
   so there's no manual step.
5. Confirm the voice landed on a card with headroom:

   ```bash
   nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv
   kubectl -n voice logs deploy/esp32-homeai-api | grep -iE "tts|cuda|device"
   ```

## Notes

- `runtimeClassName: nvidia` on a CPU-only pod is harmless but pointless — this overlay is the GPU variant.
- If TTS OOMs on the GPU, the llama.cpp side has the cheap knob (`--ctx-size`), since the 27B weights dominate the card.
