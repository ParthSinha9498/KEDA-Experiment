# CLAUDE.md

Part 1 of a Medium series + GitHub repo: autoscaling a 3-stage document pipeline
(OCR -> LLM extraction -> render) on Redis queues with KEDA on minikube, benchmarking elastic
capacity against fixed worker pools. All workloads are mocks. Target: end of October 2026.

Core thesis: scale out CPU/I-O-bound stages on queue depth; for a rate-limited LLM stage, batch to
the quota and stream results onward. The LLM stage is a design pattern only, not measured.
There is ONE experiment (KEDA). The LLM rate-limit experiment was dropped.

## Read first
- `../keda-blog-brief.md` - the reviewed design and every decision (source of truth)
- `README.md` - layout, setup, exact commands
- `BLOG.md` - post draft (Results section still empty)
- `HANDOVER.md` - current state, what remains, gotchas

## Architecture
- `src/worker.py` - one worker for every stage, picked by `STAGE=ocr|llm|render`
- `src/mock_workloads.py` - OCR CPU burn (0.05s CPU/KB), LLM token+latency model, render sleep
- `src/limits.py` - RPM, TPM and concurrency limits held in Redis (Lua token bucket) so they are
  global across replicas, not per pod
- `src/producer.py` - pushes bursts to `ocr_queue` (defaults: 3 bursts x 1000, 300s apart; `--reset`)
- `src/collector.py` - samples queue depths + ready replicas each second, writes
  `runs/<run>/{timeseries.csv,results.jsonl,meta.json}`; `--mode k8s|compose|static`
- `src/common.py` - Redis connect, key names, clock
- `scripts/make_docs.py` - generates `data/sample_docs.json` (10 docs, 5-15KB, fixed seed)
- `scripts/checks.py` - design checks, modes `unit|run|scaledown`, PASS/FAIL vs fixed thresholds
- `scripts/report.py` / `scripts/plot.py` - results table / figures from run directories
- `docker/Dockerfile` (image `keda-docs:dev`), `docker/docker-compose.yml` (fixed pools, dev only)
- `k8s/base` - namespace `keda-demo`, redis, ocr/llm/render Deployments (`imagePullPolicy: Never`)
- `k8s/keda/scaledobjects.yaml` - ScaledObjects for OCR and render only (no LLM ScaledObject, on
  purpose). redis trigger, listLength 50, pollingInterval 5, cooldown 30, max 15, HPA scale-down
  window 30s, scale-up up to +15 pods/15s
- `k8s/overlays/` - one per run: `fixed-2`, `fixed-15` (replica counts, no KEDA), `keda-0`
  (min 0), `keda-1` (identical but minReplicaCount 1: isolates the cost of scale to zero)
- `scripts/minikube_setup.sh` - docker driver, all host CPUs, 8g, KEDA via helm, builds + loads image
- `scripts/run_k8s.sh <run>` - applies overlay, port-forwards Redis to 16379, waits to settle,
  resets Redis, runs collector + producer
- `scripts/run_local.sh` - quick trial: Redis container `keda-redis` on 6379, workers as processes
- `checks/` - committed check output; `runs/`, `figures/` - experiment output (not created yet)

## Commands (from README/scripts; verify before use)
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/make_docs.py
docker run -d --name keda-redis -p 6379:6379 redis:7-alpine
.venv/bin/python -m scripts.checks unit --out checks/unit.txt
OCR=8 RENDER=15 BURSTS=1 BURST_SIZE=600 scripts/run_local.sh
.venv/bin/python -m scripts.checks run --out checks/run.txt

scripts/minikube_setup.sh
scripts/run_k8s.sh fixed-2      # also fixed-15, keda-0, keda-1; each >= 17 min
.venv/bin/python -m scripts.checks run --run-dir runs/fixed-15 --out checks/fixed-15.txt
.venv/bin/python -m scripts.checks scaledown --run-dir runs/keda-0 --min-replicas 0 --out checks/keda-0-scaledown.txt
.venv/bin/python -m scripts.checks scaledown --run-dir runs/keda-1 --min-replicas 1 --out checks/keda-1-scaledown.txt
.venv/bin/python -m scripts.report runs/fixed-2 runs/fixed-15 runs/keda-0 runs/keda-1 --out results.md
.venv/bin/python -m scripts.plot runs/fixed-2 runs/fixed-15 runs/keda-0 runs/keda-1 --out figures/
```
Run Python modules as `-m src.x` / `-m scripts.x` from the project root. No test suite beyond
`scripts/checks.py`. kubectl/minikube/helm live in `~/.local/bin`.

## Rules
- The user reviews code BEFORE any testing or benchmarking. Do not run experiments, minikube,
  docker builds or long jobs unless explicitly asked.
- Never invent or predict numbers. Every figure in BLOG.md comes from `runs/` via report/plot.
- LLM limits are expressed as token caps (TPM, `LLM_MAX_INPUT_TOKENS`), not call counts.
- The 32k batch ceiling (`LLM_MAX_INPUT_TOKENS=32000`) is this experiment's setting, not a
  recommendation. Say so wherever it appears.
- LLM defaults are deliberately loose so the LLM never binds; `checks run` verifies a >=2x margin.
- Part 2 scope (Airflow, retries, DLQ, idempotency, Prometheus, cost) stays out of Part 1.
- Docs are local markdown. Critique should be blunt and numbered (blocking / should fix / minor).
- Keep comments explaining *why*; match the existing terse style.

## Known fixes (2026-10-19)
- `scripts/run_k8s.sh` `ready()`: `sed 's/^$/0/'` does not match when `kubectl jsonpath` emits
  no trailing newline. Fixed to `${r:-0}`. Without this, keda-0/keda-1 settle step always times out.
- Lost-doc risk: if a previous run left a worker mid-BRPOP, resetting Redis orphans that doc.
  Start each full experiment run from a clean minikube state (see HANDOVER.md Gotcha 8).

## Machine for experiments
Run the 4 full experiments on the **Lenovo Legion 5i Pro (WSL2/Ubuntu)**, not the dev machine.
The i7-1165G7 (4c/8t, 28W) saturates OCR too early; the Legion's H-series chip (14-16 cores)
gives a longer ramp and a visible speedup — that is the blog's core result.
Setup on Legion: `git clone` → `scripts/minikube_setup.sh` → `scripts/run_all.sh`.
Keep docker driver; do not use VM driver (clock skew, Gotcha 5).
