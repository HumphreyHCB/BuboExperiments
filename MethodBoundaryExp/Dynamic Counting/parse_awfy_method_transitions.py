#!/usr/bin/env python3
"""
parse_awfy_activation_counts.py

Parses AWFY-style benchmark logs that end with lines like:

    Total Runtime: 1010879930us
    Index : som.Vector.forEach(ForEachInterface) Activation Count : 450539227

For each *.log in a logs directory:
- benchmark name is taken from the filename (e.g. Havlak.log -> "Havlak")
- all "Index : ... Activation Count : N" lines are collected
- we write:
    1) awfy_activation_counts.csv  (Benchmark, Index, Activation Count)
    2) awfy_benchmark_totals.csv   (Benchmark, Total Activation Count)
    3) awfy_benchmark_totals.png   bar chart of totals
"""

import csv
import re
from pathlib import Path
from collections import defaultdict, OrderedDict
import matplotlib.pyplot as plt

# --------- HARD-CODED PATHS (edit if you move things) ----------
BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR  = BASE_DIR / "logs_awfy_2025-11-03_17-36-38"   # <-- change to your actual log dir

OUT_ROWS_CSV = BASE_DIR / "awfy_activation_counts.csv"
OUT_BENCH_CSV = BASE_DIR / "awfy_benchmark_totals.csv"
OUT_PNG       = BASE_DIR / "awfy_benchmark_totals.png"
# ---------------------------------------------------------------

# Example line:
# Index : havlak.HavlakLoopFinder.findLoops() Activation Count : 84410209166
LINE_RE = re.compile(
    r"^Index\s*:\s*(.+?)\s+Activation Count\s*:\s*(-?\d+)\s*$"
)

def parse_log(path: Path):
    """
    Return list[(index_name, count)] for one log file.
    """
    rows = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LINE_RE.match(line)
            if m:
                index_name = m.group(1).strip()
                count = int(m.group(2))
                rows.append((index_name, count))
    return rows

def main():
    if not LOGS_DIR.exists():
        raise FileNotFoundError(f"Missing logs directory: {LOGS_DIR}")

    # benchmark -> list of (index_name, count)
    bench_rows = defaultdict(list)

    # parse every .log
    for log_path in sorted(LOGS_DIR.glob("*.log")):
        bench_name = log_path.stem  # e.g. "Havlak"
        rows = parse_log(log_path)
        if rows:
            bench_rows[bench_name].extend(rows)

    # per-benchmark totals
    bench_totals = OrderedDict()
    for bench in sorted(bench_rows.keys()):
        total = sum(count for _, count in bench_rows[bench])
        bench_totals[bench] = total

    # --- write long CSV of all rows ---
    OUT_ROWS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_ROWS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Benchmark", "Index", "Activation Count"])
        for bench in sorted(bench_rows.keys()):
            for idx_name, count in bench_rows[bench]:
                w.writerow([bench, idx_name, count])

    # --- write per-benchmark totals CSV ---
    with OUT_BENCH_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Benchmark", "Total Activation Count"])
        for bench, total in bench_totals.items():
            w.writerow([bench, total])

    # --- make bar chart ---
    benches = list(bench_totals.keys())
    totals = list(bench_totals.values())

    if benches:
        # sort by total desc
        benches, totals = zip(
            *sorted(zip(benches, totals), key=lambda x: x[1], reverse=True)
        )

        plt.figure(figsize=(12, 6))
        plt.bar(benches, totals)
        plt.xticks(rotation=45, ha="right")

        # <<< here are the changes >>>
        import matplotlib.ticker as mticker
        plt.yscale("log")  # use logarithmic scale
        ax = plt.gca()
        fmt = mticker.ScalarFormatter(useOffset=False)
        fmt.set_scientific(False)   # show full numbers, not 1e11
        ax.yaxis.set_major_formatter(fmt)
        # optional: fewer ticks so they don’t overlap
        ax.yaxis.set_major_locator(mticker.MaxNLocator(8))
        # <<< end changes >>>

        plt.ylabel("Total Activation Count")
        plt.title("AWFY: Total Activation Count per Benchmark")
        plt.tight_layout()
        plt.savefig(OUT_PNG, dpi=200)


if __name__ == "__main__":
    main()
