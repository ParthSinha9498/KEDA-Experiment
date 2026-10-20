#!/usr/bin/env bash
# Run all 4 experiment runs back-to-back, with a minikube restart between each
# to avoid leftover pod state causing lost docs (no ack-and-requeue in Part 1).
#
# Usage:
#   scripts/run_all.sh [--runs fixed-2,fixed-15,keda-0,keda-1] [--skip-checks] [--skip-plot]
#
# Outputs:
#   runs/<run>/timeseries.csv, results.jsonl, meta.json  (per run)
#   checks/<run>.txt, checks/<run>-scaledown.txt          (per run, if applicable)
#   results.md                                            (combined report)
#   figures/                                              (combined plots)
#
# Each run takes ~25-35 min; total wall time ~2.5 hours.
# Keep the session alive: don't close the terminal or let the machine sleep.
set -euo pipefail
cd "$(dirname "$0")/.."

MINIKUBE=${MINIKUBE:-$HOME/.local/bin/minikube}
KUBECTL=${KUBECTL:-$HOME/.local/bin/kubectl}
PY=${PY:-.venv/bin/python}
RUNS=${RUNS:-"fixed-2 fixed-15 keda-0 keda-1"}
SKIP_CHECKS=${SKIP_CHECKS:-0}
SKIP_PLOT=${SKIP_PLOT:-0}

# Parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --runs)        RUNS="${2//,/ }"; shift 2 ;;
    --skip-checks) SKIP_CHECKS=1; shift ;;
    --skip-plot)   SKIP_PLOT=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { echo "[run_all] $(date '+%H:%M:%S') $*"; }

export PATH="$(dirname "$MINIKUBE"):$PATH"

# ── helpers ──────────────────────────────────────────────────────────────────

restart_minikube() {
  log "restarting minikube (docker driver)..."
  "$MINIKUBE" stop --cancel-scheduled 2>/dev/null || true
  "$MINIKUBE" start --driver=docker
  log "minikube ready."
}

run_checks() {
  local run=$1
  log "running checks for $run..."
  mkdir -p checks

  # 'run' check: applies to all runs that have a results dir
  "$PY" -m scripts.checks run --run-dir "runs/$run" \
    --out "checks/${run}.txt" && log "checks/run PASS for $run" \
    || log "WARNING: checks/run had failures for $run (see checks/${run}.txt)"

  # 'scaledown' check: only for keda runs
  case "$run" in
    keda-0)
      "$PY" -m scripts.checks scaledown --run-dir "runs/$run" --min-replicas 0 \
        --out "checks/${run}-scaledown.txt" \
        && log "checks/scaledown PASS for $run" \
        || log "WARNING: checks/scaledown had failures for $run"
      ;;
    keda-1)
      "$PY" -m scripts.checks scaledown --run-dir "runs/$run" --min-replicas 1 \
        --out "checks/${run}-scaledown.txt" \
        && log "checks/scaledown PASS for $run" \
        || log "WARNING: checks/scaledown had failures for $run"
      ;;
  esac
}

# ── main loop ─────────────────────────────────────────────────────────────────

RUNS_ARRAY=($RUNS)
TOTAL=${#RUNS_ARRAY[@]}
log "will run: ${RUNS_ARRAY[*]} ($TOTAL runs, ~$((TOTAL * 30)) min estimated)"
log "=================================================="

START_ALL=$(date +%s)

for i in "${!RUNS_ARRAY[@]}"; do
  RUN="${RUNS_ARRAY[$i]}"
  RUN_NUM=$((i + 1))
  log "── Run $RUN_NUM/$TOTAL: $RUN ──────────────────────────"

  # Always restart minikube before each run for a clean slate
  restart_minikube

  RUN_START=$(date +%s)
  log "starting experiment: $RUN"
  bash scripts/run_k8s.sh "$RUN"
  RUN_END=$(date +%s)
  RUN_MIN=$(( (RUN_END - RUN_START) / 60 ))
  log "$RUN done in ${RUN_MIN} min. Output: runs/$RUN/"

  if [[ "$SKIP_CHECKS" -eq 0 ]]; then
    run_checks "$RUN"
  fi

  ELAPSED=$(( (RUN_END - START_ALL) / 60 ))
  REMAINING=$(( (TOTAL - RUN_NUM) * RUN_MIN ))
  log "progress: $RUN_NUM/$TOTAL done. ~${ELAPSED} min elapsed, ~${REMAINING} min remaining."
  log "=================================================="
done

# ── report + figures ──────────────────────────────────────────────────────────

# Collect whichever run dirs exist
RUN_DIRS=""
for r in $RUNS; do
  [[ -d "runs/$r" ]] && RUN_DIRS="$RUN_DIRS runs/$r"
done

if [[ "$SKIP_PLOT" -eq 0 ]] && [[ -n "$RUN_DIRS" ]]; then
  log "generating report..."
  "$PY" -m scripts.report $RUN_DIRS --out results.md \
    && log "results.md written." \
    || log "WARNING: report failed."

  log "generating figures..."
  mkdir -p figures
  "$PY" -m scripts.plot $RUN_DIRS --out figures/ \
    && log "figures/ written." \
    || log "WARNING: plot failed."
fi

TOTAL_MIN=$(( ($(date +%s) - START_ALL) / 60 ))
log "all done in ${TOTAL_MIN} min."
log "next step: fill BLOG.md [MEASURED: ...] blocks from results.md and figures/."
