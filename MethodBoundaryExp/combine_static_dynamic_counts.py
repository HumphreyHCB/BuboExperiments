#!/usr/bin/env python3
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

SHOW_VALUE_LABELS = True
LABEL_FONT_SIZE = 8

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "Static Counting"
DYNAMIC_DIR = BASE_DIR / "Dynamic Counting"

STATIC_CSV = STATIC_DIR / "awfy_benchmark_totals.csv"
DYNAMIC_CSV = DYNAMIC_DIR / "awfy_benchmark_totals.csv"

OUT_CSV = BASE_DIR / "combined_benchmark_counts.csv"
OUT_PDF = BASE_DIR / "combined_benchmark_counts_stackedbars.pdf"


def read_totals(path: Path) -> dict[str, int]:
    totals: dict[str, int] = {}
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


def add_value_labels(ax, values: np.ndarray) -> None:
    for i, v in enumerate(values):
        if not np.isfinite(v) or v <= 0:
            continue

        if v >= 1e6:
            exp = int(np.floor(np.log10(v)))
            mantissa = v / (10 ** exp)

            if mantissa >= 9.5:
                label = rf"$10^{{{exp + 1}}}$"
            else:
                label = rf"${mantissa:.1f}\times 10^{{{exp}}}$"
        else:
            label = f"{int(v)}"

        ax.text(
            i,
            v,
            label,
            ha="center",
            va="bottom",
            fontsize=LABEL_FONT_SIZE,
            rotation=0,
            clip_on=True,
        )




def set_three_log_ticks(ax, values: np.ndarray, formatter) -> None:
    vals = np.array(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return
    vmin = float(np.min(vals))
    vmax = float(np.max(vals))
    if vmin <= 0 or vmax <= 0:
        return

    lo = int(np.floor(np.log10(vmin)))
    hi = int(np.ceil(np.log10(vmax)))
    mid = int(np.round((lo + hi) / 2))

    ticks = [10**lo, 10**mid, 10**hi]
    ticks = sorted(set(ticks))

    ax.yaxis.set_major_locator(mticker.FixedLocator(ticks))
    ax.yaxis.set_major_formatter(formatter)


def main() -> None:
    if not STATIC_CSV.exists():
        raise FileNotFoundError(f"Missing static CSV: {STATIC_CSV}")
    if not DYNAMIC_CSV.exists():
        raise FileNotFoundError(f"Missing dynamic CSV: {DYNAMIC_CSV}")

    static_totals = read_totals(STATIC_CSV)
    dynamic_totals = read_totals(DYNAMIC_CSV)

    all_benchmarks = sorted(set(static_totals) | set(dynamic_totals))

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Benchmark", "Static Total", "Dynamic Total"])
        for b in all_benchmarks:
            w.writerow([b, static_totals.get(b, 0), dynamic_totals.get(b, 0)])

    x = np.arange(len(all_benchmarks))
    dynamic_vals = np.array([dynamic_totals.get(b, 0) for b in all_benchmarks], dtype=float)
    static_vals = np.array([static_totals.get(b, 0) for b in all_benchmarks], dtype=float)
    # Clamp Mandelbrot to 0
    if "Mandelbrot" in all_benchmarks:
        idx = all_benchmarks.index("Mandelbrot")
        dynamic_vals[idx] = 0.0
        static_vals[idx] = 0.0

    dynamic_vals[dynamic_vals <= 0] = np.nan
    static_vals[static_vals <= 0] = np.nan

    cmap = plt.get_cmap("tab20")
    colors = [cmap(i % cmap.N) for i in range(len(all_benchmarks))]

    fig, (ax_top, ax_bottom) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)

    ax_top.bar(x, dynamic_vals, color=colors)
    ax_top.set_yscale("log")
    ax_top.set_ylabel("Method Boundary Activations", rotation=90)
    ax_top.grid(True, which="both", axis="y", linestyle="--", alpha=0.3)

    dyn_fmt = mticker.LogFormatterSciNotation(base=10)
    set_three_log_ticks(ax_top, dynamic_vals, dyn_fmt)

    if SHOW_VALUE_LABELS:
        add_value_labels(ax_top, dynamic_vals)

    ax_bottom.bar(x, static_vals, color=colors)
    ax_bottom.set_yscale("log")
    ax_bottom.set_ylabel("Method Boundary Sites", rotation=90)
    ax_bottom.grid(True, which="both", axis="y", linestyle="--", alpha=0.3)

    stat_fmt = mticker.ScalarFormatter()
    stat_fmt.set_scientific(False)
    set_three_log_ticks(ax_bottom, static_vals, stat_fmt)

    if SHOW_VALUE_LABELS:
        add_value_labels(ax_bottom, static_vals)

    ax_bottom.set_xticks(x)
    ax_bottom.set_xticklabels(all_benchmarks, rotation=45, ha="right")

    fig.tight_layout()
    plt.savefig(OUT_PDF)
    plt.close(fig)

    print(f"Wrote combined CSV: {OUT_CSV}")
    print(f"Wrote stacked bar plot: {OUT_PDF}")


if __name__ == "__main__":
    main()
