# KEDA document processing

A 3-stage document pipeline (OCR → LLM extraction → rendering) on Redis queues, used to measure
elastic capacity (KEDA) against fixed worker pools. All workloads are mocks. The design and every
decision behind it are in [the brief](../keda-blog-brief.md); the post draft is [BLOG.md](BLOG.md).

## Layout

| Path | What it does |
| --- | --- |
| `src/mock_workloads.py` | OCR CPU burn (0.05s CPU per KB), LLM token and latency model, render sleep |
| `src/limits.py` | RPM, TPM and concurrency limits shared through Redis |
| `src/worker.py` | One worker for every stage; `STAGE=ocr\|llm\|render` |
| `src/producer.py` | Submits bursts of docs to `ocr_queue` |
| `src/collector.py` | Samples queue depths and ready replicas every second; writes a run directory |
| `scripts/make_docs.py` | Generates the 10 sample docs (5–15KB, mean 10KB, fixed seed) |
| `scripts/checks.py` | Design checks, pass/fail against fixed thresholds |
| `scripts/report.py` | Results table from run directories |
| `scripts/plot.py` | The post's figures from run directories |
| `scripts/run_local.sh` | Quick trial: Redis in Docker, workers as local processes |
| `scripts/minikube_setup.sh` | One-time cluster setup: minikube, KEDA, worker image |
| `scripts/run_k8s.sh` | One experiment run on minikube |
| `docker/` | Worker image and a compose file for fixed pools |
| `k8s/` | Base manifests, KEDA ScaledObjects, one overlay per run |
| `checks/` | Committed output of the design checks |
| `runs/` | Committed run data: `timeseries.csv`, `results.jsonl`, `meta.json` |

## Setup

Needs Python 3.12 and Docker; the experiment also needs minikube, kubectl and helm.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/make_docs.py
```

## Design checks

```bash
docker run -d --name keda-redis -p 6379:6379 redis:7-alpine   # for the concurrency check
.venv/bin/python -m scripts.checks unit --out checks/unit.txt

# A quick trial at fixed-15 scale on this machine
OCR=8 RENDER=15 BURSTS=1 BURST_SIZE=600 scripts/run_local.sh
.venv/bin/python -m scripts.checks run --out checks/run.txt
```

After the experiment, the full checks run against its run directories:

```bash
.venv/bin/python -m scripts.checks run --run-dir runs/fixed-15 --out checks/fixed-15.txt
.venv/bin/python -m scripts.checks scaledown --run-dir runs/keda-0 --min-replicas 0 --out checks/keda-0-scaledown.txt
.venv/bin/python -m scripts.checks scaledown --run-dir runs/keda-1 --min-replicas 1 --out checks/keda-1-scaledown.txt
```

## The experiment (minikube)

All four runs happen on minikube so fixed and elastic capacity share one environment.

```bash
scripts/minikube_setup.sh   # docker driver, all host CPUs, KEDA via helm, image loaded

scripts/run_k8s.sh fixed-2
scripts/run_k8s.sh fixed-15
scripts/run_k8s.sh keda-0
scripts/run_k8s.sh keda-1

.venv/bin/python -m scripts.report runs/fixed-2 runs/fixed-15 runs/keda-0 runs/keda-1 --out results.md
.venv/bin/python -m scripts.plot runs/fixed-2 runs/fixed-15 runs/keda-0 runs/keda-1 --out figures/
```

Each run takes at least 15 minutes: 3 bursts of 1000 docs, one every 300s, then a 120s tail so
KEDA's scale-down is captured. `run_k8s.sh` switches the cluster to the run's overlay, waits for
OCR and render to settle at their starting replica count, clears Redis, then starts the collector
and the producer.

Notes:

- **One clock.** Turnaround compares `submitted_at` (producer, on the host) with `completed_at` (a
  pod). The docker driver shares the host's clock; a VM driver may not.
- **Recorded environment.** Each run's `meta.json` records kubectl, minikube and KEDA versions, the
  controller-manager flags (sync period, tolerance), the ScaledObjects and HPAs as applied, the LLM
  limits and the machine spec.
- **Shared measurement window.** `scripts/report.py` integrates replica-seconds from t=0 to the
  later of the injection window and the slowest run's completion, for every run, and says how long
  each run's last sample was held to reach it.

## docker compose (fixed pools, for development)

```bash
docker compose -f docker/docker-compose.yml up -d --build --scale ocr=2 --scale render=2
.venv/bin/python -m src.collector --run-dir runs/dev-fixed-2 --mode compose --min-duration 0 &
.venv/bin/python -m src.producer --bursts 1 --burst-size 300 --reset
```

Stop `keda-redis` first if it's running: both use port 6379.

## LLM stage settings

| Env var | Default | Meaning |
| --- | --- | --- |
| `LLM_RPM` | 100000 | Requests per minute |
| `LLM_TPM` | 50000000 | Tokens per minute (input + output) |
| `LLM_CONCURRENCY` | 64 | Calls in flight |
| `LLM_MAX_INPUT_TOKENS` | 32000 | Batch ceiling for this experiment, not a recommendation |
| `LLM_FLUSH_S` | 2.0 | Send a partial batch this long after its first doc |

The defaults are set so the LLM stage doesn't bind; `scripts/checks.py run` verifies the margin
against measured OCR throughput.
