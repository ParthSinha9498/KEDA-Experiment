#!/usr/bin/env bash
# Trial run on this machine: Redis in Docker, workers as local processes.
#
#   OCR=4 RENDER=4 BURSTS=1 BURST_SIZE=300 INTERVAL=0 scripts/run_local.sh
#
# Leaves results in Redis for `python -m scripts.checks run`.
set -euo pipefail
cd "$(dirname "$0")/.."

OCR=${OCR:-4}
RENDER=${RENDER:-4}
BURSTS=${BURSTS:-1}
BURST_SIZE=${BURST_SIZE:-300}
INTERVAL=${INTERVAL:-0}
PY=${PY:-.venv/bin/python}
TOTAL=$((BURSTS * BURST_SIZE))

if ! docker ps --format '{{.Names}}' | grep -qx keda-redis; then
  docker rm -f keda-redis >/dev/null 2>&1 || true
  docker run -d --name keda-redis -p 6379:6379 redis:7-alpine >/dev/null
  sleep 1
fi
redis_llen() { docker exec keda-redis redis-cli LLEN "$1"; }

mkdir -p logs
pids=()
stop_workers() { kill -TERM "${pids[@]}" 2>/dev/null || true; wait "${pids[@]}" 2>/dev/null || true; }
trap stop_workers EXIT

# Reset before workers start so none of them reads leftover state.
$PY -c "from src.common import connect; from src.producer import reset; reset(connect())"

for i in $(seq 1 "$OCR"); do STAGE=ocr $PY -m src.worker >"logs/ocr-$i.log" 2>&1 & pids+=($!); done
STAGE=llm $PY -m src.worker >logs/llm.log 2>&1 & pids+=($!)
for i in $(seq 1 "$RENDER"); do STAGE=render $PY -m src.worker >"logs/render-$i.log" 2>&1 & pids+=($!); done

start=$(date +%s)
$PY -m src.producer --bursts "$BURSTS" --burst-size "$BURST_SIZE" --interval "$INTERVAL"

while [ "$(redis_llen results)" -lt "$TOTAL" ]; do
  echo "$(date +%T) results $(redis_llen results)/$TOTAL  ocr_queue $(redis_llen ocr_queue)  llm_queue $(redis_llen llm_queue)  render_queue $(redis_llen render_queue)"
  sleep 5
done
echo "done: $TOTAL docs in $(( $(date +%s) - start ))s"
