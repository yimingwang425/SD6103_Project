#!/usr/bin/env python3
"""Task 3 - plot database size against average runtime for each query.

Reads results/benchmark_summary.csv (written by run_benchmark.py) and writes
results/task3_benchmark.png.

Usage:
  python3 scripts/queries/plot_task3.py
"""

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

RESULT_DIR = Path(__file__).resolve().parents[2] / "results"

COLORS = {"Q1": "#2a78d6", "Q2": "#eb6834", "Q3": "#1baf7a",
          "Q4": "#eda100", "Q5": "#e87ba4", "Q6": "#008300"}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def main() -> None:
    points = defaultdict(list)
    with (RESULT_DIR / "benchmark_summary.csv").open() as handle:
        for row in csv.DictReader(handle):
            points[row["query"]].append((int(row["total_transactions"]),
                                         float(row["avg_time_ms"])))

    fig, ax = plt.subplots(figsize=(6.26, 3.45), dpi=300)
    for query, color in COLORS.items():
        x, y = zip(*sorted(points[query]))
        ax.plot(x, y, color=color, lw=1.5, marker="o", ms=4.5,
                mec="white", mew=0.8, label=query, zorder=3)

    # label the slowest query at the full database size
    top = max(COLORS, key=lambda q: sorted(points[q])[-1][1])
    x, y = sorted(points[top])[-1]
    ax.annotate(f"{top}  {y:,.0f} ms", (x, y), xytext=(0, 6),
                textcoords="offset points", ha="right", fontsize=7.5, color=INK)

    ax.set_xlabel("Total resale transactions", fontsize=8.5, color=MUTED)
    ax.set_ylabel("Average runtime over 5 runs (ms)", fontsize=8.5, color=MUTED)
    ax.set_xticks(range(300000, 1000001, 100000))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1000:.0f}K"))
    ax.set_xlim(240000, 1000000)
    ax.set_ylim(bottom=0)
    ax.tick_params(labelsize=8, colors=MUTED, length=0)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.legend(frameon=False, fontsize=8, ncol=6, loc="lower center",
              bbox_to_anchor=(0.5, 1.0), handlelength=1.6, columnspacing=1.4,
              labelcolor=INK)
    fig.tight_layout()
    fig.savefig(RESULT_DIR / "task3_benchmark.png", dpi=300, facecolor="white")
    print(f"plot -> {RESULT_DIR / 'task3_benchmark.png'}")


if __name__ == "__main__":
    main()
