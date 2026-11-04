# Argo CD

The [Applications](applications/) that track this repo's `main` branch. Argo CD itself was installed by hand and isn't
managed from here.

```bash
kubectl apply -k k8s/argocd/applications
```

- **Auto-sync with prune, no self-heal.** A push to `main` deploys by itself, usually within about 3 minutes: a new image
  digest restarts the voice, and a change to a brain's deployment reloads the model. Self-heal stays off because it
  would revert the quick `kubectl scale` and `rollout undo` shortcuts in the READMEs. A shortcut stays in place until
  the app next syncs, so change Git as well.
- **Brain swaps are two pushes.** Flipping both `replicas` in one commit would start the new model before the old one
  has freed the GPUs. Wait for the old pod to stop before the second push.
- **Prune only removes what Argo CD tracks,** such as old hashed ConfigMaps. Applying these manifests is still by hand:
  Argo CD doesn't manage its own Applications here.
- **No `resources-finalizer`.** Deleting an Application leaves its workloads running.
- **Hand-made objects aren't in Git** (the `esp32-homeai-home` ConfigMap and the `telegram-bot`, `rocky-cam` and
  `searxng` Secrets). Argo CD doesn't touch them, and the namespaces holding them carry
  `argocd.argoproj.io/sync-options: Prune=false,Delete=false`. Back them up privately, never in this repo.
- **Left out:** `k8s/vllm/qwen-llmd` isn't deployed and would compete for the GPUs. Immich, Navidrome and the other
  apps with no manifests here can't be tracked yet.

## Cluster settings it depends on

Neither of these lives in Git:

- **Every node's kubelet uses its Tailscale IP** (`KUBELET_EXTRA_ARGS=--node-ip=<tailscale IP>` in
  `/etc/default/kubelet`). With a mix of LAN and Tailscale node IPs, Cilium's tunnels to that node fail, and pods can't
  reach CoreDNS.
- **CoreDNS forwards to public DNS** (`forward . 1.1.1.1 8.8.8.8` in the `kube-system/coredns` ConfigMap), because a
  CoreDNS pod on a worker timed out on the router. A `kubeadm upgrade` may reset this.
