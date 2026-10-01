# llm-d router for Qwen3-8B (Mode B, "ChatGPT at home")

This is an llm-d router in **standalone mode** (Envoy + Endpoint Picker in one pod). It sits in front of the two `vllm-qwen3-8b` replicas on heavy1 and routes each request to the replica most likely to have its prompt in cache, unless that replica is overloaded.

- **Based on:** `llm-d-router` **v0.11.0** (19 Sep 2026), the `llm-d-router-standalone` chart and the llm-d *optimized-baseline* guide.
- **CRDs:** Gateway API Inference Extension **v1.5.0** (the version llm-d-router v0.11.0 pins), with `InferencePool` at `inference.networking.k8s.io/v1`.
- **Delivery:** plain kustomize YAML, written by hand from the chart templates. There is no Helm release.

---

## 1. Architecture

```
                          ┌─────────────── pod qwen3-8b-epp (heavy1, no GPU) ───────────────┐
 client / OpenWebUI       │                                                                  │
 :30820 ──► Service ──────┼─► envoy :8081 ──ext_proc (gRPC, TLS, 127.0.0.1:9002)──► epp     │
 qwen3-8b-router          │        │      ◄── "x-gateway-destination-endpoint: <pod>:8000" ──┘│
                          │        │                                       │ watches pods +   │
                          └────────┼───────────────────────────────────────┼ scrapes /metrics ┘
                                   │ ORIGINAL_DST (direct to pod IP)       │
                     ┌─────────────┴─────────────┐                         │
                     ▼                           ▼                         │
            vllm-qwen3-8b replica 0     vllm-qwen3-8b replica 1  ◄─────────┘
                 (GPU0)                      (GPU1)
                     ▲                           ▲
 :30810 ──► Service vllm-qwen3-8b (kube-proxy, random)  ← baseline, kept for A/B
```

| Piece | What it does |
|---|---|
| **InferencePool `qwen3-8b`** | Groups the pods that serve the same model (selector `llm.model: qwen3-8b`, port 8000) and names the EPP that routes for them. Think of it as "a Service for LLMs". |
| **EPP (Endpoint Picker)** | The routing logic. It tracks the pool's pods, reads vLLM `/metrics`, remembers which prompt prefixes it sent to which pod, and returns the chosen pod for each request. |
| **Envoy sidecar** | The data plane. It receives OpenAI API traffic, asks the EPP through `ext_proc`, then forwards the request directly to the chosen pod IP. |
| **`qwen3-8b-router` Service** | The user entry point: NodePort **30820**, or `qwen3-8b-router.llm:8000` inside the cluster. |
| **`qwen3-8b-epp` Service** | Referenced by the InferencePool. It also exposes EPP metrics (:9090) and Envoy stats (:19001). |

**Standalone vs gateway mode:** gateway mode replaces the Envoy sidecar with a Gateway API implementation (Istio, kgateway, agentgateway, GKE). Cilium is not a supported Inference Extension gateway, so standalone is the right fit for this cluster.

---

## 2. Routing logic (`config/epp-config.yaml`)

This is the official *optimized-baseline* composition ("sticky until saturated"):

| # | Plugin | Role |
|---|---|---|
| 1 | `approx-prefix-cache-producer` | Hashes the prompt into 64-token blocks and records which pod received which blocks, so it can estimate each pod's cache without asking vLLM. `autoTune: true` reads the block size (64) and the cache size from vLLM metrics. |
| 2 | `inflight-load-producer` | Counts the tokens still in flight on each pod. |
| 3 | `prefix-cache-affinity-filter` | If one or more pods hold ≥ `affinityThreshold` (80%) of the prompt, only those pods remain candidates. This stickiness is broken when the sticky pod's estimated TTFT exceeds another pod's by more than `maxTTFTPenaltyMs`. |
| 4 | `token-load-scorer` | Among the remaining candidates, picks the one with the fewest tokens in flight. |

The EPP adds the picker (`max-score`), the parsers (OpenAI, Anthropic, vLLM) and the vLLM metrics data source automatically.

**Effect:** turn N+1 of a conversation returns to the replica that served turn N, so its cache is warm and TTFT is low. New conversations go to the least-loaded replica.

### Parameters

| Parameter | Value | Note |
|---|---|---|
| `affinityThreshold` | 0.80 | Default. |
| `maxTTFTPenaltyMs` | 18000 | Default. With ~55K KV tokens per replica, the stickiness break will rarely trigger on this hardware. This is the knob to lower if you see hot-spotting. |
| `explorationProbability` | 0 | Default. |
| `peakPrefillThroughput` | **15928 → calibrate** | The default was measured with Qwen3-32B on 2×H100 (TP=2). Measure it on the 5060 Ti (section 5). |

**Why Qwen3-8B and not Qwen3.5/3.8:** those are hybrid Gated DeltaNet models, and vLLM gives them KV blocks of 528+ tokens. Prompts shorter than one block get 0% prefix-cache hits (vLLM issue #40696), and llm-d's block-based prefix tracking no longer matches. Qwen3-8B uses standard attention, so `--block-size=64` works on both sides.

---

## 3. Files

```
./
├── README.md                 this document
├── crds/kustomization.yaml   GIE v1.5.0 CRDs (cluster-wide, applied once, kept separate)
├── kustomization.yaml        namespace llm, configMapGenerators, EPP image tag v0.11.0
├── inferencepool.yaml        InferencePool qwen3-8b → pods llm.model=qwen3-8b :8000
├── rbac.yaml                 ServiceAccount + Role (pods, inferencepools, objectives: read only)
├── epp.yaml                  Deployment qwen3-8b-epp (containers envoy + epp)
├── service.yaml              qwen3-8b-epp (ClusterIP) + qwen3-8b-router (NodePort 30820)
├── config/
│   ├── epp-config.yaml       EndpointPickerConfig (routing plugins)
│   └── envoy.yaml            Envoy config (chart preset, sidecar mode, JSON access log)
└── calibrate/
    ├── kustomization.yaml    Job to measure peakPrefillThroughput
    ├── job.yaml
    └── calibrate.py          official llm-d calibration script (unchanged, Apache-2.0)
```

### Differences from the Helm chart

| Chart default | Here | Why |
|---|---|---|
| EPP requests 8 CPU / 8Gi, Envoy 4 CPU / 8Gi | EPP 500m / 512Mi (limit 2Gi), Envoy 200m / 128Mi (limit 512Mi), `--concurrency 2` | A Ryzen 5600G with 12 threads, shared with vLLM. |
| `preStop: sh -c "sleep 5"` | `preStop: sleep: {seconds: 5}` | The EPP image is distroless and has no shell. The native sleep action works on K8s 1.35. |
| Pod annotation `checksum/config` | `configMapGenerator` hash suffix | Changing a config file triggers a rollout automatically. |
| Envoy access log only on `NR` / truncated format | JSON log line per request, including `upstream` | Shows which pod the EPP picked. |
| Metrics endpoint requires a token | `--metrics-endpoint-auth=false` | Lets you curl metrics and scrape them with Prometheus easily in the lab. |
| `failureMode: FailOpen` | `FailClose`, plus Envoy `failure_mode_allow: false` | With `ORIGINAL_DST` there is no fallback destination anyway, so a clear 5xx is better. |
| No scheduling constraints | `nodeSelector: heavy1` + `nvidia.com/gpu` toleration | Everything runs on heavy1 (the Pi is the arm64 control plane), and heavy1 carries the GPU taint. |

---

## 4. Deploy (all commands from the **laptop**, inside `llmd/`)

### Prerequisites

```bash
# Mode B active: 27B off, both qwen3-8b replicas Ready
kubectl -n llm scale deploy/vllm-qwen38 --replicas=0
kubectl apply -k ../model
kubectl -n llm get pods -l llm.model=qwen3-8b -w          # wait for 2/2 Ready
```

### Install

```bash
kubectl apply -k crds/      # once, cluster-wide
kubectl apply -k .
kubectl -n llm get inferencepool,deploy,svc -l app=qwen3-8b-epp
kubectl -n llm get inferencepool qwen3-8b
```

### Verify

```bash
H=<heavy1-tailscale-ip>

curl -s http://$H:30820/v1/models | jq .

curl -s http://$H:30820/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "qwen3-8b",
  "messages": [{"role":"user","content":"Say hi in one word"}],
  "max_tokens": 200
}' | jq .choices[0].message

# Which pod got each request ("upstream")
kubectl -n llm logs deploy/qwen3-8b-epp -c envoy -f

# EPP decisions (raise --v to 3-4 in epp.yaml for per-request scoring detail)
kubectl -n llm logs deploy/qwen3-8b-epp -c epp -f

# Router metrics
kubectl -n llm port-forward svc/qwen3-8b-epp 9090 19001 &
curl -s localhost:9090/metrics | grep -E 'llm_d_epp_|prefix_cache_affinity'
curl -s localhost:19001/stats/prometheus | grep ext_proc
```

**Quick stickiness test:** send the same long conversation (a growing `messages` array) a few times. The `upstream` field should stay on the same pod IP. Different conversations should spread across both pods.

### Uninstall

```bash
kubectl delete -k .      # router only: no vLLM pods, no namespace, no CRD
kubectl delete -k crds/ # only if you really want to remove the CRD (deletes ALL InferencePools)
```

---

## 5. Calibrate `peakPrefillThroughput`

The affinity filter estimates each pod's TTFT as `inFlightTokens / peakPrefillThroughput`. The default value comes from H100s, so measure it on this hardware:

```bash
# run with NO other traffic
kubectl -n llm delete job calibrate-peak-throughput --ignore-not-found
kubectl apply -k calibrate/
kubectl -n llm wait --for=condition=complete --timeout=600s job/calibrate-peak-throughput
kubectl -n llm logs job/calibrate-peak-throughput | grep PEAK_PREFILL_THROUGHPUT
```

- **What the Job sends:** 5 warmup and 20 measured requests of exactly **4096 random token IDs**. That matches `--max-num-batched-tokens`, so each request is one full prefill chunk. Random prompts mean 0% cache hits. The requests go straight to `vllm-qwen3-8b.llm:8000` (no router), so the result measures the GPU, not the routing.
- **Result:** `CHUNK_SIZE / median(TTFT)`, in tokens/s.
- **Apply it:** set the value in `config/epp-config.yaml`, then run `kubectl apply -k .`. The ConfigMap hash changes and the EPP restarts.

---

## 6. Pinned versions

| Component | Version | Source |
|---|---|---|
| EPP image | `ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.11.0` | llm-d-router release workflow (image = `<repo>-endpoint-picker:<tag>`) |
| Envoy | `docker.io/envoyproxy/envoy:distroless-v1.33.2` | chart preset in v0.11.0 |
| GIE CRDs | v1.5.0 `v1-manifests.yaml` (upstream latest: v1.6.2) | `deploy/components/crds-gie` in llm-d-router v0.11.0 |
| EPP config API | `llm-d.ai/v1alpha1` `EndpointPickerConfig` | v0.11.0 |
| vLLM (pods) | `vllm/vllm-openai:v0.30.0` | ../model/ |

**Validated offline:**
- `kustomize build` for the router and calibration folders.
- `kubeconform -strict` against K8s 1.35 schemas.
- The InferencePool against the GIE v1.5.0 CRD schema.
- Plugin names and parameter keys against the v0.11.0 source.

**Not tested before the first apply:**
- The Envoy config has not been loaded by a real Envoy binary.
- The `v0.11.0` image tag on ghcr.io could not be checked from the build environment.

If the EPP crashes on startup, check `kubectl -n llm logs deploy/qwen3-8b-epp -c epp` first; config errors show up there.

---

## 7. Troubleshooting

| Symptom | Likely cause / check |
|---|---|
| `no matches for kind "InferencePool"` | The CRDs are missing: run `kubectl apply -k crds/` first. |
| EPP pod `Pending` | Check the taint toleration and nodeSelector on heavy1 with `kubectl describe pod`. |
| EPP not Ready | The pool has no Ready endpoints: check that the vllm-qwen3-8b pods are Ready and labeled `llm.model=qwen3-8b`. |
| `503` / `NR` flag in the Envoy log | The EPP returned no destination. Check the EPP logs and the pods' readiness. |
| `ext_proc` errors / TLS handshake failures | Envoy ↔ EPP TLS mismatch. The EPP serves TLS by default (`--secure-serving=true`). |
| Everything goes to one pod | Expected for the same conversation. For different conversations, check `maxTTFTPenaltyMs`, the calibrated `peakPrefillThroughput`, and the `llm_d_epp_prefix_cache_affinity_filter_decisions_total` counter. |

---

## 8. Roadmap

1. ✅ vLLM Qwen3-8B ×2 + round-robin Service (:30810)
2. ✅ llm-d router standalone (:30820), this document
3. ⏭ Calibrate `peakPrefillThroughput`
4. ⏭ A/B load test: the same multi-turn chat workload on :30810 vs :30820, comparing TTFT p50/p95 and prefix-cache hit rate per replica
5. ⏭ Prometheus + Grafana (kube-prometheus-stack, PodMonitor for vLLM and the EPP)
6. ⏭ OpenWebUI → `http://qwen3-8b-router.llm:8000/v1`
7. ⏭ Precise prefix routing (vLLM KV events over ZMQ). Research the v0.11.0 way first: `precise-prefix-cache-scorer` was removed in v0.11.0.
8. ⏭ `llm-d-inference-sim` to emulate 8+ replicas without GPUs

---

## References

- llm-d-router: https://github.com/llm-d/llm-d-router
- Chart README: https://github.com/llm-d/llm-d-router/blob/main/config/charts/README.md
- v0.11.0 release: https://github.com/llm-d/llm-d-router/releases/tag/v0.11.0
- Optimized-baseline guide: https://github.com/llm-d/llm-d/tree/main/guides/optimized-baseline
- Router calibration recipe: https://github.com/llm-d/llm-d/tree/main/guides/recipes/router/calibration
- InferencePool API: https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/
