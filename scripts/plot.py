"""Draw the blog's figures from finished runs.

    python -m scripts.plot runs/fixed-2 runs/fixed-15 runs/keda-0 runs/keda-1 --out figures/

hero.png         turnaround vs. sampled replica-seconds, one point per run
replicas.png     ready OCR and render replicas over time, one panel per run
queues.png       queue depth over time, one panel per run
completions.png  completions per second, one panel per run

Static PNGs for a blog post; the results table (scripts/report.py) is the
table view of the hero chart.
"""

import argparse
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from scripts.report import Run, load_run, summarize  # noqa: E402

# Light-mode chart chrome and categorical slots 1-3 from the reference palette.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
STAGE_COLORS = {"ocr": "#2a78d6", "llm": "#eb6834", "render": "#1baf7a"}  # fixed per stage in every figure
STAGE_LABELS = {"ocr": "OCR", "llm": "LLM", "render": "Render"}
LINE_W = 1.5
DPI = 200


def style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASELINE)
    ax.tick_params(colors=MUTED, labelcolor=INK_2, labelsize=8)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def figure(nrows: int = 1, height: float = 4.5, **kw):
    fig, axes = plt.subplots(nrows, 1, figsize=(8, height), facecolor=SURFACE, squeeze=False, **kw)
    axes = [a for row in axes for a in row]
    for ax in axes:
        style(ax)
    return fig, axes


def mark_bursts(ax, run: Run) -> None:
    for b in range(run.bursts):
        ax.axvline(b * run.interval, color=BASELINE, linewidth=0.8, linestyle=(0, (3, 3)), zorder=0)


def panel_title(ax, text: str) -> None:
    ax.set_title(text, loc="left", fontsize=9, color=INK, fontweight="bold", pad=4)


def save(fig, out: Path, name: str, title: str, caption: str) -> None:
    fig.suptitle(title, x=0.02, ha="left", fontsize=12, color=INK, fontweight="bold")
    fig.text(0.02, 0.005, caption, fontsize=7.5, color=INK_2, ha="left", va="bottom")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(out / name, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)


def hero(runs: list[Run], out: Path) -> None:
    rows, end = summarize(runs)
    fig, (ax,) = figure(height=4.8)
    for row in rows:
        x, y = row["replica_seconds"], row["turnaround_mean_s"]
        lo, hi = min(row["turnaround_per_burst_s"]), max(row["turnaround_per_burst_s"])
        ax.errorbar(x, y, yerr=[[y - lo], [hi - y]], fmt="o", color=INK_2, ecolor=BASELINE,
                    elinewidth=1.2, capsize=3, markersize=8, markeredgecolor=SURFACE, markeredgewidth=1.5)
        ax.annotate(row["run"], (x, y), xytext=(8, 6), textcoords="offset points", fontsize=9, color=INK)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Sampled replica-seconds, OCR + render (capacity reserved)", fontsize=9, color=INK_2)
    ax.set_ylabel("Turnaround per burst (s)", fontsize=9, color=INK_2)
    save(fig, out, "hero.png",
         "Turnaround vs. reserved capacity",
         f"Point = mean over bursts; bar = fastest to slowest burst. Replica-seconds over t=0-{end:.0f}s "
         "for every run. Capacity reserved, not compute used.")


def per_run_panels(runs: list[Run], out: Path, name: str, title: str, caption: str, draw) -> None:
    fig, axes = figure(nrows=len(runs), height=1.9 * len(runs) + 0.8, sharex=True, sharey=True)
    for ax, run in zip(axes, runs):
        mark_bursts(ax, run)
        draw(ax, run)
        panel_title(ax, run.name)
        ax.set_ylim(bottom=0)
    axes[-1].set_xlabel("Time since the first burst (s)", fontsize=9, color=INK_2)
    handles, labels = axes[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="upper right", ncol=len(handles), frameon=False, fontsize=8,
                   labelcolor=INK_2, bbox_to_anchor=(0.98, 0.985))
    save(fig, out, name, title, caption)


def replicas(runs: list[Run], out: Path) -> None:
    def draw(ax, run):
        t = [row["t"] for row in run.series]
        for stage in ("ocr", "render"):
            ax.step(t, [row[f"{stage}_replicas"] for row in run.series], where="post",
                    color=STAGE_COLORS[stage], linewidth=LINE_W, label=STAGE_LABELS[stage])
        ax.set_ylabel("Ready replicas", fontsize=8, color=INK_2)

    per_run_panels(runs, out, "replicas.png", "Ready replicas over time",
                   "Dashed lines mark burst starts. The LLM stage is fixed at 1 replica in every run.", draw)


def queues(runs: list[Run], out: Path) -> None:
    def draw(ax, run):
        t = [row["t"] for row in run.series]
        for stage, queue in (("ocr", "ocr_queue"), ("llm", "llm_queue"), ("render", "render_queue")):
            ax.plot(t, [row[queue] for row in run.series], color=STAGE_COLORS[stage],
                    linewidth=LINE_W, label=queue)
        ax.set_ylabel("Docs queued", fontsize=8, color=INK_2)

    per_run_panels(runs, out, "queues.png", "Queue depth over time",
                   "Dashed lines mark burst starts. Sampled every second.", draw)


def completions(runs: list[Run], out: Path, bin_s: int = 5) -> None:
    def draw(ax, run):
        counts = Counter(int((d["completed_at"] - run.start) // bin_s) for d in run.docs)
        bins = range(0, max(counts) + 1)
        ax.bar([b * bin_s for b in bins], [counts.get(b, 0) / bin_s for b in bins], width=bin_s * 0.9,
               align="edge", color=INK_2, linewidth=0)
        ax.set_ylabel("Docs/s", fontsize=8, color=INK_2)

    per_run_panels(runs, out, "completions.png", "Completions per second",
                   f"Docs reaching results, in {bin_s}s bins. Dashed lines mark burst starts.", draw)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("runs", nargs="+", help="run directories written by the collector")
    p.add_argument("--out", default="figures")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runs = [load_run(path) for path in args.runs]
    hero(runs, out)
    replicas(runs, out)
    queues(runs, out)
    completions(runs, out)
    print(f"wrote {', '.join(sorted(p.name for p in out.glob('*.png')))} to {out}")


if __name__ == "__main__":
    main()
