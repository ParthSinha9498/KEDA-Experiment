"""Sample queue depths and replica counts every second during a run.

    python -m src.collector --run-dir runs/keda-0 --mode k8s
    python -m src.collector --run-dir runs/dev --mode static --static ocr=8,llm=1,render=15

Start it before the producer: it waits for the producer's run:meta, then
samples until every doc is done and the run has passed --min-duration. It keeps
sampling for --tail seconds after the last doc so KEDA's scale-down is
captured; scripts/report.py holds the last sample beyond that.

Writes to --run-dir:
    timeseries.csv  t (s since the run started), queue depths, ready replicas per stage
    results.jsonl   every finished doc with its per-stage fields
    meta.json       run config, LLM settings, collector settings, recorded environment
"""

import argparse
import csv
import json
import os
import platform
import subprocess
import time
from pathlib import Path

from src.common import (
    LLM_CONFIG,
    LLM_QUEUE,
    OCR_QUEUE,
    RENDER_QUEUE,
    RESULTS,
    RUN_META,
    connect,
    log,
    now,
)

STAGES = ("ocr", "llm", "render")
QUEUES = (OCR_QUEUE, LLM_QUEUE, RENDER_QUEUE, RESULTS)
COLUMNS = ["t", *QUEUES, *(f"{s}_replicas" for s in STAGES)]


def sh(*cmd: str) -> str:
    """Run a command for the environment record; never fail the run over it."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=True).stdout.strip()
    except Exception as e:  # noqa: BLE001 - best effort by design
        return f"unavailable: {e}"


class Replicas:
    """Ready replicas per stage, from Kubernetes, docker compose or fixed values."""

    def __init__(self, mode: str, namespace: str, project: str, static: str):
        self.mode, self.namespace, self.project = mode, namespace, project
        self.static = dict(kv.split("=") for kv in static.split(",")) if static else {}

    def sample(self) -> dict[str, int]:
        if self.mode == "k8s":
            out = subprocess.run(
                ["kubectl", "get", "deploy", "-n", self.namespace, "-o", "json"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout
            ready = {d["metadata"]["name"]: d["status"].get("readyReplicas", 0) for d in json.loads(out)["items"]}
            return {s: ready.get(s, 0) for s in STAGES}
        if self.mode == "compose":
            out = subprocess.run(
                ["docker", "ps", "--filter", f"label=com.docker.compose.project={self.project}",
                 "--filter", "status=running", "--format", '{{.Label "com.docker.compose.service"}}'],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout.split()
            return {s: out.count(s) for s in STAGES}
        return {s: int(self.static.get(s, 0)) for s in STAGES}


def environment(mode: str, namespace: str) -> dict:
    env = {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "cpu_model": next(
            (line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
             if line.startswith("model name")),
            "unknown",
        ) if Path("/proc/cpuinfo").exists() else "unknown",
        "replica_source": mode,
    }
    if mode == "k8s":
        env.update({
            "kubectl_version": sh("kubectl", "version", "-o", "json"),
            "minikube_version": sh("minikube", "version", "--short"),
            "minikube_profile": sh("minikube", "profile", "list", "-o", "json"),
            "keda_operator_image": sh("kubectl", "get", "deploy", "-n", "keda", "keda-operator",
                                      "-o", "jsonpath={.spec.template.spec.containers[0].image}"),
            # Sync period and tolerance flags, if set; defaults apply when absent.
            "controller_manager_command": sh("kubectl", "get", "pod", "-n", "kube-system",
                                             "-l", "component=kube-controller-manager",
                                             "-o", "jsonpath={.items[0].spec.containers[0].command}"),
            "scaledobjects": sh("kubectl", "get", "scaledobject", "-n", namespace, "-o", "json"),
            "hpas": sh("kubectl", "get", "hpa", "-n", namespace, "-o", "json"),
            "deployments": sh("kubectl", "get", "deploy", "-n", namespace, "-o", "json"),
        })
    return env


def wait_for_run(r, not_before: float, timeout: float) -> dict:
    # Ignore a run:meta left over from an earlier run.
    deadline = now() + timeout
    while now() < deadline:
        meta = r.hgetall(RUN_META)
        if meta and float(meta["start"]) >= not_before:
            return meta
        time.sleep(0.2)
    raise SystemExit("no run started; start the producer after the collector")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-dir", required=True)
    p.add_argument("--mode", choices=["k8s", "compose", "static"], required=True)
    p.add_argument("--namespace", default="keda-demo")
    p.add_argument("--compose-project", default="keda")
    p.add_argument("--static", default="", help="fixed replica counts for --mode static, e.g. ocr=8,llm=1,render=15")
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--min-duration", type=float, default=900, help="sample at least this long (the injection window)")
    p.add_argument("--tail", type=float, default=120, help="keep sampling this long after the last doc")
    p.add_argument("--timeout", type=float, default=3600)
    args = p.parse_args()

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    r = connect()
    replicas = Replicas(args.mode, args.namespace, args.compose_project, args.static)

    log("collector", "waiting for the producer")
    meta = wait_for_run(r, not_before=now() - 5, timeout=600)
    t0, total = float(meta["start"]), int(meta["total"])
    log("collector", f"run started: {total} docs; sampling every {args.interval:g}s")

    done_at = None
    reps = {s: 0 for s in STAGES}
    with open(run_dir / "timeseries.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        next_tick = now()
        while True:
            t = now() - t0
            pipe = r.pipeline()
            for q in QUEUES:
                pipe.llen(q)
            depths = pipe.execute()
            try:
                reps = replicas.sample()
            except Exception as e:  # noqa: BLE001 - a flaky kubectl call shouldn't end the run
                log("collector", f"replica sample failed at t={t:.1f}s, repeating the last one: {e}")
            writer.writerow([f"{t:.3f}", *depths, *(reps[s] for s in STAGES)])
            f.flush()

            if done_at is None and depths[-1] >= total:
                done_at = t
                log("collector", f"all {total} docs done at t={t:.1f}s; sampling the tail")
            if done_at is not None and t >= max(args.min_duration, done_at + args.tail):
                break
            if t > args.timeout:
                log("collector", f"timeout at t={t:.0f}s with {depths[-1]}/{total} docs done")
                break
            next_tick += args.interval
            time.sleep(max(0.0, next_tick - now()))

    # Drop the filler text so the run directory stays small enough to commit.
    with open(run_dir / "results.jsonl", "w") as f:
        for i in range(0, r.llen(RESULTS), 1000):
            for item in r.lrange(RESULTS, i, i + 999):
                doc = json.loads(item)
                doc.pop("text", None)
                f.write(json.dumps(doc) + "\n")

    (run_dir / "meta.json").write_text(json.dumps({
        "run": {k: float(v) for k, v in meta.items()},
        "done_at_s": done_at,
        "llm_config": {k: float(v) for k, v in r.hgetall(LLM_CONFIG).items()},
        "collector": vars(args),
        "environment": environment(args.mode, args.namespace),
    }, indent=2))
    log("collector", f"wrote {run_dir}")


if __name__ == "__main__":
    main()
