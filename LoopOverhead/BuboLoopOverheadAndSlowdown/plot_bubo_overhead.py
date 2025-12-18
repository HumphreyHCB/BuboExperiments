#!/usr/bin/env python3
import os
import re
import csv
from collections import OrderedDict

import numpy as np
import matplotlib.pyplot as plt

BASE_DIR = "FourPhase_BuboSlowdownRuns"
OUT_DIR = "plots_and_csvs"
os.makedirs(OUT_DIR, exist_ok=True)

# Regexes to extract data
AVG_RUNTIME_RE = re.compile(r"average:\s+(\d+)us")
CYCLES_RE = re.compile(r"Bubo\.RDTSC\.Harness\.main Total RDTSC cycles:\s+(\d+)")

def parse_file(path):
    """
    Parse one .out file and return (avg_runtime_us, total_cycles) as ints or None.
    Either may be missing; we don't fail if one is absent.
    """
    avg_us = None
    cycles = None

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if avg_us is None:
                    m = AVG_RUNTIME_RE.search(line)
                    if m:
                        avg_us = int(m.group(1))
                if cycles is None:
                    m = CYCLES_RE.search(line)
                    if m:
                        cycles = int(m.group(1))
                if avg_us is not None and cycles is not None:
                    break
    except FileNotFoundError:
        return None, None

    return avg_us, cycles

def safe_pct(numer, denom):
    """
    (numer/denom - 1) * 100, but returns NaN if inputs are missing/invalid.
    """
    numer = np.asarray(numer, dtype=float)
    denom = np.asarray(denom, dtype=float)
    out = np.full_like(numer, np.nan, dtype=float)
    ok = np.isfinite(numer) & np.isfinite(denom) & (denom != 0.0)
    out[ok] = (numer[ok] / denom[ok] - 1.0) * 100.0
    return out

def plot_three_bars(benches, a, b, c, ylabel, title, outpath, labels):
    if len(benches) == 0:
        return
    x = np.arange(len(benches))
    width = 0.25
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(x - width, a, width, label=labels[0])
    ax.bar(x,         b, width, label=labels[1])
    ax.bar(x + width, c, width, label=labels[2])
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(x)
    ax.set_xticklabels(benches, rotation=45, ha="right")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(outpath)
    plt.close(fig)

def plot_two_bars(benches, a, b, ylabel, title, outpath, labels):
    if len(benches) == 0:
        return
    x = np.arange(len(benches))
    width2 = 0.35
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(x - width2/2, a, width2, label=labels[0])
    ax.bar(x + width2/2, b, width2, label=labels[1])
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(x)
    ax.set_xticklabels(benches, rotation=45, ha="right")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(outpath)
    plt.close(fig)

def main():
    benchmarks = [
        d for d in sorted(os.listdir(BASE_DIR))
        if os.path.isdir(os.path.join(BASE_DIR, d))
    ]

    data = OrderedDict()

    for bench in benchmarks:
        bench_dir = os.path.join(BASE_DIR, bench)
        paths = {
            "baseline_noBubo":    os.path.join(bench_dir, f"{bench}_baseline_noBubo.out"),
            "baseline_withBubo":  os.path.join(bench_dir, f"{bench}_baseline_withBubo.out"),
            "slowdown_noBubo":    os.path.join(bench_dir, f"{bench}_slowdown_noBubo.out"),
            "slowdown_withBubo":  os.path.join(bench_dir, f"{bench}_slowdown_withBubo.out"),
        }

        bench_entry = {}
        any_metric_found = False

        for key, path in paths.items():
            avg_us, cycles = parse_file(path)
            bench_entry[key] = {"time": avg_us, "cycles": cycles}
            if (avg_us is not None) or (cycles is not None):
                any_metric_found = True

        if not any_metric_found:
            print(f"[WARN] No time or cycle data found anywhere for {bench}, skipping.")
            continue

        data[bench] = bench_entry

    if not data:
        print("No benchmark data found. Exiting.")
        return

    benches_all = list(data.keys())

    def to_array(metric, key):
        arr = []
        for b in benches_all:
            v = data[b][key][metric]
            arr.append(float(v) if v is not None else np.nan)
        return np.array(arr, dtype=float)

    # Raw values (NaN if missing)
    base_no_bubo_time   = to_array("time",   "baseline_noBubo")
    base_with_bubo_time = to_array("time",   "baseline_withBubo")
    slow_no_bubo_time   = to_array("time",   "slowdown_noBubo")
    slow_with_bubo_time = to_array("time",   "slowdown_withBubo")

    base_no_bubo_cycles   = to_array("cycles", "baseline_noBubo")
    base_with_bubo_cycles = to_array("cycles", "baseline_withBubo")
    slow_no_bubo_cycles   = to_array("cycles", "slowdown_noBubo")
    slow_with_bubo_cycles = to_array("cycles", "slowdown_withBubo")

    # % increases vs baseline_noBubo (time/cycles)
    pct_slow_no_bubo_time   = safe_pct(slow_no_bubo_time,   base_no_bubo_time)
    pct_slow_with_bubo_time = safe_pct(slow_with_bubo_time, base_no_bubo_time)
    pct_base_with_bubo_time = safe_pct(base_with_bubo_time, base_no_bubo_time)

    pct_slow_no_bubo_cycles   = safe_pct(slow_no_bubo_cycles,   base_no_bubo_cycles)
    pct_slow_with_bubo_cycles = safe_pct(slow_with_bubo_cycles, base_no_bubo_cycles)
    pct_base_with_bubo_cycles = safe_pct(base_with_bubo_cycles, base_no_bubo_cycles)

    # Runtime plots: keep only benches where baseline_noBubo_time exists
    time_mask = np.isfinite(base_no_bubo_time)
    benches_time = [b for i, b in enumerate(benches_all) if time_mask[i]]

    plot_three_bars(
        benches_time,
        pct_slow_no_bubo_time[time_mask],
        pct_slow_with_bubo_time[time_mask],
        pct_base_with_bubo_time[time_mask],
        ylabel="Runtime increase vs baseline (%)",
        title="Runtime overhead vs baseline (no slowdown, no Bubo)",
        outpath=os.path.join(OUT_DIR, "runtime_overhead_three_bars.png"),
        labels=("Slowdown, no Bubo", "Slowdown, with Bubo", "Bubo, no slowdown"),
    )

    # Cycles plots: keep only benches where baseline_noBubo_cycles exists
    cycles_mask = np.isfinite(base_no_bubo_cycles)
    benches_cycles = [b for i, b in enumerate(benches_all) if cycles_mask[i]]

    plot_three_bars(
        benches_cycles,
        pct_slow_no_bubo_cycles[cycles_mask],
        pct_slow_with_bubo_cycles[cycles_mask],
        pct_base_with_bubo_cycles[cycles_mask],
        ylabel="RDTSC cycles increase vs baseline (%)",
        title="Cycle overhead vs baseline (no slowdown, no Bubo)",
        outpath=os.path.join(OUT_DIR, "cycles_overhead_three_bars.png"),
        labels=("Slowdown, no Bubo", "Slowdown, with Bubo", "Bubo, no slowdown"),
    )

    # Extra Bubo overhead (runtime) plot: only where both slow_* time % are finite
    extra_bubo_in_slow_time = pct_slow_with_bubo_time - pct_slow_no_bubo_time
    bubo_no_slow_time = pct_base_with_bubo_time
    extra_mask = np.isfinite(extra_bubo_in_slow_time) & np.isfinite(bubo_no_slow_time) & time_mask
    benches_extra = [b for i, b in enumerate(benches_all) if extra_mask[i]]

    plot_two_bars(
        benches_extra,
        extra_bubo_in_slow_time[extra_mask],
        bubo_no_slow_time[extra_mask],
        ylabel="Percentage points vs baseline (%)",
        title="Bubo extra overhead: slowdown vs no slowdown (runtime)",
        outpath=os.path.join(OUT_DIR, "bubo_extra_overhead_runtime.png"),
        labels=("Extra Bubo cost in slowdown run", "Bubo overhead (no slowdown)"),
    )

    # ---------------- CSVs ----------------
    runtime_csv = os.path.join(OUT_DIR, "runtime_overhead_three_bars.csv")
    with open(runtime_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Benchmark",
            "baseline_noBubo_us",
            "slowdown_noBubo_us",
            "slowdown_withBubo_us",
            "baseline_withBubo_us",
            "pct_slowdown_noBubo_vs_baseline",
            "pct_slowdown_withBubo_vs_baseline",
            "pct_baseline_withBubo_vs_baseline",
            "extra_Bubo_in_slowdown_vs_baseline",
            "Bubo_no_slowdown_vs_baseline",
        ])
        for i, b in enumerate(benches_all):
            writer.writerow([
                b,
                base_no_bubo_time[i],
                slow_no_bubo_time[i],
                slow_with_bubo_time[i],
                base_with_bubo_time[i],
                pct_slow_no_bubo_time[i],
                pct_slow_with_bubo_time[i],
                pct_base_with_bubo_time[i],
                (pct_slow_with_bubo_time[i] - pct_slow_no_bubo_time[i]) if (np.isfinite(pct_slow_with_bubo_time[i]) and np.isfinite(pct_slow_no_bubo_time[i])) else np.nan,
                pct_base_with_bubo_time[i],
            ])

    cycles_csv = os.path.join(OUT_DIR, "cycles_overhead_three_bars.csv")
    with open(cycles_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Benchmark",
            "baseline_noBubo_cycles",
            "slowdown_noBubo_cycles",
            "slowdown_withBubo_cycles",
            "baseline_withBubo_cycles",
            "pct_slowdown_noBubo_vs_baseline",
            "pct_slowdown_withBubo_vs_baseline",
            "pct_baseline_withBubo_vs_baseline",
        ])
        for i, b in enumerate(benches_all):
            writer.writerow([
                b,
                base_no_bubo_cycles[i],
                slow_no_bubo_cycles[i],
                slow_with_bubo_cycles[i],
                base_with_bubo_cycles[i],
                pct_slow_no_bubo_cycles[i],
                pct_slow_with_bubo_cycles[i],
                pct_base_with_bubo_cycles[i],
            ])

    print(f"Plots and CSVs written to: {OUT_DIR}")

if __name__ == "__main__":
    main()
