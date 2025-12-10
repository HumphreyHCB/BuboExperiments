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
CYCLES_RE = re.compile(
    r"Bubo\.RDTSC\.Harness\.main Total RDTSC cycles:\s+(\d+)"
)

def parse_file(path):
    """
    Parse one .out file and return (avg_runtime_us, total_cycles) as ints or None.
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


def main():
    # Discover benchmarks from subdirectories
    benchmarks = [
        d for d in sorted(os.listdir(BASE_DIR))
        if os.path.isdir(os.path.join(BASE_DIR, d))
    ]

    # Data structure:
    # data[bench] = {
    #   "baseline_noBubo": {"time": ..., "cycles": ...},
    #   "baseline_withBubo": {...},
    #   "slowdown_noBubo": {...},
    #   "slowdown_withBubo": {...},
    # }
    data = OrderedDict()

    for bench in benchmarks:
        bench_dir = os.path.join(BASE_DIR, bench)
        paths = {
            "baseline_noBubo":  os.path.join(bench_dir, f"{bench}_baseline_noBubo.out"),
            "baseline_withBubo": os.path.join(bench_dir, f"{bench}_baseline_withBubo.out"),
            "slowdown_noBubo": os.path.join(bench_dir, f"{bench}_slowdown_noBubo.out"),
            "slowdown_withBubo": os.path.join(bench_dir, f"{bench}_slowdown_withBubo.out"),
        }

        bench_entry = {}
        missing = False
        for key, path in paths.items():
            avg_us, cycles = parse_file(path)
            if avg_us is None:
                print(f"[WARN] Could not find average runtime in {path}, skipping benchmark {bench}")
                missing = True
                break
            bench_entry[key] = {"time": avg_us, "cycles": cycles}

        if not missing:
            data[bench] = bench_entry

    if not data:
        print("No complete benchmark data found. Exiting.")
        return

    # Prepare arrays for plots / CSVs
    benches = list(data.keys())

    # Runtime raw values
    base_no_bubo_time      = np.array([data[b]["baseline_noBubo"]["time"] for b in benches], dtype=float)
    base_with_bubo_time    = np.array([data[b]["baseline_withBubo"]["time"] for b in benches], dtype=float)
    slow_no_bubo_time      = np.array([data[b]["slowdown_noBubo"]["time"] for b in benches], dtype=float)
    slow_with_bubo_time    = np.array([data[b]["slowdown_withBubo"]["time"] for b in benches], dtype=float)

    # Cycles raw values (may be None for some; treat missing as NaN)
    def to_cycles_array(key):
        arr = []
        for b in benches:
            val = data[b][key]["cycles"]
            arr.append(float(val) if val is not None else np.nan)
        return np.array(arr, dtype=float)

    base_no_bubo_cycles   = to_cycles_array("baseline_noBubo")
    base_with_bubo_cycles = to_cycles_array("baseline_withBubo")
    slow_no_bubo_cycles   = to_cycles_array("slowdown_noBubo")
    slow_with_bubo_cycles = to_cycles_array("slowdown_withBubo")

    # Percentage increases vs baseline_noBubo (time)
    pct_slow_no_bubo_time   = (slow_no_bubo_time / base_no_bubo_time - 1.0) * 100.0
    pct_slow_with_bubo_time = (slow_with_bubo_time / base_no_bubo_time - 1.0) * 100.0
    pct_base_with_bubo_time = (base_with_bubo_time / base_no_bubo_time - 1.0) * 100.0

    # Percentage increases vs baseline_noBubo (cycles)
    pct_slow_no_bubo_cycles   = (slow_no_bubo_cycles / base_no_bubo_cycles - 1.0) * 100.0
    pct_slow_with_bubo_cycles = (slow_with_bubo_cycles / base_no_bubo_cycles - 1.0) * 100.0
    pct_base_with_bubo_cycles = (base_with_bubo_cycles / base_no_bubo_cycles - 1.0) * 100.0

    # ---------------- Plot 1: Runtime % increase (3 bars) ----------------
    x = np.arange(len(benches))
    width = 0.25

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(x - width, pct_slow_no_bubo_time,   width, label="Slowdown, no Bubo")
    ax.bar(x,         pct_slow_with_bubo_time, width, label="Slowdown, with Bubo")
    ax.bar(x + width, pct_base_with_bubo_time, width, label="Bubo, no slowdown")

    ax.set_ylabel("Runtime increase vs baseline (%)")
    ax.set_title("Runtime overhead vs baseline (no slowdown, no Bubo)")
    ax.set_xticks(x)
    ax.set_xticklabels(benches, rotation=45, ha="right")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR, "runtime_overhead_three_bars.png"))
    plt.close(fig)

    # ---------------- Plot 2: Cycles % increase (3 bars) ----------------
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(x - width, pct_slow_no_bubo_cycles,   width, label="Slowdown, no Bubo")
    ax.bar(x,         pct_slow_with_bubo_cycles, width, label="Slowdown, with Bubo")
    ax.bar(x + width, pct_base_with_bubo_cycles, width, label="Bubo, no slowdown")

    ax.set_ylabel("RDTSC cycles increase vs baseline (%)")
    ax.set_title("Cycle overhead vs baseline (no slowdown, no Bubo)")
    ax.set_xticks(x)
    ax.set_xticklabels(benches, rotation=45, ha="right")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR, "cycles_overhead_three_bars.png"))
    plt.close(fig)

    # ---------------- Plot 3: Extra Bubo overhead (runtime) ------------- 
    # Extra cost of Bubo in slowdown run (relative to baseline)
    extra_bubo_in_slow_time = pct_slow_with_bubo_time - pct_slow_no_bubo_time
    # Bubo overhead with no slowdown (relative to baseline)
    bubo_no_slow_time = pct_base_with_bubo_time

    fig, ax = plt.subplots(figsize=(12, 6))
    width2 = 0.35

    ax.bar(x - width2/2, extra_bubo_in_slow_time, width2,
           label="Extra Bubo cost in slowdown run")
    ax.bar(x + width2/2, bubo_no_slow_time,       width2,
           label="Bubo overhead (no slowdown)")

    ax.set_ylabel("Percentage points vs baseline (%)")
    ax.set_title("Bubo extra overhead: slowdown vs no slowdown (runtime)")
    ax.set_xticks(x)
    ax.set_xticklabels(benches, rotation=45, ha="right")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR, "bubo_extra_overhead_runtime.png"))
    plt.close(fig)

    # ---------------- CSVs ----------------
    # CSV 1: Runtime
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
            "extra_Bubo_in_slowdown_vs_baseline",  # new
            "Bubo_no_slowdown_vs_baseline"         # == pct_baseline_withBubo_vs_baseline
        ])
        for i, b in enumerate(benches):
            writer.writerow([
                b,
                int(base_no_bubo_time[i]),
                int(slow_no_bubo_time[i]),
                int(slow_with_bubo_time[i]),
                int(base_with_bubo_time[i]),
                float(pct_slow_no_bubo_time[i]),
                float(pct_slow_with_bubo_time[i]),
                float(pct_base_with_bubo_time[i]),
                float(extra_bubo_in_slow_time[i]),
                float(bubo_no_slow_time[i]),
            ])

    # CSV 2: Cycles
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
        for i, b in enumerate(benches):
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
