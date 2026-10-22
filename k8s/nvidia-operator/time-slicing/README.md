# GPU time-slicing

Each RTX 5060 Ti on heavy1 is advertised as **4** `nvidia.com/gpu` (8 in total), so small GPU workloads
(e.g. `k8s/esp32-homeai/with-gpu`, 1 share) can run next to the 27B (2 shares, one per card). In practice that's the
llama.cpp 27B (`k8s/llama.cpp/qwen38-27b`): vLLM leaves too little memory per card for the voice.

```bash
./enable_time_slicing.sh     # applies configmap.yaml + helm upgrade (chart v26.7.1, --reuse-values)
```

It points the ClusterPolicy's `devicePlugin.config` at `time-slicing-config` through Helm, so the next
`helm upgrade` keeps it. A `kubectl patch` would be reverted.

## Check after enabling

```bash
kubectl get node heavy1 -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'     # expect 8
kubectl get node heavy1 -L nvidia.com/gpu.replicas,nvidia.com/gpu.product      # replicas=4, product ends in -SHARED
# the 27B must still get two DIFFERENT physical GPUs (after its next restart):
kubectl -n llm exec deploy/llama-qwen38 -- nvidia-smi -L                       # two different GPU UUIDs
# (or deploy/vllm-qwen38 when vLLM is the brain)
```

## Caveats

- **Memory isn't shared out.** Time-slicing interleaves compute only; every pod on a card must fit in its 16 GB together.
  vLLM at `--gpu-memory-utilization=0.91` leaves ~1 GiB per card; llama.cpp leaves ~6-7 GiB. A pod that needs more
  gets CUDA OOM, not a scheduling error.
- **Running pods keep their devices.** The new allocation applies to pods (re)started after the device plugin restarts.
- **Two shares on one card.** The device plugin spreads a multi-share request across physical GPUs, but kubelet
  only treats that as a preference. That's why the `nvidia-smi -L` check above matters for the 27B's two-card split.
- **Driver/library mismatch** (after an apt driver upgrade without a reboot) breaks new GPU pods regardless of
  slicing: reboot first if `nvidia-smi` on heavy1 complains.
