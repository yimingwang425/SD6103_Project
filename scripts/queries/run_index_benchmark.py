#!/usr/bin/env python3
"""Task 4 - index benchmark.

Measures Q1-Q6 on the full database twice: with the indexes of
create_indexes.sql and without any non-constraint index. The primary-key and
UNIQUE indexes stay in both configurations, so what is measured is the
marginal effect of the four statements in create_indexes.sql. The two
configurations are interleaved over six blocks of five runs, so that drift
in machine load hits both equally; the report quotes the average of the 30
runs per configuration.

Each query is timed on the statement that runs against the full data set
(Q1 carries one statement per incremental stage, so its last statement, the
2020-2022 window, is the one timed). One warm-up execution per query and
block is discarded.

Outputs:
  results/index_benchmark_raw.csv      config, block, query, run, time_ms
  results/index_benchmark_summary.csv  config, query, avg/median/min/max
  results/index_benchmark.png          grouped bar chart

Usage:
  python3 scripts/queries/run_index_benchmark.py

Requirements: psycopg (v3) and matplotlib.

The connection defaults to the team setup (localhost:5432/sd6103_project)
and can be overridden with PGHOST, PGPORT, PGDATABASE and PGUSER.
"""

from __future__ import annotations

import csv
import os
import statistics
import sys
import time
from pathlib import Path

import psycopg

PROJECT_ROOT = Path(__file__).resolve().parents[2]
QUERY_DIR = PROJECT_ROOT / "scripts" / "queries"
RESULT_DIR = PROJECT_ROOT / "results"
CREATE_SQL = QUERY_DIR / "create_indexes.sql"

BLOCKS = 6
RUNS = 5
FIGURE_WIDTH = 6.26          # inch — the text column of the report page
FIGURE_HEIGHT = 3.05
FIGURE_DPI = 300
DROP_SQL = """
DROP INDEX IF EXISTS idx_hdb_block_town;
DROP INDEX IF EXISTS resale_transaction_flat_idx;
DROP INDEX IF EXISTS resale_transaction_month_idx;
DROP INDEX IF EXISTS idx_prox_level;
"""
CONF = dict(
    host=os.environ.get("PGHOST", "localhost"),
    port=os.environ.get("PGPORT", "5432"),
    dbname=os.environ.get("PGDATABASE", "sd6103_project"),
    user=os.environ.get("PGUSER", "postgres"),
)


def statement(index: int) -> str:
    path = sorted(QUERY_DIR.glob(f"Q{index}_*.sql"))[0]
    return [s.strip() for s in path.read_text(encoding="utf-8").split(";") if s.strip()][-1]


def time_query(cur, sql: str) -> list[float]:
    cur.execute(sql)
    cur.fetchall()                              # warm-up, discarded
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        cur.execute(sql)
        cur.fetchall()
        times.append((time.perf_counter() - start) * 1000)
    return times


def measure() -> None:
    if not RESULT_DIR.exists():
        RESULT_DIR.mkdir()
    sql = {f"Q{i}": statement(i) for i in range(1, 7)}
    create_sql = CREATE_SQL.read_text(encoding="utf-8")
    rows = []

    with psycopg.connect(**CONF, autocommit=True) as conn:
        for block in range(1, BLOCKS + 1):
            # alternate the order so a monotone drift in machine load cannot
            # systematically favour one configuration
            order = [("baseline", DROP_SQL), ("indexed", create_sql)]
            for config, ddl in (order if block % 2 else order[::-1]):
                with conn.cursor() as cur:
                    cur.execute(ddl)
                    cur.execute("VACUUM ANALYZE;")
                for name, text in sql.items():
                    with conn.cursor() as cur:
                        times = time_query(cur, text)
                    rows.extend([config, block, name, run, round(ms, 1)]
                                for run, ms in enumerate(times, 1))
                    print(f"block {block}  {config:8s}  {name}: "
                          f"mean {statistics.mean(times):7.0f} ms")

    raw = RESULT_DIR / "index_benchmark_raw.csv"
    with raw.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["config", "block", "query", "run", "time_ms"])
        writer.writerows(rows)

    grouped: dict[tuple[str, str], list[float]] = {}
    for config, _block, name, _run, ms in rows:
        grouped.setdefault((config, name), []).append(ms)

    summary = RESULT_DIR / "index_benchmark_summary.csv"
    with summary.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["config", "query", "avg_ms", "median_ms", "min_ms", "max_ms"])
        for (config, name), values in sorted(grouped.items()):
            writer.writerow([config, name,
                             round(statistics.mean(values), 1),
                             round(statistics.median(values), 1),
                             round(min(values), 1),
                             round(max(values), 1)])
    print(f"\n{len(rows)} measurements -> {raw}")
    plot(grouped)


def plot(grouped: dict[tuple[str, str], list[float]]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [f"Q{i}" for i in range(1, 7)]
    base = [statistics.mean(grouped[("baseline", n)]) for n in names]
    idx = [statistics.mean(grouped[("indexed", n)]) for n in names]

    # the figure is printed at the width of the text column (6.26 in), so it is
    # drawn at exactly that width: the point sizes below are then the sizes the
    # reader actually sees, instead of being scaled down from a wider canvas
    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, FIGURE_HEIGHT), dpi=FIGURE_DPI)
    width = 0.38
    for offset, values, label, colour in ((-width / 2, base, "without Task-4 indexes", "#9aa0a6"),
                                          (width / 2, idx, "with Task-4 indexes", "#1f6feb")):
        bars = ax.bar([i + offset for i in range(len(names))], values, width,
                      label=label, color=colour)
        for bar, value in zip(bars, values):
            ax.annotate(f"{value:,.0f}", (bar.get_x() + bar.get_width() / 2, value),
                        ha="center", va="bottom", fontsize=7.0)

    for i, (before, after) in enumerate(zip(base, idx)):
        ax.annotate(f"{(after - before) / before * 100:+.0f}%", (i, max(before, after)),
                    ha="center", va="bottom", fontsize=8.5, color="#c0392b", xytext=(0, 11),
                    textcoords="offset points")

    ax.set_ylim(0, max(max(base), max(idx)) * 1.18)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, fontsize=8.5)
    ax.set_ylabel("average runtime over 30 runs (ms)", fontsize=8.5)
    ax.set_title("Query runtime with and without the Task-4 indexes (976,714 transactions)",
                 fontsize=9.5)
    ax.tick_params(axis="y", labelsize=8.5)
    ax.legend(frameon=False, fontsize=8.5, loc="upper right")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    ax.set_axisbelow(True)
    fig.tight_layout(pad=0.4)
    out = RESULT_DIR / "index_benchmark.png"
    fig.savefig(out)
    print(f"plot -> {out}")


def replot() -> None:
    grouped: dict[tuple[str, str], list[float]] = {}
    with (RESULT_DIR / "index_benchmark_summary.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            grouped.setdefault((row["config"], row["query"]), []).append(float(row["avg_ms"]))
    plot(grouped)


if __name__ == "__main__":
    if "--plot-only" in sys.argv:
        replot()
    else:
        measure()
