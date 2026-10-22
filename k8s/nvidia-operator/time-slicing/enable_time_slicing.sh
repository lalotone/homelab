#!/bin/bash
# Enable GPU time-slicing: apply the ConfigMap, then point the GPU Operator's device plugin at it via Helm.
# Helm rather than `kubectl patch clusterpolicy`: a manual patch is reverted by the next `helm upgrade`.
set -euo pipefail
cd "$(dirname "$0")"

kubectl apply -k .
helm upgrade gpu-operator nvidia/gpu-operator -n gpu-operator \
  --version v26.7.1 \
  --reuse-values \
  --set devicePlugin.config.name=time-slicing-config \
  --set devicePlugin.config.default=any

echo "Waiting for the device plugin to restart with the new config..."
kubectl -n gpu-operator rollout status ds/nvidia-device-plugin-daemonset --timeout=5m
kubectl get node heavy1 -o jsonpath='{"allocatable nvidia.com/gpu: "}{.status.allocatable.nvidia\.com/gpu}{"\n"}'
