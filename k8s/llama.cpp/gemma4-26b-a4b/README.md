# llama.cpp Gemma 4 26B A4B — the voice assistant's brain (since 2026-10-28)

[Gemma 4 26B A4B](https://ai.google.dev/gemma/docs/core) served by llama.cpp `llama-server`, split across both RTX 5060 Ti
on heavy1. It replaced [`../qwen38-27b`](../qwen38-27b/) as Rocky's brain (the ESP32 voice assistant,
[`k8s/esp32-homeai/with-gpu`](../../esp32-homeai/with-gpu/)). Qwen is kept on standby (replicas 0) to roll back.

## Why

Speed. Gemma 4 26B A4B is a *mixture of experts*: 26B weights in memory, but only ~4B of them work for each token. It
takes about the same GPU memory as the dense Qwen 27B and writes about 3x faster. Measured on heavy1, same questions,
same API:

| | Qwen3.8-27B (dense) | Gemma 4 26B A4B (MoE) |
|---|---|---|
| Writing speed | 22 tokens/s | **75 tokens/s** |
| A chat reply | 2-3 s | **0.5-1.5 s** |
| A Live View answer (photo + look) | 4.7-5.2 s | **~3 s** |
| Right tool for 6 action requests (timer, alarm, list, reminder, cancel, send) | 4/6 (2 claimed "set" with no tool) | **6/6** |
| GPU memory (per card, without the voice pod) | ~9.5 GB | ~9.5 GB |

## What had to change for it (esp32-homeai API)

Rocky's prompts were tuned by ear on Qwen. Gemma follows them more literally, so a few were reworded. The new wording
works for both models, so a rollback needs no API change.

- **"Do not talk about Erid... unless the user asks"**: Gemma refused even when asked ("I cannot talk about Erid").
  Now: "don't bring them up on your own; when the user asks, answer gladly".
- **Live View answers**: the hint "say what you need (closer, more light, another angle)" made Gemma answer "Too dark,
  too dark!" about a bright photo. Now: "name what you actually see; if it isn't in the photo, ask them to hold it up".
  And "a word said three times" is "now and then": Gemma did it in every sentence.
- **The watcher's NOTHING**: Gemma sometimes wrote "(No change)" instead, which would have been spoken. The prompt says
  "only the word NOTHING", and the API also treats "(...)" / "No change" as quiet.
- **Math**: about 1 in 8 times Gemma writes the calculator call without quotes, and llama.cpp's Gemma 4 parser turns
  `240*0.17` into the number `0.17240`. The `calculate` tool now refuses a non-string (so the model retries properly),
  and a reply with raw tool-call markup (`<|tool_call>call:...`) is never spoken: the API asks again once, then says
  "say it again".

All of it was checked with the same test questions before and after (chat, read-only tool flows, the tool chosen for
actions, Live View answers, the watcher, math x5).

## Swap and roll back

Only one llama.cpp brain runs at a time: both need both cards. Both deployments label their pods `llm-brain=llama-cpp`,
and the shared Service ([`../shared`](../shared/), still named `llama-qwen38`, pinned ClusterIP `10.105.13.250`)
selects that label, so the voice assistant's `LLM_BASE_URL` is the same whichever brain is up. `llama-server` answers
whatever model name a client sends, so the voice's `LLM_MODEL` doesn't matter either.

**Roll back to Qwen** (as committed in git: flip the two `replicas`, then apply both):

```bash
# in ../qwen38-27b/deployment.yaml: replicas: 1      in ./deployment.yaml: replicas: 0
kubectl apply -k k8s/llama.cpp/gemma4-26b-a4b       # Gemma down first: frees the GPUs
kubectl apply -k k8s/llama.cpp/qwen38-27b           # Qwen up (~2 min to load)
```

Quick, without editing files (git then disagrees until you flip the replicas):

```bash
kubectl -n llm scale deploy/llama-gemma4 --replicas=0
kubectl -n llm scale deploy/llama-qwen38 --replicas=1
```

Check: `kubectl -n llm get endpoints llama-qwen38` shows one pod IP, and Rocky answers.

## Before the first apply: the files on heavy1

The GGUF is read straight from disk (hostPath `/mnt/models`, read-only):

```bash
ssh heavy1 'cd /mnt/models && \
  curl -fL -o gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf \
    https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf && \
  curl -fL -o gemma4-mmproj-F16.gguf \
    https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/mmproj-F16.gguf'   # 17.0 GB + 1.19 GB
```

UD-Q4_K_XL is the same quantization type as the Qwen GGUF, so the comparison above is like for like. Disk: with both
models on heavy1, the root disk had 44 GB free, ~11 GB above where the kubelet starts evicting (~33 GB). Delete the
Qwen files only once Gemma is settled for good.

## Flags (deployment.yaml)

| Flag | Value | Why |
|---|---|---|
| `--tensor-split` | `1.2,1` | ~9.5 GB per card. CUDA1 also holds the vision encoder, so CUDA0 takes more layers. Balanced because the voice pod (~5 GB) can land on either card (time-slicing can't pin it); with it, a card is at ~14.4 of 16.3 GB. `1,1.1` left 8.5 / 10.6 GB. |
| `--main-gpu 1`, `--mmproj-device CUDA1` | | vision work on CUDA1, as for Qwen |
| image budget | default (512 tokens) | llama.cpp caps Gemma's images at `n_ubatch` (512) tokens. Tried `--ubatch-size 2048 --image-min-tokens 1024` (~1000 tokens per image): looks got slower (2.7 → 4.3 s) and no better. Qwen used up to 2048. |
| `--ctx-size 16384`, `--cache-type-k/v q8_0`, `--jinja` | | same as Qwen |
| `--alias gemma-4-26b-a4b` | | the name in `/v1/models`; clients may send any name |

Image: same `ghcr.io/ggml-org/llama.cpp:server-cuda` as Qwen. Gemma 4 (text and vision) works in build 11804 (commit
c7fc509), the one heavy1 had cached.

## Known differences and things to watch

- **Personality**: replies are shorter and a bit plainer than Qwen's, still warm and in Rocky's grammar. If Rocky
  sounds off, compare with Qwen (roll back takes ~2 minutes).
- **Vision**: fewer image tokens (512 vs up to 2048), so tiny text and small parts are harder to read; the zoom tool
  (crops of the full photo) still helps. Plain photo descriptions and written-sum checks were as good as Qwen's.
- **The watcher**: on its first look it tends to stay quiet instead of asking what you're making.
- **Speculative decoding: tried, not worth it** (2026-10-28). unsloth's MTP draft head
  (`mtp-gemma-4-26B-A4B-it.gguf`, 0.46 GB, still in `/mnt/models`) with `--spec-type draft-mtp --model-draft ...`:

  | Setup | Chat replies | Median reply | 100-word photo description |
  |---|---|---|---|
  | **Without (current)** | **75 tokens/s** | **0.78 s** | 1.8 s |
  | MTP, default placement | 17 tokens/s | 2.5 s | 2.2-2.6 s |
  | MTP on the GPU (`--spec-draft-ngl 9999 --spec-draft-device CUDA1`) | 61 tokens/s | 1.03 s | 1.4 s |
  | ...and only confident guesses (`--spec-draft-p-min 0.7`) | 66 tokens/s | 0.95 s | 1.5 s |

  Only 25-40% of the guesses were accepted for Rocky's replies (his broken English is hard to predict), and checking
  guesses on a mixture of experts wakes more experts, so it lost on the short answers that matter (chat, Live View).
  It only helped long plain descriptions (~60% accepted). It also costs ~0.5 GB per card.
