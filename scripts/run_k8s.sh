#!/usr/bin/env bash
# One experiment run on minikube. Writes runs/<run>/ via the collector.
#
#   scripts/run_k8s.sh fixed-2|fixed-15|keda-0|keda-1
#
# Load defaults to the experiment's: 3 bursts of 1000 docs, one every 300s.
# The producer and collector run on the host through a port-forward.
set -euo pipefail
cd "$(dirname "$0")/.."

RUN=${1:?usage: scripts/run_k8s.sh fixed-2|fixed-15|keda-0|keda-1}
NS=keda-demo
PY=${PY:-.venv/bin/python}
BURSTS=${BURSTS:-3}
BURST_SIZE=${BURST_SIZE:-1000}
INTERVAL=${INTERVAL:-300}
PORT=${PORT:-16379}  # not 6379, so it can't collide with a local Redis
export REDIS_URL="redis://localhost:$PORT/0"

case "$RUN" in
  fixed-2|fixed-15) MIN_REPLICAS="" ;;
  keda-0) MIN_REPLICAS=0 ;;
  keda-1) MIN_REPLICAS=1 ;;
  *) echo "unknown run $RUN" >&2; exit 1 ;;
esac

ready() { r=$(kubectl get deploy "$1" -n "$NS" -o jsonpath='{.status.readyReplicas}' 2>/dev/null); echo "${r:-0}"; }

# Switch the cluster to this run's configuration. apply doesn't remove
# ScaledObjects from a previous KEDA run, so delete them first.
kubectl delete scaledobject --all -n "$NS" --ignore-not-found 2>/dev/null || true
kubectl apply -k "k8s/overlays/$RUN"
kubectl rollout status deploy/redis deploy/llm -n "$NS" --timeout=180s

kubectl port-forward -n "$NS" svc/redis "$PORT:6379" >/dev/null 2>&1 &
PF=$!
trap 'kill $PF 2>/dev/null || true' EXIT
until $PY -c "from src.common import connect; connect().ping()" 2>/dev/null; do sleep 0.5; done

# Start from a clean, settled state: empty queues, and OCR/render at their
# starting replica count (the fixed count, or KEDA's minimum).
$PY -c "from src.common import connect; from src.producer import reset; reset(connect())"
if [ -z "$MIN_REPLICAS" ]; then
  kubectl rollout status deploy/ocr deploy/render -n "$NS" --timeout=300s
else
  echo "waiting for KEDA to settle OCR and render at $MIN_REPLICAS replicas"
  for _ in $(seq 1 120); do
    [ "$(ready ocr)" = "$MIN_REPLICAS" ] && [ "$(ready render)" = "$MIN_REPLICAS" ] && break
    sleep 5
  done
  [ "$(ready ocr)" = "$MIN_REPLICAS" ] && [ "$(ready render)" = "$MIN_REPLICAS" ] \
    || { echo "OCR/render didn't settle at $MIN_REPLICAS replicas" >&2; exit 1; }
fi

mkdir -p "runs/$RUN"
$PY -m src.collector --run-dir "runs/$RUN" --mode k8s --namespace "$NS" \
  --min-duration "$(( BURSTS * ${INTERVAL%.*} ))" &
COLLECTOR=$!
sleep 2
$PY -m src.producer --bursts "$BURSTS" --burst-size "$BURST_SIZE" --interval "$INTERVAL"
wait "$COLLECTOR"
echo "done: runs/$RUN"
