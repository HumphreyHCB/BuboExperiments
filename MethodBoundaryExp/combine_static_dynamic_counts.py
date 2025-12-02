#!/usr/bin/env python3
"""
combine_static_dynamic_counts_dualaxis.py

Reads:
  ./Static Counting/awfy_benchmark_totals.csv
  ./Dynamic Counting/awfy_benchmark_totals.csv

Produces:
  combined_benchmark_counts.csv
  combined_benchmark_counts_dualaxis.png

Plots dynamic counts (left Y) and static counts (right Y) on a shared X axis,
both using log scales.
"""

import csv
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "Static Counting"
DYNAMIC_DIR = BASE_DIR / "Dynamic Counting"

STATIC_CSV = STATIC_DIR / "awfy_benchmark_totals.csv"
DYNAMIC_CSV = DYNAMIC_DIR / "awfy_benchmark_totals.csv"

OUT_CSV = BASE_DIR / "combined_benchmark_counts.csv"
OUT_PNG = BASE_DIR / "combined_benchmark_counts_dualaxis.png"


def read_totals(path: Path) -> dict[str, int]:
    """Read a 2-column CSV: Benchmark, Total …"""
    totals = {}
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        _ = next(r, None)
        for row in r:
            if len(row) < 2:
                continue
            name, val = row[0].strip(), row[1].strip()
            if not name:
                continue
            try:
                totals[name] = int(val)
            except ValueError:
                continue
    return totals


def main():
    if not STATIC_CSV.exists():
        raise FileNotFoundError(f"Missing static CSV: {STATIC_CSV}")
    if not DYNAMIC_CSV.exists():
        raise FileNotFoundError(f"Missing dynamic CSV: {DYNAMIC_CSV}")

    static_totals = read_totals(STATIC_CSV)
    dynamic_totals = read_totals(DYNAMIC_CSV)

    # union of benchmark names
    all_benchmarks = sorted(set(static_totals) | set(dynamic_totals))
    # order by descending dynamic total
    all_benchmarks.sort(key=lambda b: dynamic_totals.get(b, 0), reverse=True)

    # write combined CSV
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Benchmark", "Static Total", "Dynamic Total"])
        for b in all_benchmarks:
            w.writerow([b, static_totals.get(b, 0), dynamic_totals.get(b, 0)])

    # build values
    x = range(len(all_benchmarks))
    dynamic_vals = [dynamic_totals.get(b, 0) for b in all_benchmarks]
    static_vals = [static_totals.get(b, 0) for b in all_benchmarks]

    fig, ax1 = plt.subplots(figsize=(12, 6))
    ax2 = ax1.twinx()  # second y-axis

    # dynamic (left)
    ax1.bar(x, dynamic_vals, color="tab:blue", alpha=0.6, label="Dynamic (activation count)")
    ax1.set_yscale("log")
    ax1.set_ylabel("Dynamic Count (log scale)", color="tab:blue")
    ax1.tick_params(axis="y", labelcolor="tab:blue")

    # static (right)
    ax2.plot(x, static_vals, color="tab:orange", marker="o", label="Static (method transitions)")
    ax2.set_yscale("log")
    ax2.set_ylabel("Static Count (log scale)", color="tab:orange")
    ax2.tick_params(axis="y", labelcolor="tab:orange")

    # format x-axis
    plt.xticks(x, all_benchmarks, rotation=45, ha="right")

    # tidy tick formatting
    for axis in (ax1.yaxis, ax2.yaxis):
        fmt = mticker.ScalarFormatter(useOffset=False)
        fmt.set_scientific(False)
        fmt.set_powerlimits((-3, 6))
        axis.set_major_formatter(fmt)
        axis.set_major_locator(mticker.MaxNLocator(8))

    ax1.grid(True, which="both", axis="y", linestyle="--", alpha=0.3)
    plt.title("Static vs Dynamic Counts per Benchmark (dual log scale)")
    fig.tight_layout()
    plt.savefig(OUT_PNG, dpi=200)

    print(f"Wrote combined CSV: {OUT_CSV}")
    print(f"Wrote dual-axis plot: {OUT_PNG}")


if __name__ == "__main__":
    main()
