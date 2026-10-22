# esp32-homeai

The API behind the ESP32 smart speaker ([esp32-homeai](https://github.com/lalotone/esp32-homeai)): the board POSTs a
WAV to `/talk`, and the pod runs Whisper STT → the in-cluster 27B LLM with tools → Rocky's voice (Qwen3-TTS 0.6B), then
answers with a 16 kHz WAV. While idle the board polls `/pending` for alarms, timers and reminders.

```
ESP32 (home WiFi) ──► heavy1:30880 (NodePort) ──► esp32-homeai-api (ns voice, GPU share: Whisper + TTS)
                                                        ├──► llama-qwen38.llm:8000 (in-cluster 27B)
                                                        └──► internet: weather, news, Wikipedia, Shazam, Telegram
```

Two ways to run it:

| | Brain | Voice | Answer time |
|---|---|---|---|
| [`with-gpu/`](with-gpu/) (**the intended setup**) | llama.cpp 27B, vLLM scaled to 0 | Whisper + Qwen3-TTS on a time-sliced GPU share | ~4-6 s |
| `deploy/` (CPU fallback) | vLLM 27B | on heavy1's CPU | ~48 s for 3 sentences |

## Before the first apply

1. **Pick the mode.** The GPU voice only fits next to llama.cpp, not vLLM (vLLM at its full config leaves ~0.6 GiB per
   card, and even the [`with-voice`](../vllm/qwen38-27b-single/with-voice/) overlay left only ~2.2 GiB, below
   Qwen3-TTS's ~2.6-3.25 GiB). For `with-gpu`: enable [time-slicing](../nvidia-operator/time-slicing/), deploy
   [llama.cpp](../llama.cpp/qwen38-27b/) and scale vLLM to 0.
2. **Host folders**, owned by the image's uid 1000:

   ```bash
   ssh -t heavy1 'sudo mkdir -p /mnt/esp32-homeai/{rocky,hf,state} && sudo chown -R 1000:1000 /mnt/esp32-homeai'
   scp ~/src/repos/esp32-homeai/api/rocky/rocky_ref.{wav,txt} heavy1:/mnt/esp32-homeai/rocky/
   ```

   The voice reference is private (cut from film audio) and lives only on heavy1's disk, never in an image or this repo.
3. **Models, pre-copied** (the pod runs with `HF_HUB_OFFLINE=1`, like vLLM), from any machine that has them cached:

   ```bash
   ssh heavy1 mkdir -p /mnt/esp32-homeai/hf/hub
   rsync -a ~/.cache/huggingface/hub/models--Systran--faster-whisper-small \
            ~/.cache/huggingface/hub/models--Qwen--Qwen3-TTS-12Hz-0.6B-Base heavy1:/mnt/esp32-homeai/hf/hub/
   ```

4. **Images** (public on Docker Hub; they hold only code: no voice reference, no `.env`, no tokens, no model weights):
   - `deploy/` pins `fooser/esp32-homeai-api:cuda` (the full image; CUDA torch runs on the CPU too).
   - `with-gpu/` pins `:cuda-tools`, built from the esp32-homeai repo with `api/Dockerfile.thin`: its `app/` plus the
     newer deps as one ~75 MB layer on top of `:cuda`, so heavy1 never pulls another ~12 GB. To update:

     ```bash
     cd ~/src/repos/esp32-homeai/api
     docker build -f Dockerfile.thin -t fooser/esp32-homeai-api:cuda-tools . && docker push fooser/esp32-homeai-api:cuda-tools
     # then put the pushed digest in with-gpu/kustomization.yaml (the line marked "# :cuda-tools") and apply
     ```
5. **Optional, made by hand and never committed**: your real home location ([below](#home-location)) and the
   Telegram bot ([below](#telegram-send-it-to-my-phone)). Without them Rocky uses the Madrid example and the phone
   tools say "not set up".
6. **Disk**: the kubelet evicts pods below ~33 GB free on heavy1 (15% of `/`). The image (~13 GB unpacked) plus models
   (~3 GB) must leave that much. Running short evicted vLLM too on 2026-10-20.

## Deploy

```bash
kubectl kustomize with-gpu          # review (or deploy/ for the CPU fallback)
kubectl apply -k with-gpu
kubectl -n voice logs -f deploy/esp32-homeai-api
curl http://<heavy1 LAN IP>:30880/health
```

Rollback after a bad image: `kubectl -n voice rollout undo deploy/esp32-homeai-api`, then restore the previous digest
in `with-gpu/kustomization.yaml` so the next apply doesn't bring it back.

Then set the board's assistant URL to `http://<heavy1 LAN IP>:30880/talk` in its config portal.

## Known workaround: pod networking on heavy1

Since the 2026-10-20 reboot, pods on heavy1 can't reach k8s-master: no CoreDNS (it runs there), and no internet.
Hence `HF_HUB_OFFLINE=1` and `LLM_BASE_URL` set to vLLM's **ClusterIP** (`10.100.199.252`, local to heavy1) instead of
`vllm-qwen38.llm.svc.cluster.local`. Once cross-node pod traffic works again, switch back to the DNS name: a ClusterIP
changes if the Service is recreated.

The same breakage also takes out DNS (CoreDNS lives on k8s-master), while direct egress from the pod works. The LLM
tools need names resolved, so the Deployment uses `dnsPolicy: None` with public resolvers (1.1.1.1, 8.8.8.8). Drop that
too once cross-node traffic is fixed.

## LLM tools

The API gives the LLM function-calling tools (`~/src/repos/esp32-homeai/api/app/tools.py`, free keyless sources):

| Tool | Source |
|---|---|
| `get_time` | local clock, `HOME_TZ` |
| `get_weather` (place, days ahead) | Open-Meteo forecast + geocoding; default place `HOME_NAME`/`HOME_LAT`/`HOME_LON` |
| `get_news` (world / spain / local) | RSS: BBC World; El País + 20minutos; `LOCAL_NEWS_FEEDS` |
| `calculate` | safe arithmetic evaluator (no `eval`) |
| `lookup_wikipedia` | Wikipedia (en/es) summaries |
| `get_homelab_status` | Kubernetes API (read-only ServiceAccount, `deploy/rbac.yaml`: get/list nodes, pods, deployments), DCGM exporter (`HOMELAB_DCGM_URL`), heavy1's disk |
| `set_timer` / `set_reminder` / `list_alerts` / `cancel_alert` | kept in `/mnt/esp32-homeai/state/alerts.json`; the speaker polls `GET /pending` every 3 s and plays Rocky's announcement (green LED) |
| `set_alarm` (once / daily / weekdays / weekends) | same file; rings with the morning briefing, composed ~2 min ahead so `/pending` answers instantly |
| shopping list (add / remove / read / clear / undo) | `/mnt/esp32-homeai/state/shopping.json` |
| `get_morning_briefing` | "good morning": weather, today's reminders, 2 world news, shopping list size |
| `identify_song` | Shazam via shazamio (unofficial, no key); the follow-up recording is the music sample; matches also go to Telegram |
| `send_to_phone` / `send_shopping_list` | Telegram bot, only to `TELEGRAM_CHAT_ID` |
| photo inbox + `look_at_last_photo` | photos you send the bot, answered by llama.cpp's vision (`TELEGRAM_PHOTOS=true`, guardrails below) |

A question that needs a tool costs one extra LLM round (~2-4 s). The last 4 exchanges (tool calls included, or the model starts claiming "timer set" without calling the tool) are kept for 5 minutes, so follow-ups work (`HISTORY_TURNS`, `HISTORY_TTL_S`). `TOOLS_ENABLED=false` turns them off. `WHISPER_PROMPT`
lists local names so the English Whisper doesn't mishear them ("Zaragoza" as "Maragona"). An action request that ends
without a tool call is nudged once and never reported as done (`app/llm.py`): the model otherwise often says "alarm
set" without setting anything. `SILENCE_THRESHOLD_DBFS=-55`: real speech through the board averages -45..-48 dBFS over
the whole clip, so the old -40 threw questions away.

## Home location

The committed values are an example (Madrid, in `deploy/kustomization.yaml`). The real home lives only in the
cluster, in a ConfigMap that overrides any of those keys:

```bash
kubectl -n voice create configmap esp32-homeai-home \
  --from-literal=HOME_NAME='<city>' --from-literal=HOME_LAT='<lat>' --from-literal=HOME_LON='<lon>' \
  --from-literal=LOCAL_NEWS_NAME='<city> (in Spanish)' --from-literal=LOCAL_NEWS_FEEDS='<rss>,<rss>' \
  --from-literal=WHISPER_PROMPT='Rocky. <local place names>.'
kubectl -n voice rollout restart deploy/esp32-homeai-api   # env is read at start
```

## Telegram ("send it to my phone")

Rocky can send the shopping list, any text, and reminders ("remind me on my phone at 6") to a Telegram bot chat.
It only ever writes to `TELEGRAM_CHAT_ID`; phone reminders go out from a server loop, so they arrive even when the
speaker is off. Both values come from a Secret that is **not** in this repo:

```bash
# token from @BotFather; run this in your own terminal so the token never lands in a log or a chat
kubectl -n voice create secret generic telegram-bot --from-literal=token='<BOT_TOKEN>'
# after sending the bot any message, find your chat id and add it:
kubectl -n voice patch secret telegram-bot -p '{"stringData":{"chat_id":"<CHAT_ID>"}}'
kubectl -n voice rollout restart deploy/esp32-homeai-api
```

Without the Secret the API runs normally and the phone tools answer "not set up".

**Photos (`TELEGRAM_PHOTOS=true` here):** you can send the bot a photo and Rocky answers in the chat, using llama.cpp's
vision ([`--mmproj`](../llama.cpp/qwen38-27b/README.md#vision)); the speaker can be asked about it for 10 minutes.
It's the only inbound path to the AI, so: only your private chat (chat and sender = `TELEGRAM_CHAT_ID`), photos only,
EXIF/GPS stripped and kept in memory only, answered with **no tools** (text in a photo can't trigger actions), 10 per
10 minutes, outgoing polling only. Full list: esp32-homeai's `api/README.md`, "Photos via Telegram".

## Design notes

- **LLM in-cluster**: `LLM_BASE_URL` is a **ClusterIP** (llama.cpp's pinned `10.105.13.250` in `with-gpu`, vLLM's in
  `deploy`) — not a DNS name, because pods on heavy1 can't reach CoreDNS (see the workaround above) — so there's no
  NodePort or Tailscale hop.
  `LLM_ENABLE_THINKING=false` gives ~1 s replies.
- **Voice choice** (Rocky's "golden" reference) was made by ear in A/B listening. Change it only after listening tests.
- **Recreate strategy**: never two copies of the models loaded at once.
- The API answers one `/talk` at a time (HTTP 503 while busy), so one replica is the right size.
