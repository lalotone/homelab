# llama.cpp Qwen3.8-27B — the 27B brain that leaves room for a GPU voice

The 27B model served by [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server`, split across both RTX 5060 Ti
on heavy1 with `--tensor-split`. It's the **Mode A alternative to**
[`k8s/vllm/qwen38-27b-single`](../../vllm/qwen38-27b-single/): same role, same served model name
(`qwen3.8-27b`), but a different engine.

## Why a second engine?

vLLM (even in its
[with-voice](../../vllm/qwen38-27b-single/with-voice/) squeeze) reserves a fixed fraction of each card and only leaves
~3.2 GiB, which is the ceiling Qwen3-TTS can just barely fit. llama.cpp is sized by its inputs instead: it allocates the
weights plus exactly the KV cache you ask for, and stops there. A Q4_K_XL 27B split in two is ~8.2 GiB of weights per
card; with a 16k quantized KV cache that's ~9 GiB in use, leaving **~6-7 GiB free per card** — comfortably enough for
the voice assistant's TTS to run on a GPU share instead of the CPU.

That's the whole point: this deployment exists so
[`k8s/esp32-homeai/with-gpu`](../../esp32-homeai/with-gpu/) can move Qwen3-TTS onto a GPU.

## It's a swap, not a stack

Both engines need both physical cards. Never run both. To switch the brain:

```bash
kubectl -n llm scale deploy/vllm-qwen38 --replicas=0    # down vLLM first
kubectl apply -k .                                      # up llama.cpp
# ... or back:
kubectl -n llm scale deploy/llama-qwen38 --replicas=0
kubectl -n llm scale deploy/vllm-qwen38  --replicas=1
```

When it's the brain, point the voice at it (the `with-gpu` overlay does this): `LLM_BASE_URL` → this service's
pinned ClusterIP `10.105.13.250`, port 8000 (pinned in `service.yaml` because pods on heavy1 can't reach CoreDNS).
NodePort 30801 for outside the cluster.

## Before the first apply

1. **The model on heavy1.** `llama-server` reads the GGUF straight from disk (hostPath), so it must already be at
   `/mnt/models/Qwen3.8-27B-UD-Q4_K_XL.gguf`:

   ```bash
   ssh -t heavy1 'sudo mkdir -p /mnt/models'
   scp ~/path/to/Qwen3.8-27B-UD-Q4_K_XL.gguf heavy1:/mnt/models/
   ```

   The volume is `type: Directory`, so if `/mnt/models` doesn't exist the pod fails to start (intended: fail fast
   rather than serve with no model). The filename is baked into the `-m` flag — rename the file and you must edit
   `deployment.yaml` to match. It's a single-file model, so there's no HF cache or `HF_HUB_OFFLINE` to set up here.
2. **Disk.** The GGUF is ~16-17 GB; with the vLLM-era caches it must still leave the ~33 GB the kubelet evicts below
   on heavy1 (see `k8s/esp32-homeai/README.md`).

## Memory budget per RTX 5060 Ti (15.47 GB usable)

Estimate for Q4_K_XL (~4.5 bits/weight), `--ctx-size 16384`, `--cache-type-k/v q8_0`, `--tensor-split 1,1`:

| GiB per card | Estimate |
|---|---|
| In use before the pod starts (driver/CUDA) | ~0.34 |
| Weights (16-17 GB GGUF ÷ 2, + per-card overhead) | ~8.2-8.5 |
| KV cache (16k, q8_0) | ~0.3-0.7 |
| Compute buffers + CUDA context | ~0.3-0.5 |
| **In use** | **~9.0-9.7** |
| **Free for the voice pod** | **~6-6.5** |

llama.cpp prints nothing like vLLM's KV budget, so verify empirically on heavy1 after it's Ready:

```bash
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv
```

Expect ~9-10 GB used per card, leaving room for the voice's ~3.25 GiB. If the voice OOMs, shrink `--ctx-size`
(16384 → 8192 roughly halves the KV) — the weights dominate, so context is the cheap knob.

## Flags

| Flag | Value | Why |
|---|---|---|
| `-m` | `/models/Qwen3.8-27B-UD-Q4_K_XL.gguf` | hostPath-mounted from `/mnt/models` |
| `--n-gpu-layers 9999` | all | everything on the GPUs, none offloaded |
| `--tensor-split 1,1` | even | equal split across the two cards (needs both to be visible) |
| `--main-gpu 0` | | which card holds the first layers |
| `--ctx-size 16384` | | same working context as vLLM's with-voice |
| `--cache-type-k/v q8_0` | | quantized KV ≈ half the memory of f16 |
| `--jinja` | | use the model's chat template from the GGUF (tools/thinking) |
| `--alias qwen3.8-27b` | | served name matches vLLM's, so clients are unchanged |

The `esp32-homeai` API talks to this over the standard OpenAI `/v1/chat/completions` endpoint, which `llama-server`
implements. Its `LLM_ENABLE_THINKING=false` sends `chat_template_kwargs: {enable_thinking: false}`, which llama-server
passes to the model's chat template (`--jinja`), so thinking really is off. Verified on 2026-10-22: `true` returns
reasoning text in `reasoning_content`, `false` answers directly.

## Order and checks

1. Time-slicing is on (`k8s/nvidia-operator/time-slicing`) and `nvidia-smi` works on heavy1.
2. Scale vLLM 27B to 0 (see above), then `kubectl apply -k .`.
3. Wait for Ready, then confirm it is really on the GPUs and split across both physical cards:

    ```bash
    kubectl -n llm get pod -l app=llama-qwen38                 # 1/1 Ready (readinessProbe hits /health)
    kubectl -n llm exec deploy/llama-qwen38 -- nvidia-smi -L    # two different GPUs
    kubectl -n llm logs deploy/llama-qwen38 | grep -iE "no usable GPU|CUDA devices"
    ```

   "no usable GPU found" means a CPU-only image (the plain `:server` tag): it still serves, at ~1.4 tokens/s.

   (`nvidia-smi` is injected into the pod by the NVIDIA runtime; the image itself may not ship `curl`, so check
   readiness via the probe / `kubectl get pod` rather than `exec curl`.)

4. Deploy the voice on the GPU: `kubectl apply -k ../../esp32-homeai/with-gpu`.
5. `nvidia-smi` on heavy1: the card the voice landed on should still have headroom (llama.cpp uses both cards, so
   whichever the 1/4-share maps to already has ~9 GB of weights).

## Image tag

`ghcr.io/ggml-org/llama.cpp:server-cuda` in `kustomization.yaml`. **Not `:server`**: that one is the CPU-only build and
ignores the GPU flags without failing. `server-cuda` is still a moving tag; after a good start, pin the exact build
(`newTag: server-cuda-b<NNNN>`, listed on the package's container tags page) the same way the vLLM kustomization pins
its version, so an upstream flag rename can't break a live deployment.
