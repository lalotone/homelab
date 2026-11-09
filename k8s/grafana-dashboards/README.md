# Grafana dashboards

Plain JSON, loaded into the Helm-installed Grafana (`monitoring` namespace) as ConfigMaps labelled
`grafana_dashboard: "1"`. Argo CD's `grafana-dashboards` app deploys them; the Helm release itself isn't touched.

| Dashboard | Shows |
|---|---|
| [Homelab / GPUs](gpu.json) | Utilization, VRAM, temperature and power per card, total GPU power, and estimated energy |
| [Homelab / Machines](machines.json) | One row per node: status, uptime, CPU, RAM, load, CPU temperature, disk and network |
| [Homelab / Markets](markets.json) | World indexes, crypto, gold and EUR/USD: today's change, performance table (1d to 5y), returns, VIX, drawdown, volatility, trend and recent prices. Data from [`../markets`](../markets/) |

## Changing one

Edit it in the Grafana UI, export the JSON ("Export for sharing" off), replace the file here, and push. Changes made
only in the UI aren't kept: `monitoring` has no persistent volumes.

## The Helm release (Prometheus + Grafana)

`prometheus-stack` in `monitoring`, chart `kube-prometheus-stack` 81.5.0, installed by hand with Helm (not Argo CD).
Its custom values, as of 2026-11-09 (revision 2):

```yaml
prometheus:
  prometheusSpec:
    serviceMonitorSelectorNilUsesHelmValues: false   # pick up ServiceMonitors from other releases too
  service:
    type: NodePort
    nodePort: 30100                                  # http://<any node>:30100
grafana:
  adminPassword: <hidden>                            # pinned to the existing one, see below
  service:
    type: NodePort
    nodePort: 30300                                  # http://<any node>:30300, no port-forward needed
```

## Changing it

Always `--reuse-values` and `--version`, so the chart itself isn't upgraded by accident:

    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts   # once
    helm upgrade prometheus-stack prometheus-community/kube-prometheus-stack -n monitoring \
      --version 81.5.0 --reuse-values --set <key>=<value>

Check first with `--dry-run=server` and compare against `helm -n monitoring get manifest prometheus-stack`.

**Grafana's password:** the chart generates a random admin password (in the Secret `prometheus-stack-grafana`) and,
unless `grafana.adminPassword` is set, makes a new one on every upgrade. It was pinned to the existing one in revision 2,
so `--reuse-values` keeps it. Read it with:

    kubectl -n monitoring get secret prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d

A password changed only in the Grafana UI doesn't last: there's no persistent volume, so a restart brings back the
Helm value. To change it, `helm upgrade ... --set grafana.adminPassword=<new>`.

## Limits

- **Power is GPU only.** The energy figure adds up the cards' power once a minute. There are no CPU power metrics, so a
  whole-machine figure would need a smart plug.
- **GPU series are grouped by card,** because DCGM's pod labels can split one card into several series.
- **Node names come from `node_uname_info`,** so the JSON holds no node IPs.
