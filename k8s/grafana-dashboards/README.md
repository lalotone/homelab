# Grafana dashboards

Plain JSON, loaded into the Helm-installed Grafana (`monitoring` namespace) as ConfigMaps labelled
`grafana_dashboard: "1"`. Argo CD's `grafana-dashboards` app deploys them; the Helm release itself isn't touched.

| Dashboard | Shows |
|---|---|
| [Homelab / GPUs](gpu.json) | Utilization, VRAM, temperature and power per card, total GPU power, and estimated energy |
| [Homelab / Machines](machines.json) | One row per node: status, uptime, CPU, RAM, load, CPU temperature, disk and network |

## Changing one

Edit it in the Grafana UI, export the JSON ("Export for sharing" off), replace the file here, and push. Changes made
only in the UI aren't kept: `monitoring` has no persistent volumes.

## Limits

- **Power is GPU only.** The energy figure adds up the cards' power once a minute. There are no CPU power metrics, so a
  whole-machine figure would need a smart plug.
- **GPU series are grouped by card,** because DCGM's pod labels can split one card into several series.
- **Node names come from `node_uname_info`,** so the JSON holds no node IPs.
