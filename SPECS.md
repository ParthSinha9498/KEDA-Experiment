# SPECS.md — Remaining tasks for KEDA blog Part 1
# Self-contained reference for a fresh session. Last updated: 2026-10-19.
# Read this file first. Everything you need is here.

---

## What this project is

Medium blog post + GitHub repo: autoscaling a 3-stage document pipeline
(OCR → LLM extraction → render) on Redis queues with KEDA on minikube.
Benchmarks elastic capacity (KEDA) against fixed worker pools (fixed-2, fixed-15).
All workloads are mocks. Target: end of October 2026.

**Core thesis:** Scale out CPU/IO-bound stages on queue depth. For the rate-limited LLM
stage, batch to quota and stream onward — design pattern only, not measured.
ONE experiment (KEDA). LLM rate-limit experiment was dropped.

---

## Project layout (key files only)

```
src/worker.py            one worker for all stages; STAGE=ocr|llm|render env var
src/producer.py          pushes bursts to ocr_queue (3x1000 docs, 300s apart)
src/collector.py         samples queue depths + replicas → runs/<run>/timeseries.csv
src/mock_workloads.py    OCR=0.05s CPU/KB, LLM=token+latency, render=0.5s sleep
src/limits.py            RPM/TPM/concurrency in Redis (Lua token bucket, global across pods)
src/common.py            Redis connect, key names, clock

scripts/make_docs.py     generates data/sample_docs.json (10 docs, 5–15KB, fixed seed)
scripts/checks.py        PASS/FAIL checks: unit | run | scaledown
scripts/report.py        results table from runs/ → results.md
scripts/plot.py          figures from runs/ → figures/
scripts/minikube_setup.sh  installs minikube/kubectl/helm, KEDA 2.21.0, builds+loads image
scripts/run_k8s.sh <run>   runs one experiment end-to-end (apply overlay, port-forward, collect)
scripts/run_all.sh         orchestrates all 4 runs with minikube restarts + checks + report

k8s/base/                namespace keda-demo, redis/ocr/llm/render Deployments
k8s/keda/scaledobjects.yaml  OCR+render ScaledObjects (listLength 50, max 15, cooldown 30)
k8s/overlays/fixed-2/    2 OCR + 2 render replicas, no KEDA
k8s/overlays/fixed-15/   15 OCR + 15 render replicas, no KEDA
k8s/overlays/keda-0/     KEDA, minReplicaCount 0
k8s/overlays/keda-1/     KEDA, minReplicaCount 1 (control: isolates scale-to-zero cost)

docker/Dockerfile        image keda-docs:dev (imagePullPolicy: Never)
docker/docker-compose.yml  fixed pools, dev only

BLOG.md                  post draft — Results + placeholders still empty
HANDOVER.md              current state, decisions, gotchas
CLAUDE.md                architecture quick-ref + rules
```

---

## Current state (as of 2026-10-19)

- ✅ All code complete and passing design checks (unit + run)
- ✅ Minikube cluster set up (docker driver, KEDA 2.21.0); tools in `~/.local/bin`
- ✅ Bug fixed: `scripts/run_k8s.sh` `ready()` now uses `${r:-0}` not `sed 's/^$/0/'`
  (sed doesn't fire without trailing newline from kubectl jsonpath; caused settle timeouts)
- ✅ `scripts/run_all.sh` orchestration script written
- ✅ Smoke run (keda-0) done on dev machine: 2999/3000 docs, timeseries.csv saved
- ❌ `runs/keda-0/results.jsonl` and `meta.json` NOT written (collector killed; re-run needed)
- ❌ No `runs/fixed-2/`, `runs/fixed-15/`, `runs/keda-1/` yet
- ❌ No `figures/` directory
- ❌ BLOG.md Results section and all `[MEASURED: ...]` blocks are empty

---

## Machine for experiments

**DO NOT run experiments on the dev machine (i7-1165G7, 4c/8t, 28W).**
OCR saturates at ~4-6 replicas — the speedup story is weak.

**Run on: Lenovo Legion 5i Pro (WSL2 + Ubuntu)**
- H-series CPU (14-16 cores, 45W+) → longer ramp, higher saturation ceiling
- Makes the KEDA speedup visible in charts — that is the blog's core result
- WSL2 works fine with docker driver; do NOT use VM driver (clock skew)

**Setup on Legion (one-time, ~30 min):**
```bash
git clone <repo> && cd keda-document-processing
bash scripts/minikube_setup.sh
```

---

## Remaining tasks — in strict order

### TASK 1: Run all 4 experiments (Legion, ~2.5 hours total)

```bash
# From project root on the Legion:
bash scripts/run_all.sh
```

This script does automatically:
- `minikube stop && minikube start` between each run (avoids lost-doc issue)
- `scripts/run_k8s.sh fixed-2` → `fixed-15` → `keda-0` → `keda-1`
- `scripts.checks run` + `scripts.checks scaledown` after each run
- `scripts.report` → `results.md` and `scripts.plot` → `figures/` at the end

**Expected outputs after Task 1:**
```
runs/fixed-2/{timeseries.csv,results.jsonl,meta.json}
runs/fixed-15/{timeseries.csv,results.jsonl,meta.json}
runs/keda-0/{timeseries.csv,results.jsonl,meta.json}
runs/keda-1/{timeseries.csv,results.jsonl,meta.json}
checks/fixed-2.txt
checks/fixed-15.txt
checks/keda-0.txt, checks/keda-0-scaledown.txt
checks/keda-1.txt, checks/keda-1-scaledown.txt
results.md
figures/hero.png, figures/replicas.png, figures/queues.png
```

**Do not proceed to Task 2 until all 4 runs are in `runs/`.**

---

### TASK 2: Fill BLOG.md placeholders (from measured data only)

Open `BLOG.md`. Fill every item below. **Never invent numbers.**

#### 2a. Three code TODOs (inline in BLOG.md)

| Line | TODO | What to do |
|------|------|------------|
| 34 | `<!-- TODO: export the architecture diagram from the brief -->` | Export/copy architecture diagram from `../keda-blog-brief.md` as `figures/architecture.png` and ensure `![Pipeline architecture](figures/architecture.png)` renders |
| 60 | `<!-- TODO: excerpt the sync loop from src/worker.py: BRPOP → process → LPUSH -->` | Paste the relevant ~10-line loop from `src/worker.py` as a fenced Python code block |
| 64 | `<!-- TODO: excerpt k8s/keda/scaledobjects.yaml and explain each field -->` | Paste the ScaledObject YAML and add a brief explanation of each field inline |

#### 2b. Six `[MEASURED: ...]` blocks — fill from `results.md` + `figures/`

| Block | What to write |
|-------|--------------|
| Line 27 `[MEASURED: machine spec, core count.]` | Legion CPU model, core count, RAM from `minikube node` or lscpu |
| Line 105 `[MEASURED: one paragraph on where each run lands in the hero chart.]` | 2-4 sentences describing hero.png: which run finished fastest, which was slowest, KEDA vs fixed gap |
| Line 107 `[MEASURED: results table from scripts.report]` | Paste content of `results.md` directly |
| Line 111 `[MEASURED: confirm OCR CPU time is ~same across runs.]` | Pull ocr_cpu_s or equivalent from results.md |
| Lines 115-116 `[MEASURED: scale-up ramp ...]` | Describe replicas.png: when OCR peaks, how fast scale-up was, when it scales back down |
| Lines 119-122 `[MEASURED: render backlog / control / keda gap]` | Describe queues.png + keda-1 vs keda-0 difference |

#### 2c. Title and GitHub link

| Placeholder | What to do |
|-------------|-----------|
| `[TITLE: pick from the main finding]` | Pick after reading results. Should name the measured result, not the technique. E.g. "How KEDA cut document pipeline turnaround by Nx with N workers" |
| `[GitHub link]` on line 9 | Add actual repo URL once pushed |

---

### TASK 3: Finalize BLOG.md

- Word-count each section against budgets (in BLOG.md comments). Trim if over.
- Proofread for invented/predicted numbers — there must be none.
- The 32k batch ceiling must be flagged as "this experiment's setting, not a recommendation" wherever it appears.
- Part 2 scope (Airflow, retries, DLQ, idempotency, Prometheus, cost) must NOT appear in Part 1.

---

## Critical rules (enforce always)

1. **Never invent numbers.** Every figure in BLOG.md comes from `runs/` via report/plot.
2. **Do not run experiments unless explicitly asked.** User confirms before any long job.
3. LLM limits = token caps (TPM, `LLM_MAX_INPUT_TOKENS`), not call counts.
4. 32k batch ceiling = this experiment's setting. Say so wherever it appears.
5. Part 2 scope stays out of Part 1.
6. LLM stage has no ScaledObject on purpose — more replicas can't raise a shared rate limit.

---

## Known gotchas

1. **Lost doc on reset:** If a pod is mid-BRPOP when Redis is reset, that doc is silently lost.
   Collector hangs at (total-1)/total for up to 1 hour. Fix: minikube restart before each run.
2. **ready() in run_k8s.sh:** Fixed 2026-10-19. Uses `${r:-0}` not `sed 's/^$/0/'`.
3. **Port collision:** run_local uses 6379, k8s uses 16379. Don't run both at once.
4. **Image staleness:** `keda-docs:dev` uses `imagePullPolicy: Never`. Rebuild + `minikube image load` after any code change, or pods run stale code.
5. **Clock skew:** Use docker driver only. VM driver breaks turnaround timing.
6. **Scale-down timeout:** KEDA runs wait up to 10 min for OCR/render to settle at minReplicaCount. With a clean minikube start, OCR/render start at 0 and settle immediately.
7. **Collector stop condition:** stops when all docs done AND t >= max(min_duration=900s, done_at+120s). Hard timeout at 3600s. If 1 doc is lost, it will hang until timeout.

---

## Commands reference

```bash
# Setup (Legion, one-time)
bash scripts/minikube_setup.sh

# Run all experiments (Legion, ~2.5 hours)
bash scripts/run_all.sh

# Run single experiment
bash scripts/run_k8s.sh fixed-2   # or fixed-15, keda-0, keda-1

# Checks
.venv/bin/python -m scripts.checks run --run-dir runs/fixed-15 --out checks/fixed-15.txt
.venv/bin/python -m scripts.checks scaledown --run-dir runs/keda-0 --min-replicas 0 --out checks/keda-0-scaledown.txt
.venv/bin/python -m scripts.checks scaledown --run-dir runs/keda-1 --min-replicas 1 --out checks/keda-1-scaledown.txt

# Report + figures
.venv/bin/python -m scripts.report runs/fixed-2 runs/fixed-15 runs/keda-0 runs/keda-1 --out results.md
.venv/bin/python -m scripts.plot   runs/fixed-2 runs/fixed-15 runs/keda-0 runs/keda-1 --out figures/

# Cluster status
~/.local/bin/minikube status
kubectl get deploy,scaledobject,hpa -n keda-demo
```

---

## Appendix A: Exact Code Excerpts & Diagram for BLOG.md

Smaller models must not invent or re-write these snippets; paste directly.

### 1. Architecture Diagram (`figures/architecture.png`)
Target in BLOG.md: Line 34 `<!-- TODO: export the architecture diagram from the brief -->`
If `../keda-blog-brief.md` is inaccessible or does not contain an image file, generate `figures/architecture.png` using matplotlib/PIL or graphviz. The pipeline architecture is:
```text
Producer (Host) ──[3x1000 docs]──> Redis (ocr_queue)
                                        │
                         ┌──────────────┴──────────────┐
                         ▼                             ▼
                 [KEDA ScaledObject]             [OCR Workers]
                 (min 0/1, max 15)          (CPU-bound: 0.05s/KB)
                         │                             │
                         └───────> scales pods <───────┘
                                                       │
                                                       ▼
                                            Redis (llm_queue)
                                                       │
                                                       ▼
                                                 [LLM Workers]
                                           (Shared Token Limiter)
                                           (Batched to 32k tokens)
                                           (Fixed pool, no KEDA)
                                                       │
                                                       ▼
                                           Redis (render_queue)
                                        │
                         ┌──────────────┴──────────────┐
                         ▼                             ▼
                 [KEDA ScaledObject]            [Render Workers]
                 (min 0/1, max 15)           (I/O-bound: 0.5s sleep)
                         │                             │
                         └───────> scales pods <───────┘
                                                       │
                                                       ▼
                                                Redis (results)
                                                       ▲
Collector (Host) ──────[Samples / sec]─────────────────┘
```

### 2. Worker sync loop excerpt
Target in BLOG.md: Line 60 `<!-- TODO: excerpt the sync loop from src/worker.py: BRPOP -> process -> LPUSH -->`
Exact drop-in snippet (from `src/worker.py`):
```python
while not stopping:
    item = r.brpop(in_queue, timeout=1)
    if item is None:
        continue
    doc = json.loads(item[1])
    start = now()
    doc.update(process(doc))
    end = now()
    doc.update({f"{stage}_start": start, f"{stage}_end": end, f"{stage}_worker": WORKER_ID})
    if stage == "render":
        doc["completed_at"] = end
    r.lpush(out_queue, json.dumps(doc))
```

### 3. ScaledObject YAML excerpt & Line-by-Line Breakdown
Target in BLOG.md: Line 64 `<!-- TODO: excerpt k8s/keda/scaledobjects.yaml and explain each field -->`
Exact drop-in snippet (from `k8s/keda/scaledobjects.yaml`):
```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: ocr
spec:
  scaleTargetRef:
    name: ocr
  pollingInterval: 5
  cooldownPeriod: 30
  minReplicaCount: 0  # the keda-1 overlay sets 1
  maxReplicaCount: 15
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleUp:
          stabilizationWindowSeconds: 0
          selectPolicy: Max
          policies:
            - {type: Pods, value: 15, periodSeconds: 15}
        scaleDown:
          stabilizationWindowSeconds: 30
          selectPolicy: Max
          policies:
            - {type: Percent, value: 100, periodSeconds: 15}
  triggers:
    - type: redis
      metadata:
        address: redis.keda-demo.svc.cluster.local:6379
        listName: ocr_queue
        listLength: "50"
        activationListLength: "0"
```

Field-by-field commentary to accompany the snippet:
- `scaleTargetRef.name: ocr`: Points KEDA to the Kubernetes Deployment (`ocr`) whose replicas it will manage.
- `pollingInterval: 5`: How frequently (in seconds) KEDA queries Redis queue depth when scaling from 0 to 1.
- `cooldownPeriod: 30`: Time in seconds KEDA waits after the queue is completely empty before scaling the last replica down to 0.
- `minReplicaCount: 0` / `maxReplicaCount: 15`: Replicas scale down to 0 during quiet periods, and cap at 15 during bursts. (In `keda-1`, `minReplicaCount` is set to 1 to measure the cold-start cost).
- `behavior.scaleUp`: `stabilizationWindowSeconds: 0` allows immediate scaling reaction with zero stabilization delay. The policy allows adding up to 15 pods in a 15-second window so bursts are met immediately.
- `behavior.scaleDown`: `stabilizationWindowSeconds: 30` overrides the Kubernetes HPA default of 300s (5 minutes). A 300s delay would hold idle pods across our 300s burst intervals, ruining the efficiency test.
- `triggers`: Uses the native Redis list scaler. `listLength: "50"` tells the HPA the target ratio: 1 replica per 50 queued messages (e.g. 500 items targets 10 replicas). `activationListLength: "0"` triggers the 0-to-1 transition the moment a single document arrives.

---

## Appendix B: Detailed Recipe for the 6 `[MEASURED: ...]` Blocks in BLOG.md

Do NOT write these until Task 1 completes and `results.md` + `figures/` exist.

### Block 1: Machine Spec (Line 27)
- Target: `[MEASURED: machine spec, core count.]`
- Source: Run `lscpu` or `minikube node` on the Legion laptop.
- Format: `Lenovo Legion 5i Pro (Intel Core i7-XXXXXH, N cores / M threads, P GB RAM, minikube docker driver with 8 GB RAM allocated).`

### Block 2: Hero Chart Analysis (Line 105)
- Target: `[MEASURED: one paragraph on where each run lands in the hero chart.]`
- Source: `figures/hero.png` and `results.md` turnaround & replica-seconds.
- Structure:
  1. State the baseline fixed pools: `fixed-2` turnaround time vs `fixed-15` turnaround time.
  2. State the capacity cost: `fixed-15` consumed X replica-seconds compared to Y for `fixed-2`.
  3. Compare KEDA: `keda-0` achieved turnaround of X seconds (close to `fixed-15`) while consuming only Y replica-seconds (Z% reduction compared to `fixed-15`).
  4. Compare `keda-1` to `keda-0`: Note the delta in turnaround and replica-seconds.

### Block 3: Results Table (Line 107)
- Target: `[MEASURED: results table from python -m scripts.report ... --out results.md.]`
- Source: Paste the full markdown table generated in `results.md`.

### Block 4: OCR CPU Time Confirmation (Line 111)
- Target: `[MEASURED: confirm OCR worker CPU time is ~same across runs.]`
- Source: `ocr_cpu_s` column in `results.md`.
- Content: Confirm that total OCR CPU burn across all 4 runs was within ~1-2% (e.g., ~1,500s total work across 3,000 docs). This proves that the speedup is strictly from parallelism, not workload variance.

### Block 5: Scale-up / Scale-down Ramp & Core Saturation (Lines 115-116)
- Target: `[MEASURED: the scale-up ramp, the scale-down after each burst, and where OCR throughput flattens on this machine's cores.]`
- Source: `figures/replicas.png` and `figures/completions.png`.
- Content: Describe how fast replicas climbed from 0 to 15 after each burst (e.g. within ~30s), how long they stayed at 15, and how the 30s scale-down stabilization window dropped replicas back to min replicas. Call out the actual point where completion rate flattened relative to available physical cores.

### Block 6: Queue Backlog & Control Comparison (Lines 119-122)
- Target: `[MEASURED: the render backlog under listLength: 50, and the LLM queue staying near empty.]` and `[MEASURED: the control. How much of KEDA's turnaround gap comes from scale to zero.]`
- Source: `figures/queues.png` and `results.md` differences between `keda-0` and `keda-1`.
- Content: Detail how the LLM queue stayed near empty because batching processed docs rapidly, whereas `render_queue` peaked and drained smoothly under `listLength: 50`. State the exact difference in turnaround between `keda-0` (cold start from 0) and `keda-1` (warm pool of 1), quantifying the exact scale-to-zero latency penalty.

---

## Appendix C: Troubleshooting & Recovery Runbook

### If a run appears stuck:
1. Check collector output:
   ```bash
   tail -n 20 logs/collector.log
   ```
2. Check if stuck at 2999/3000 documents:
   - Cause: A pod died mid-BRPOP during reset or an earlier run, dropping 1 document.
   - Action: Kill the running script (`Ctrl+C` or `kill`). Restart minikube cleanly (`minikube stop && minikube start`).
   - Re-run only the missing experiment: `bash scripts/run_k8s.sh <run_name>`.

### If minikube fails to start:
1. Ensure Docker is running in WSL2:
   ```bash
   sudo service docker status || sudo service docker start
   ```
2. Check minikube status:
   ```bash
   ~/.local/bin/minikube status
   ```

### If KEDA settle times out:
- Ensure the bug fix in `scripts/run_k8s.sh` (using `${r:-0}`) is present.
- A clean restart of minikube eliminates orphaned pods that cause non-zero replica states before the run.

---

## Appendix D: Blog Post Constraints & Editorial Checklist

- **Target Word Count:** ~2,000 words total across all sections.
  - Fixed pools make you choose: ~200 words
  - The pipeline: ~250 words
  - Why KEDA: ~150 words
  - The code: ~450 words
  - The LLM stage: ~150 words
  - Results: ~400 words
  - Gotchas: ~250 words
  - Run it yourself: ~100 words
  - What's next: ~50 words
- **Mandatory Caveat:** The 32k batch ceiling must be explicitly cited as "this experiment's setting, not a recommendation" wherever mentioned.
- **Strict Scope Boundary:** Do NOT mention Part 2 implementation details (Airflow DAGs, DLQ implementation, Celery, Prometheus metrics) in the body of Part 1. They belong only in the final "What's next" section.
- **Rule of Truth:** No hallucinated or predicted metrics. If a number is cited in the blog, it must be traceable to `results.md` or `timeseries.csv`.
