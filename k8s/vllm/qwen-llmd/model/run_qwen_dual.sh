#!/bin/bash
# Caution: Running this will scale down the replicas for Qwen3.8 to 0
# 1. Scale the 27B down; it uses both cards
kubectl -n llm scale deploy/vllm-qwen38 --replicas=0

# 2. Bring up the two replicas. If we go OOM, start with 1 replica instead 2
kubectl apply -k .
kubectl -n llm get pods -l app=vllm-qwen3-8b -o wide -w
