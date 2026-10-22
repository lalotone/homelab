#!/bin/bash
# NVIDIA GPU Operator with the host driver (driver.enabled=false).
# Chart pinned to the installed version; time-slicing config in time-slicing/ (apply its ConfigMap first).
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia && helm repo update
kubectl create namespace gpu-operator --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -k "$(dirname "$0")/time-slicing"
helm install gpu-operator nvidia/gpu-operator \
  -n gpu-operator \
  --version v26.7.1 \
  --set driver.enabled=false \
  --set devicePlugin.config.name=time-slicing-config \
  --set devicePlugin.config.default=any
