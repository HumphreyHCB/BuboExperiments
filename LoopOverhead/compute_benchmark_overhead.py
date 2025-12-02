#!/usr/bin/env python3
"""
compute_benchmark_overhead.py

Reads logs from:
  ./Instrument
  ./NoInstrument

Each log file is expected to contain lines like:
  BenchmarkName: iterations=500 average: 120383us total: 60191668us

We:
  - Parse the *average* time (in microseconds) per benchmark from each folder.
  - Use NoInstrument as the baseline.
  - Compute overhead:
        factor  = Instrument / NoInstrument
        percent = (Instrument - NoInstrument) / NoInstrument * 100
  - Write:
        benchmark_overhead.csv
  - Plot:
        benchmark_overhead.png
    as a bar chart of percentage overhead per benchmark (sorted by highest overhead).
"""

import csv
import re
from pathlib import Path
import matplotlib.pyplot as plt

BASE_DIR = Path(__file__).resolve().parent
INSTRUMENT_DIR = BASE_DIR / "Instrument"
NOINSTRUMENT_DIR = BASE_DIR / "NoInstrument"

OUT_CSV = BASE_DIR / "benchmark_overhead.csv"
OUT_PNG = BASE_DIR / "benchmark_overhead.png"

# Example line:
#   DeltaBlue: iterations=500 average: 120383us total: 60191668us
LINE_RE = re.compile(
    r"^\s*(?P<name>[^:]+):\s*iterations=\d+\s+average:\s*(?P<avg>\d+)us\b"
)


def parse_folder(folder: Path) -> dict[str, int]:
    """
    Parse all log files in a folder and return:
        { benchmark_name: average_us }
    """
    if not folder.exists():
        raise FileNotFoundError(f"Missing folder: {folder}")

    result: dict[str, int] = {}

    for path in sorted(folder.glob("*.log")):
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                m = LINE_RE.match(line)
                if not m:
                    continue
                name = m.group("name").strip()
                avg_us = int(m.group("avg"))
                # If multiple lines per file/benchmark, last one wins.
                result[name] = avg_us

    return result


def main():
    noinst = parse_folder(NOINSTRUMENT_DIR)
    inst = parse_folder(INSTRUMENT_DIR)

    # Only compare benchmarks present in both.
    common_benchmarks = sorted(set(noinst) & set(inst))
    if not common_benchmarks:
        raise RuntimeError("No common benchmarks found between Instrument and NoInstrument.")

    rows = []
    for b in common_benchmarks:
        base_us = noinst[b]
        inst_us = inst[b]
        if base_us <= 0:
            # Avoid division by zero; skip or mark specially.
            factor = float("nan")
            percent = float("nan")
        else:
            factor = inst_us / base_us
            percent = (inst_us - base_us) / base_us * 100.0
        rows.append((b, base_us, inst_us, factor, percent))

    # Sort by descending percentage overhead (NaNs last).
    rows.sort(key=lambda r: (float("-inf") if r[4] != r[4] else -r[4]))

    # Write CSV
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Benchmark",
            "NoInstrument_avg_us",
            "Instrument_avg_us",
            "Overhead_factor",
            "Overhead_percent"
        ])
        for b, base_us, inst_us, factor, percent in rows:
            w.writerow([b, base_us, inst_us, factor, percent])

    # Build data for plotting
    benchmarks = [r[0] for r in rows if r[4] == r[4]]  # filter out NaN
    overhead_percent = [r[4] for r in rows if r[4] == r[4]]

    x = range(len(benchmarks))
    fig, ax = plt.subplots(figsize=(12, 6))

    ax.bar(x, overhead_percent)
    ax.set_xticks(x)
    ax.set_xticklabels(benchmarks, rotation=45, ha="right")

    ax.set_ylabel("Overhead vs NoInstrument (%)")
    ax.set_xlabel("Benchmark")
    ax.set_title("Instrumentation Overhead per Benchmark (NoInstrument baseline)")

    ax.grid(True, axis="y", linestyle="--", alpha=0.3)

    fig.tight_layout()
    plt.savefig(OUT_PNG, dpi=200)

    print(f"Wrote overhead CSV: {OUT_CSV}")
    print(f"Wrote overhead plot: {OUT_PNG}")


if __name__ == "__main__":
    main()
