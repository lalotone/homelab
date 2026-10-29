# SearXNG — private web search for Rocky

[SearXNG](https://docs.searxng.org) is a self-hosted metasearch engine: it asks several search engines at once (Google,
Bing, DuckDuckGo, Brave...) and merges the results, without cookies or an account. Here it serves one client: the voice
assistant's `web_search` tool ([`k8s/esp32-homeai`](../esp32-homeai/); how the tool reads the pages and its guardrails:
esp32-homeai's `api/README.md`, "Web search").

```
voice API (heavy1) ──► searxng.searxng  10.105.13.251:8080 (pinned ClusterIP, heavy1) ──► search engines
                   └─► the top 3 result pages (public addresses only)
```

In-cluster only: no NodePort, no Ingress. Nothing to log in to.

## Setup

```bash
kubectl apply -f namespace.yaml
# The secret_key (signs SearXNG's cookies/tokens). Generated and piped straight in: never printed, never in git.
openssl rand -hex 32 | tr -d '\n' | kubectl -n searxng create secret generic searxng --from-file=secret_key=/dev/stdin
kubectl apply -k .
```

Then the voice API needs `SEARXNG_URL=http://10.105.13.251:8080` (already in `k8s/esp32-homeai/deploy/deployment.yaml`,
inherited by `with-gpu`).

Check, from the voice pod:

```bash
kubectl -n voice exec deploy/esp32-homeai-api -- python -c "import httpx; \
  r = httpx.get('http://10.105.13.251:8080/search', params={'q': 'day trips from Paris', 'format': 'json'}); \
  print(r.status_code, len(r.json()['results']), 'results')"
```

## Why it's set up this way

| Choice | Why |
|---|---|
| Runs on heavy1, pinned ClusterIP `10.105.13.251` | pods on heavy1 can't reach CoreDNS or pods on other nodes (cross-node traffic broken since 2026-10-20), so client and server share the node and the client dials an IP. Next to the brain's `10.105.13.250`. |
| Public DNS (1.1.1.1, 8.8.8.8) in the pod | same reason: it must resolve the search engines' names. Drop it (and the nodeSelector) once cross-node traffic is fixed. |
| `settings.yml`: `use_default_settings` + a few changes | the stock engines, plus: the `json` format on (what the API reads), the bot limiter off (it's for public instances and would need Valkey), safe search moderate, a 3 s engine timeout (a voice answer can't wait for a slow engine). |
| Image pinned to a date tag | SearXNG has no versions, only daily builds; pinned to one at least two weeks old. Bump `newTag` in `kustomization.yaml`. |

## Notes

- Search engines sometimes rate-limit a SearXNG (seen: Brave, then DuckDuckGo, answering nothing for a while). The
  others fill in; the response's `unresponsive_engines` says which are resting. Rocky says he couldn't find anything
  only if all of them fail.
- The logs show `ahmia`/`torch` "can't register engine": those are Tor-only engines, harmless.
- `X-Forwarded-For nor X-Real-IP header is set` in the logs: the bot detection noticing direct calls without a proxy;
  harmless with the limiter off.
