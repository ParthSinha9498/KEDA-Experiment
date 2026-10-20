# Handover - KEDA document-processing blog

As of 2026-10-19 (updated after smoke run). Read with `CLAUDE.md`, `README.md`, `../keda-blog-brief.md`, `BLOG.md`.

## Current state
1. Code complete for Part 1: workers, Redis-backed limiter, producer, collector, checks, report,
   plot, Dockerfile/compose, k8s base + KEDA ScaledObjects + 4 overlays, minikube scripts.
2. Phase-1 design checks pass (`checks/unit.txt`, `checks/run.txt`): OCR and LLM mocks match their
   models (<=0.2% deviation), LLM concurrency limit reached and never exceeded, LLM margin 2.7x over
   OCR (need >=2x), busiest render worker 59% (need <80%). Two checks SKIP until Phase 2 data.
3. Cluster is running (minikube docker driver, KEDA 2.21.0); tools in `~/.local/bin`.
   Check `minikube status` before assuming it is still up between sessions.
4. Smoke run (keda-0) completed 2026-10-19. 2999/3000 docs processed; one doc lost because a
   previous interrupted run left a worker with a BRPOP-ed item that was abandoned on reset
   (no ack-and-requeue; Part 2 fixes this). `runs/keda-0/timeseries.csv` exists (~1460 rows,
   ~24 min). `results.jsonl` and `meta.json` were NOT written (collector killed before clean exit).
   The timeseries data is sufficient for scaling plots.
5. Bug fixed in `scripts/run_k8s.sh`: `ready()` used `sed 's/^$/0/'` which fails when
   `kubectl jsonpath` returns no trailing newline (empty string, not empty line). Fixed to
   `${r:-0}` shell default. This caused the first two run attempts to time out at the settle step.
6. `BLOG.md` is an outline + draft with section word budgets; title and Results are placeholders.
7. Decision: run the 4 full experiments on a **Lenovo Legion 5i Pro via WSL2** (Ubuntu), not this
   machine. Reason: the i7-1165G7 (4 cores/8 threads, 28W) flattens OCR throughput too early;
   the Legion's H-series CPU (14-16 cores, 45W+) will show a longer scale-up ramp and a higher
   saturation ceiling, making the KEDA speedup visible and convincing in the blog charts.
   WSL2 chosen over native Ubuntu because that is what the Legion runs. Docker driver for minikube
   works on WSL2; keep the docker driver to avoid clock skew (Gotcha 5).
   Setup: `git clone` the repo on the Legion, run `scripts/minikube_setup.sh`, then `scripts/run_all.sh`.

## Decisions and why
1. LLM rate-limit experiment dropped: its result follows directly from the limits, so measuring
   it proves nothing. The LLM stage is presented as a pattern (batch to the quota, stream onward).
2. One experiment: fixed-2, fixed-15, keda-0, keda-1 on minikube. All four on minikube so fixed
   and elastic share one environment; compose is for development only.
3. keda-1 is a control identical to keda-0 except minReplicaCount 1, isolating scale-to-zero cost.
4. LLM limits are token caps (TPM, max input tokens), not call counts - that is how real APIs bind.
5. 32k batch ceiling is this experiment's setting, not a recommendation.
6. Limits live in Redis, not per pod: a per-pod limiter multiplies with replica count.
7. No LLM ScaledObject: more replicas can't raise a shared rate limit.
8. HPA scale-down window 30s (default 300s would hold near-peak replicas through each 300s gap);
   scale-up allows +15 pods/15s.
9. Docker driver for minikube: one clock for `submitted_at` (host) and `completed_at` (pod).
10. Report integrates replica-seconds over a shared window across runs, so runs are comparable.
11. The brief predicts no results; no numbers are written until measured.
12. Experiments run on Legion 5i Pro (WSL2) not the dev machine: more cores give a clearer
    speedup story. The blog already notes the machine spec is measured, not assumed.

## What remains (in order)
1. Run the 4 full experiments (smoke run done; re-run keda-0 cleanly as part of the set):
   `scripts/run_k8s.sh fixed-2`, `fixed-15`, `keda-0`, `keda-1` — 17+ min each.
   Start from a clean minikube state between runs to avoid leftover pod/doc loss (see Gotchas 8).
2. Remaining checks: `checks run` on fixed-15, `checks scaledown` on keda-0 and keda-1.
3. `scripts.report` -> `results.md`, `scripts.plot` -> `figures/`.
4. Fill BLOG.md: architecture diagram, worker.py excerpt, ScaledObjects YAML excerpt (3 TODOs),
   all `[MEASURED: ...]` blocks, Results section, final title, GitHub link.
5. Finalize BLOG.md (~2000 words) from measured numbers only.
6. Open question from the user: will the post get recruiter attention? Answer bluntly when asked:
   it depends on a clear measured result, a clean repo and a sharp title, not on the topic alone.

## Gotchas
1. Port 6379 is shared by `keda-redis` (run_local) and compose Redis - stop one first. k8s
   port-forward uses 16379 to avoid this.
2. `kubectl apply -k` does not remove ScaledObjects from a previous KEDA run; run_k8s.sh deletes
   them first. Do the same if applying overlays by hand.
3. Image is `keda-docs:dev` with `imagePullPolicy: Never`: after code changes, rebuild and
   `minikube image load` it, or pods run stale code.
4. KEDA runs wait up to 10 min for OCR/render to settle at minReplicaCount, then exit 1.
5. A VM minikube driver may break turnaround timing (clock skew); keep the docker driver.
6. `logs/` holds output from local trials; it is not experiment data.
7. Each experiment run is long; don't start one in a session that may be interrupted.
8. One doc can be lost per run if a previous run left a worker mid-BRPOP when Redis was reset.
   To avoid: let each run finish cleanly (or `minikube stop && minikube start`) before the next.
   The collector will hang at (total-1)/total until the 1-hour timeout if this happens.
9. `ready()` in `run_k8s.sh` was fixed (2026-10-19): `sed 's/^$/0/'` did not fire on the no-newline
   output from `kubectl jsonpath`; now uses `${r:-0}`. Without this fix the settle step always
   times out for keda-0 and keda-1.
