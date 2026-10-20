#!/usr/bin/env bash
# One-time cluster setup: minikube, KEDA, and the worker image.
#
#   scripts/minikube_setup.sh
#
# Uses the docker driver so pods and the host share one clock: turnaround
# compares submitted_at (producer, on the host) with completed_at (a pod).
set -euo pipefail
cd "$(dirname "$0")/.."

CPUS=${CPUS:-$(nproc)}
MEMORY=${MEMORY:-8g}
IMAGE=keda-docs:dev

minikube start --driver=docker --cpus="$CPUS" --memory="$MEMORY"

helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
helm repo update kedacore
helm upgrade --install keda kedacore/keda --namespace keda --create-namespace --wait

docker build -f docker/Dockerfile -t "$IMAGE" .
minikube image load "$IMAGE"

echo "ready: minikube with $CPUS CPUs, KEDA $(kubectl get deploy -n keda keda-operator \
  -o jsonpath='{.spec.template.spec.containers[0].image}'), image $IMAGE"
