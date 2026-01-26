#!/usr/bin/env python3
"""
compute_benchmark_overhead.py
"""

import csv
import re
from pathlib import Path
import statistics

import matplotlib.pyplot as plt

# =========================================================
# GLOBAL FONT SIZE (ALL TEXT = 8)
# =========================================================
plt.rcParams.update({
    "font.size": 8,
    "axes.titlesize": 8,
    "axes.labelsize": 8,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
})

def median(values):
    return statistics.median(values) if values else float("nan")


BASE_DIR = Path(__file__).resolve().parent
INSTRUMENT_DIR = BASE_DIR / "Instrument"
NOINSTRUMENT_DIR = BASE_DIR / "NoInstrument"

OUT_CSV = BASE_DIR / "BuboL_Overhead.csv"
OUT_PNG = BASE_DIR / "BuboL_Overhead.pdf"

LINE_RE = re.compile(
    r"^\s*(?P<name>[^:]+):\s*iterations=\d+\s+average:\s*(?P<avg>\d+)us\b"
)

HIGHLIGHT = {
    "LoopsBench",
    "Sieve",
    "NBody",
    "Mandelbrot",
    "Json",
    "Bounce",
}


def parse_folder(folder: Path) -> dict[str, int]:
    if not folder.exists():
        raise FileNotFoundError(f"Missing folder: {folder}")

    result: dict[str, int] = {}

    for path in sorted(folder.glob("*.log")):
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                m = LINE_RE.match(line)
                if not m:
                    continue
                result[m.group("name").strip()] = int(m.group("avg"))

    return result


def main():
    noinst = parse_folder(NOINSTRUMENT_DIR)
    inst = parse_folder(INSTRUMENT_DIR)

    common_benchmarks = sorted(set(noinst) & set(inst))
    if not common_benchmarks:
        raise RuntimeError("No common benchmarks found between Instrument and NoInstrument.")

    rows = []
    for b in common_benchmarks:
        base_us = noinst[b]
        inst_us = inst[b]
        if base_us <= 0:
            factor = percent = float("nan")
        else:
            factor = inst_us / base_us
            percent = (inst_us - base_us) / base_us * 100.0
        rows.append((b, base_us, inst_us, factor, percent))

    rows.sort(key=lambda r: (float("-inf") if r[4] != r[4] else -r[4]))

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Benchmark",
            "NoInstrument_avg_us",
            "Instrument_avg_us",
            "Overhead_factor",
            "Overhead_percent",
        ])
        for row in rows:
            w.writerow(row)

    benchmarks = [r[0] for r in rows if r[4] == r[4]]
    overhead_percent = [r[4] for r in rows if r[4] == r[4]]

    colors = ["red" if b in HIGHLIGHT else "C0" for b in benchmarks]

    # Square canvas, and let Matplotlib manage padding for labels
    fig, ax = plt.subplots(figsize=(6, 6), layout="constrained")

    ax.bar(range(len(benchmarks)), overhead_percent, color=colors)
    ax.set_xticks(range(len(benchmarks)))
    ax.set_xticklabels(benchmarks, rotation=45, ha="right")

    ax.set_ylabel("Overhead (%)")
    ax.set_xlabel("Benchmark")
    ax.set_title("BuboL's Overhead per Benchmark")
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)

    # Make the *axes box* square, and keep it centred in the figure
    ax.set_box_aspect(1)
    ax.set_anchor("C")

    # IMPORTANT: do not use bbox_inches="tight" if you want a square PDF canvas
    fig.savefig(OUT_PNG, dpi=200, pad_inches=0.25)



    print(f"Wrote overhead CSV: {OUT_CSV}")
    print(f"Wrote overhead plot: {OUT_PNG}")

    # =========================================================
    # Console statistics
    # =========================================================

    per_benchmark = {b: pct for (b, _, _, _, pct) in rows if pct == pct}

    highlighted_overheads = [
        pct for b, pct in per_benchmark.items() if b in HIGHLIGHT
    ]

    all_overheads = list(per_benchmark.values())

    print()
    print("===== Overhead Summary =====")
    print(f"Median overhead (highlighted benchmarks): {median(highlighted_overheads):.2f}%")
    print(f"Median overhead (all benchmarks):        {median(all_overheads):.2f}%")
    print()

    print("Per-benchmark overheads:")
    for b, pct in sorted(per_benchmark.items(), key=lambda x: -x[1]):
        tag = " [HIGHLIGHTED]" if b in HIGHLIGHT else ""
        print(f"  {b}: {pct:.2f}%{tag}")


if __name__ == "__main__":
    main()
