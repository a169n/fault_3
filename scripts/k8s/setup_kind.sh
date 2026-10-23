#!/usr/bin/env bash
# Create a local kind cluster, build the image and deploy everything.
# Works on macOS (bash 3.2) and Linux. Requirements: Docker Desktop (or colima),
# kind, kubectl  ->  brew install kind kubectl
set -euo pipefail
cd "$(dirname "$0")/../.."

CLUSTER=aitu
IMAGE=aitu-demo/registration-service:1.0.0

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --config scripts/k8s/kind-cluster.yaml
fi
kubectl config use-context "kind-$CLUSTER"

docker build -t "$IMAGE" app/
kind load docker-image "$IMAGE" --name "$CLUSTER"

kubectl apply -f k8s/
kubectl -n aitu-sss rollout status statefulset/postgres --timeout=180s
kubectl -n aitu-sss rollout status deployment/registration-service --timeout=180s
kubectl -n aitu-sss get pods -o wide
echo
echo "Ready. Next: python3 scripts/k8s/experiments/one_replica.py (or all_replicas.py, database_down.py, liveness_hang.py)"
