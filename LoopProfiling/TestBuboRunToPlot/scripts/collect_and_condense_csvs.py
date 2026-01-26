#!/usr/bin/env python3
"""
collect_and_condense_csvs.py

1) Reads (does NOT modify) every .csv under:
   /home/hb478/repos/BuboExperiments/LoopProfiling/TestBuboRunToPlot/plots

2) Writes ONE combined CSV containing only rows that meet ALL conditions:
   - loop_median_pct is present (non-empty)
   - runtime_share_pct > 2.0
   - loop_call_count == 0

3) Produces a plot:
   For each benchmark:
     - For every comp in that benchmark (from the kept rows), compute:
         diff = slowdown_pct - loop_median_pct
     - Take the median of diffs across comps for that benchmark
     - Add error bars showing the range (min to max) of diffs across comps

Outputs (in the plots directory):
  - condensed_loops_runtimeShareGT2_callCount0_withMedian.csv
  - median_diff_slowdownPct_minus_loopMedianPct_per_benchmark.pdf
"""

from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt


PLOTS_DIR = Path("/home/hb478/repos/BuboExperiments/LoopProfiling/TestBuboRunToPlot/plots")

OUT_CSV = PLOTS_DIR / "condensed_loops_runtimeShareGT2_callCount0_withMedian.csv"
OUT_PDF = PLOTS_DIR / "median_diff_slowdownPct_minus_loopMedianPct_per_benchmark.pdf"

EXPECTED_HEADER = [
    "benchmark",
    "comp_id",
    "comp_name",
    "method_dot",
    "loop_id",
    "loop_call_count",
    "baseline_exclusive_cycles",
    "slowdown_exclusive_cycles",
    "slowdown_pct",
    "loop_median_pct",
    "runtime_share_pct",
    "total_cycles_slowdown",
    "prog_slowdown_pct",
]


def safe_int(s: str) -> Optional[int]:
    s = (s or "").strip()
    if s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def safe_float(s: str) -> Optional[float]:
    s = (s or "").strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def is_repeated_header_row(row: Dict[str, str]) -> bool:
    return (row.get("benchmark") or "").strip().lower() == "benchmark"


def normalise_row(row: Dict[str, str]) -> Dict[str, str]:
    return {k: (row.get(k, "") or "") for k in EXPECTED_HEADER}


def should_keep(row: Dict[str, str]) -> bool:
    loop_median_raw = (row.get("loop_median_pct") or "").strip()
    if loop_median_raw == "":
        return False

    runtime_share = safe_float(row.get("runtime_share_pct", ""))
    if runtime_share is None or runtime_share <= 2.0:
        return False

    loop_call_count = safe_int(row.get("loop_call_count", ""))
    if loop_call_count is None or loop_call_count != 0:
        return False

    return True


def read_csv_rows(path: Path) -> Tuple[int, int, List[Dict[str, str]]]:
    """
    Returns: (rows_seen, rows_kept, kept_rows)
    """
    rows_seen = 0
    rows_kept = 0
    kept: List[Dict[str, str]] = []

    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)

        header: Optional[List[str]] = None
        for raw in reader:
            if not raw or all((c or "").strip() == "" for c in raw):
                continue
            header = [c.strip() for c in raw]
            break

        if header is None:
            return (0, 0, [])

        dict_reader = csv.DictReader(f, fieldnames=header)

        for row in dict_reader:
            if row is None or all(((v or "").strip() == "") for v in row.values()):
                continue
            if is_repeated_header_row(row):
                continue

            rows_seen += 1

            nr = normalise_row(row)
            if should_keep(nr):
                kept.append(nr)
                rows_kept += 1

    return (rows_seen, rows_kept, kept)


def build_plot(rows: List[Dict[str, str]]) -> None:
    """
    For each benchmark, compute per-comp diff = slowdown_pct - loop_median_pct,
    then benchmark-level median and range error bars.
    """
    # Collect diffs per (benchmark, comp_id)
    diffs_by_bench_comp: Dict[Tuple[str, int], List[float]] = {}

    for r in rows:
        bench = (r.get("benchmark") or "").strip()
        comp_id = safe_int(r.get("comp_id", ""))
        slowdown_pct = safe_float(r.get("slowdown_pct", ""))
        loop_median_pct = safe_float(r.get("loop_median_pct", ""))

        if not bench or comp_id is None or slowdown_pct is None or loop_median_pct is None:
            continue

        diff = slowdown_pct - loop_median_pct
        diffs_by_bench_comp.setdefault((bench, comp_id), []).append(diff)

    # Reduce to a single diff per comp per benchmark (median across rows for that comp)
    diffs_by_bench: Dict[str, List[float]] = {}
    for (bench, _comp_id), diffs in diffs_by_bench_comp.items():
        if not diffs:
            continue
        comp_median = statistics.median(diffs)
        diffs_by_bench.setdefault(bench, []).append(comp_median)

    # Now benchmark-level stats
    benches = sorted(diffs_by_bench.keys())
    if not benches:
        print("[WARN] No data to plot after grouping, check filters and input CSVs.")
        return

    medians: List[float] = []
    err_low: List[float] = []
    err_high: List[float] = []

    # Only keep benchmarks that have at least 2 comps, since you said "benchmarks that have multiple comps"
    benches_filtered: List[str] = []
    for b in benches:
        vals = diffs_by_bench[b]
        if len(vals) < 2:
            continue
        m = statistics.median(vals)
        vmin = min(vals)
        vmax = max(vals)
        benches_filtered.append(b)
        medians.append(m)
        err_low.append(m - vmin)
        err_high.append(vmax - m)

    if not benches_filtered:
        print("[WARN] No benchmarks with multiple comps to plot.")
        return

    x = list(range(len(benches_filtered)))
    yerr = [err_low, err_high]  # asymmetric

    plt.figure(figsize=(10, 4))
    plt.errorbar(x, medians, yerr=yerr, fmt="o", capsize=3)
    plt.axhline(0.0, linewidth=1)
    plt.xticks(x, benches_filtered, rotation=45, ha="right")
    plt.ylabel("Median(diff) where diff = slowdown_pct - loop_median_pct")
    plt.title("Per benchmark median difference across comps, with range error bars")
    plt.tight_layout()
    plt.savefig(OUT_PDF)
    plt.close()


def main() -> None:
    if not PLOTS_DIR.is_dir():
        raise SystemExit(f"[ERROR] Directory not found: {PLOTS_DIR}")

    csv_files = sorted(p for p in PLOTS_DIR.rglob("*.csv") if p.is_file())

    total_seen = 0
    total_kept = 0
    all_kept: List[Dict[str, str]] = []

    for p in csv_files:
        try:
            seen, kept, rows = read_csv_rows(p)
        except Exception as e:
            print(f"[WARN] Failed to read {p}: {e}")
            continue

        total_seen += seen
        total_kept += kept
        all_kept.extend(rows)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as out:
        w = csv.DictWriter(out, fieldnames=EXPECTED_HEADER, extrasaction="ignore")
        w.writeheader()
        for r in all_kept:
            w.writerow(r)

    print(f"[OK] Found CSV files: {len(csv_files)}")
    print(f"[OK] Rows seen (non-empty, non-header): {total_seen}")
    print(f"[OK] Rows kept (filtered): {total_kept}")
    print(f"[OK] Wrote condensed CSV: {OUT_CSV}")

    build_plot(all_kept)
    if OUT_PDF.exists():
        print(f"[OK] Wrote plot: {OUT_PDF}")


if __name__ == "__main__":
    main()