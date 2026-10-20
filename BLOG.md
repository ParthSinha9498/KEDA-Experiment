<!--
Skeleton for Part 1. Every [MEASURED: ...] is filled from runs/ after the
experiment; nothing here is a prediction. Word budgets follow the brief.
Pick the title after the results, from the main finding.
-->

# [TITLE: pick from the main finding]

*Part 1 of 2. Code, manifests and raw run data: [GitHub link].*

## Fixed pools make you choose (~200 words)

Document preprocessing pipelines usually run a fixed number of workers. Size the pool for peak load
and you pay for idle workers most of the day. Size it for average load and bursts back up.

The stages don't scale the same way, either. In a pipeline that OCRs a document, extracts entities
with an LLM and renders the result:

- **OCR** is CPU-bound. More workers help, until the machine runs out of cores.
- **LLM extraction** is rate-limited. The API caps requests per minute, tokens per minute and calls
  in flight, and every worker shares those caps.
- **Rendering** is I/O-bound. Workers mostly wait.

This post wires KEDA into that pipeline, measures elastic capacity against pools provisioned for
average and for peak load, and shows how to handle the one stage autoscaling can't help.

Everything runs on one laptop with mocked workloads. [MEASURED: machine spec, core count.] The
result is a controlled local demonstration of KEDA's scaling behavior, not a production capacity
benchmark.

## The pipeline (~250 words)

![Pipeline architecture](figures/architecture.png)
<!-- TODO: export the architecture diagram from the brief -->

Each stage pulls from a Redis list and pushes to the next. KEDA watches queue depth and sets the
OCR and render replica counts. The LLM stage has no ScaledObject: its throughput is set by API
rate limits shared through Redis, not by how many replicas it runs. One image serves every stage,
with a `STAGE` env var choosing which.

What's simulated, so nothing is mistaken for a real benchmark:

| Stage | Mock |
| --- | --- |
| OCR | Burns 0.05s of CPU per KB of document (0.25–0.75s per doc) |
| LLM | Tokens metered per call; latency from input and output tokens; RPM, TPM and concurrency limits |
| Render | 0.5s wait per doc |

## Why KEDA (~150 words)

An autoscaler configured on CPU or memory reacts to load indirectly: work has to arrive, start, and
push utilization up before anything scales. KEDA scales on the pending work itself, here the length
of a Redis list. Redis lists keep the setup small: KEDA has a native scaler for them, and you can
inspect a queue with one `LLEN`.

## The code (~450 words)

### One worker, three stages

<!-- TODO: excerpt the sync loop from src/worker.py: BRPOP -> process -> LPUSH -->

### The ScaledObject, line by line

<!-- TODO: excerpt k8s/keda/scaledobjects.yaml and explain each field -->

Points to make:

- `listLength: 50` is a target of about 50 queued items per replica, not a formula. KEDA supplies
  the metric; the Kubernetes autoscaler decides the replica count.
- `pollingInterval` drives only the step from 0 to 1. From 1 to N, the autoscaler's own sync period
  and policies apply.
- The default scale-up policy permits +4 pods or a doubling per 15s period. The `scaleUp` block
  permits up to 15 per period; it doesn't guarantee it.
- Scale-down is two mechanisms: the autoscaler's stabilization window takes N toward the minimum
  (300s by default, 30s here), and `cooldownPeriod` takes the last step to 0.

### The LLM stage: batch to the quota, stream onward (~150 words)

Three ideas, no benchmark: the behavior follows from the limits.

1. **Keep the limits in Redis, not in each pod.** A per-pod limiter multiplies with replicas.
2. **Batch to the quota.** Pack docs into a call up to a token ceiling (32k input tokens in this
   experiment; derive yours from the model's limits), with a 2s flush so the last docs don't wait.
3. **Stream each batch onward** as soon as its call returns.

Worked example, for APIs and prompts that support several documents per request: a 10KB doc is
about 2,650 tokens. Sent alone, each call also pays the 500-token system prompt, about 3,150
tokens per doc. In a 12-doc batch, the prompt is shared: about 2,690 per doc. Under a 60
requests-per-minute cap, batching 12 per call carries 12x the docs. Under a tokens-per-minute cap,
it saves about 15%. Batching beats request caps, not token caps.

## Results (~400 words)

Four runs, all on the same minikube node. Load: 3 bursts of 1000 docs, at t=0, 300s and 600s.

| Run | What it represents |
| --- | --- |
| fixed-2 | Capacity provisioned for average load |
| fixed-15 | Capacity provisioned for peak load |
| KEDA 0→15 | Elastic capacity with scale to zero |
| KEDA 1→15 (control) | The same, minimum 1, to isolate the cost of scale to zero |

![Turnaround vs. reserved capacity](figures/hero.png)

[MEASURED: one paragraph on where each run lands in the hero chart.]

[MEASURED: results table from `python -m scripts.report ... --out results.md`.]

How to read the capacity numbers: replica-seconds count capacity reserved, not compute used. An
idle pod counts the same as a busy one. Measured OCR worker CPU time shows the work done, which
should be about the same in every run. [MEASURED: confirm.]

![Ready replicas over time](figures/replicas.png)

[MEASURED: the scale-up ramp, the scale-down after each burst, and where OCR throughput flattens
on this machine's cores.]

![Queue depth over time](figures/queues.png)

[MEASURED: the render backlog under `listLength: 50`, and the LLM queue staying near empty.]

[MEASURED: the control. How much of KEDA's turnaround gap comes from scale to zero.]

## Gotchas (~250 words)

1. **Scaling lag has several sources.** Polling (0 to 1), the autoscaler's sync period, tolerance and
   scale-up policy (1 to N), then scheduling and readiness. [MEASURED: cold-start cost from the
   control.]
2. **Rate limits are invisible to the scaler.** KEDA knows nothing about downstream limits. Put
   them in Redis so they hold across replicas.
3. **Batching beats request caps, not token caps.** And each doc waits longer for its batch.
4. **Replicas aren't cores.** On one node, CPU-bound throughput flattens once the work exceeds the
   CPU. A CPU limit on the pod would throttle it further; a request only reserves.
5. **Work in progress on termination.** A pod can be terminated mid-document. Without
   acknowledge-and-requeue, that work is lost. Part 2 fixes it.

Also worth knowing: `cooldownPeriod` trades idle pods against cold starts; `listLength` sets how much
backlog you tolerate; Redis here is in memory only.

## Run it yourself (~100 words)

```bash
scripts/minikube_setup.sh          # minikube, KEDA, worker image
scripts/run_k8s.sh fixed-2         # then fixed-15, keda-0, keda-1
python -m scripts.report runs/fixed-2 runs/fixed-15 runs/keda-0 runs/keda-1
python -m scripts.plot runs/fixed-2 runs/fixed-15 runs/keda-0 runs/keda-1
```

The README has the full steps, the design checks, and the raw data for every number above.

## What's next (~50 words)

Part 2 hardens the pipeline: retries with backoff, a dead-letter queue, idempotent processing,
acknowledge-and-requeue on termination, and orchestration with Airflow.
