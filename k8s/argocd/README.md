# Argo CD

The [Applications](applications/) that track this repo's `main` branch. Argo CD itself was installed by hand and isn't
managed from here.

```bash
kubectl apply -k k8s/argocd/applications
```

- **Manual sync on purpose.** There's no `syncPolicy`, so nothing changes until you press Sync. Turn on auto-sync per
  app once it has proved itself, and leave self-heal off until the READMEs' `kubectl scale` and `rollout undo` steps
  are rewritten as Git changes: self-heal would revert them.
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
