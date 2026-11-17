#!/bin/bash
# KServe in Standard mode (plain Deployments, no Istio/Knative), installed with Kustomize from the v0.21.0 release
# (a git worktree of the upstream repo at that tag). Not managed by Argo CD.
# --kserve-version swaps every :latest in the manifests for :v0.21.0, and UPDATE_CONFIGMAP_IMAGES=true does the same
# for the images in the inferenceservice-config ConfigMap (agent, storage initializer). Side effect: the built-in
# kserve-vllmserver runtime points at vllm/vllm-openai:v0.21.0; our InferenceServices set runtimeVersion instead.
# Pins from v0.21.0's kserve-deps.env: cert-manager v1.17.0, Gateway API v1.5.1, Inference Extension v1.5.0,
# Envoy Gateway v1.8.1, Envoy AI Gateway v1.1.0, LeaderWorkerSet v0.8.0.
set -euo pipefail
KSERVE_VERSION=v0.21.0
KSERVE_REPO="${KSERVE_REPO:-$HOME/src/repos/kserve}"
KSERVE_DIR="${KSERVE_DIR:-$HOME/src/repos/kserve-$KSERVE_VERSION}"
[ -d "$KSERVE_REPO" ] || git clone https://github.com/kserve/kserve.git "$KSERVE_REPO"
if [ ! -d "$KSERVE_DIR" ]; then
  git -C "$KSERVE_REPO" fetch --tags origin
  git -C "$KSERVE_REPO" worktree add --detach "$KSERVE_DIR" "$KSERVE_VERSION"
fi
cd "$KSERVE_DIR"
# FORCE_UPGRADE: if KServe is already installed, apply over it in place (never --reinstall: it deletes the CRDs, and
# with them every InferenceService).
FORCE_UPGRADE=true UPDATE_CONFIGMAP_IMAGES=true ./hack/kserve-install.sh --kustomize --standard --kserve-version "$KSERVE_VERSION" "$@"

# Run the controllers on k8s-master, next to the API server. On endurance-1 (WiFi) they lost the API connection
# ("http2: client connection lost"), lost their leader lease and crash-looped every ~5 min (2026-11-17).
PATCH='{"spec":{"template":{"spec":{"nodeSelector":{"kubernetes.io/hostname":"k8s-master"},"tolerations":[{"key":"node-role.kubernetes.io/control-plane","operator":"Exists","effect":"NoSchedule"}]}}}}'
kubectl -n kserve patch deploy kserve-controller-manager --type merge -p "$PATCH"
for d in cert-manager cert-manager-cainjector cert-manager-webhook; do
  kubectl -n cert-manager patch deploy "$d" --type merge -p "$PATCH"
done
