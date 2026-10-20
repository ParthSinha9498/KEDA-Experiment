"""Turn finished runs into the results table.

    python -m scripts.report runs/fixed-2 runs/fixed-15 runs/keda-0 runs/keda-1 --out results.md

Metric definitions follow the brief:
- Turnaround per burst: the slowest doc in the burst, completed_at - submitted_at.
- Sampled replica-seconds: ready OCR + render replicas, integrated over one
  window shared by every run, from t=0 to the later of the injection window and
  the slowest run's completion. A run's last sample is held to the window's end
  (a fixed pool keeps running; a KEDA run stays where it settled).
- Measured OCR worker CPU time: the sum of each doc's ocr_cpu_s.
"""

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

CAPACITY_STAGES = ("ocr", "render")


@dataclass
class Run:
    name: str
    meta: dict
    docs: list[dict]
    series: list[dict[str, float]]

    @property
    def start(self) -> float:
        return self.meta["run"]["start"]

    @property
    def bursts(self) -> int:
        return int(self.meta["run"]["bursts"])

    @property
    def interval(self) -> float:
        return self.meta["run"]["interval"]

    @property
    def injection_window(self) -> float:
        return self.bursts * self.interval


def load_run(path: str | Path) -> Run:
    path = Path(path)
    meta = json.loads((path / "meta.json").read_text())
    docs = [json.loads(line) for line in (path / "results.jsonl").read_text().splitlines() if line]
    with open(path / "timeseries.csv") as f:
        series = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(f)]
    return Run(path.name, meta, docs, series)


def completion_s(run: Run) -> float:
    return max(d["completed_at"] for d in run.docs) - run.start


def shared_window_end(runs: list[Run]) -> float:
    return max(max(r.injection_window for r in runs), max(completion_s(r) for r in runs))


def replica_seconds(run: Run, window_end: float, stages=CAPACITY_STAGES) -> float:
    """Integrate the sampled replica count as a step function over [0, window_end]."""
    total = 0.0
    rows = run.series
    for i, row in enumerate(rows):
        t_next = rows[i + 1]["t"] if i + 1 < len(rows) else window_end
        lo, hi = max(row["t"], 0.0), min(t_next, window_end)
        if hi > lo:
            total += (hi - lo) * sum(row[f"{s}_replicas"] for s in stages)
    return total


def held_s(run: Run, window_end: float) -> float:
    """How much of the window is covered by holding the last sample."""
    return max(0.0, window_end - run.series[-1]["t"])


def turnaround_per_burst(run: Run) -> list[float]:
    worst: dict[int, float] = {}
    for d in run.docs:
        worst[d["burst"]] = max(worst.get(d["burst"], 0.0), d["completed_at"] - d["submitted_at"])
    return [worst[b] for b in sorted(worst)]


def ocr_cpu_s(run: Run) -> float:
    return sum(d["ocr_cpu_s"] for d in run.docs)


def peak_replicas(run: Run, stage: str) -> int:
    return int(max(row[f"{stage}_replicas"] for row in run.series))


def summarize(runs: list[Run]) -> tuple[list[dict], float]:
    configs = {(r.bursts, r.interval, r.meta["run"]["burst_size"]) for r in runs}
    if len(configs) > 1:
        raise SystemExit(f"runs used different loads, so they can't share a window: {configs}")
    end = shared_window_end(runs)
    rows = []
    for r in runs:
        per_burst = turnaround_per_burst(r)
        rows.append({
            "run": r.name,
            "turnaround_mean_s": mean(per_burst),
            "turnaround_per_burst_s": per_burst,
            "replica_seconds": replica_seconds(r, end),
            "ocr_cpu_s": ocr_cpu_s(r),
            "peak_ocr": peak_replicas(r, "ocr"),
            "peak_render": peak_replicas(r, "render"),
            "completion_s": completion_s(r),
            "held_s": held_s(r, end),
        })
    return rows, end


def markdown(rows: list[dict], end: float) -> str:
    lines = [
        "| Run | Turnaround per burst (s) | Sampled replica-seconds | Measured OCR worker CPU time (s) "
        "| Peak replicas (OCR / render) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        bursts = " / ".join(f"{t:.0f}" for t in row["turnaround_per_burst_s"])
        lines.append(
            f"| {row['run']} | {bursts} (mean {row['turnaround_mean_s']:.0f}) | {row['replica_seconds']:,.0f} "
            f"| {row['ocr_cpu_s']:,.0f} | {row['peak_ocr']} / {row['peak_render']} |"
        )
    lines += [
        "",
        f"Replica-seconds cover t=0 to {end:.0f}s for every run. Last sample held to the window's end: "
        + ", ".join(f"{row['run']} {row['held_s']:.0f}s" for row in rows) + ".",
    ]
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("runs", nargs="+", help="run directories written by the collector")
    p.add_argument("--out", help="write the markdown table here")
    args = p.parse_args()

    rows, end = summarize([load_run(path) for path in args.runs])
    table = markdown(rows, end)
    print(table)
    if args.out:
        Path(args.out).write_text(table + "\n")


if __name__ == "__main__":
    main()
