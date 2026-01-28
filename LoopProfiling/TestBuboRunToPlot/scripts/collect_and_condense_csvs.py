#!/usr/bin/env python3
"""
collect_and_condense_csvs.py

A) Collect + condense:
   - Scan every *.csv under:
       /home/hb478/repos/BuboExperiments/LoopProfiling/TestBuboRunToPlot/plots
   - Keep only rows where:
       * loop_median_pct present (non-empty)
       * runtime_share_pct > 2.0
       * loop_call_count == 0
   - De-duplicate identical rows
   - Write:
       condensed_loops_runtimeShareGT2_callCount0_withMedian.csv

B) Plot:
   - For each loop compute:
         abs_diff = |slowdown_pct - loop_median_pct|
   - For each benchmark, draw a BOX PLOT over all its loop diffs

Output plot:
   median_absdiff_boxplot_per_benchmark.pdf
"""

from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt


# =========================================================
# GLOBAL FONT SIZE (ALL TEXT = 8)
# =========================================================
plt.rcParams.update(
    {
        "font.size": 8,
        "axes.titlesize": 8,
        "axes.labelsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
    }
)

PLOTS_DIR = Path("/home/hb478/repos/BuboExperiments/LoopProfiling/TestBuboRunToPlot/plots")

CONDENSED_CSV = PLOTS_DIR / "condensed_loops_runtimeShareGT2_callCount0_withMedian.csv"
PLOT_PDF = PLOTS_DIR / "median_absdiff_boxplot_per_benchmark.pdf"

YLIM = 25.0  # fixed axis

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
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def safe_float(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s:
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
    if not (row.get("loop_median_pct") or "").strip():
        return False

    runtime_share = safe_float(row.get("runtime_share_pct", ""))
    if runtime_share is None or runtime_share <= 2.0:
        return False

    loop_call_count = safe_int(row.get("loop_call_count", ""))
    return loop_call_count == 0


def read_one_csv_lenient(path: Path) -> List[Dict[str, str]]:
    kept: List[Dict[str, str]] = []

    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)

        header = None
        for raw in reader:
            if raw and any(c.strip() for c in raw):
                header = [c.strip() for c in raw]
                break

        if header is None:
            return []

        dict_reader = csv.DictReader(f, fieldnames=header)

        for row in dict_reader:
            if not row or all(not (v or "").strip() for v in row.values()):
                continue
            if is_repeated_header_row(row):
                continue

            nr = normalise_row(row)
            if should_keep(nr):
                kept.append(nr)

    return kept


def collect_and_condense_csvs(plots_dir: Path, out_csv: Path) -> List[Dict[str, str]]:
    seen = set()
    rows: List[Dict[str, str]] = []

    for p in plots_dir.rglob("*.csv"):
        if p.resolve() == out_csv.resolve():
            continue
        for r in read_one_csv_lenient(p):
            key = tuple(r[k] for k in EXPECTED_HEADER)
            if key not in seen:
                seen.add(key)
                rows.append(r)

    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=EXPECTED_HEADER)
        w.writeheader()
        w.writerows(rows)

    print(f"[OK] Wrote condensed CSV: {out_csv} ({len(rows)} rows)")
    return rows


def compute_absdiffs_by_benchmark(rows: List[Dict[str, str]]) -> Dict[str, List[float]]:
    out: Dict[str, List[float]] = {}

    for r in rows:
        b = (r.get("benchmark") or "").strip()
        s = safe_float(r.get("slowdown_pct", ""))
        m = safe_float(r.get("loop_median_pct", ""))
        if not b or s is None or m is None:
            continue
        out.setdefault(b, []).append(abs(s - m))

    return out


def print_summary(diffs: Dict[str, List[float]]) -> None:
    print("\n=== ABS(BuboL − VTune) summary ===")
    all_vals: List[float] = []

    for b in sorted(diffs):
        vals = diffs[b]
        if not vals:
            continue
        all_vals.extend(vals)
        print(
            f"{b}: n={len(vals)} "
            f"min={min(vals):.2f}% "
            f"median={statistics.median(vals):.2f}% "
            f"max={max(vals):.2f}%"
        )

    if all_vals:
        print(f"OVERALL median: {statistics.median(all_vals):.2f}%")
    else:
        print("OVERALL: no data")


def plot_boxplot_absdiffs(diffs: Dict[str, List[float]], out_pdf: Path) -> None:
    benches = [b for b in sorted(diffs) if diffs[b]]
    data = [diffs[b] for b in benches]

    medians = [statistics.median(v) for v in data]
    has_high = [max(v) > YLIM for v in data]

    fig, ax = plt.subplots(figsize=(8, 4))

    # Horizontal boxplot
    bp = ax.boxplot(
        data,
        labels=benches,
        patch_artist=True,
        showfliers=True,
        vert=False,   # <<< KEY CHANGE
    )

    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    for i, box in enumerate(bp["boxes"]):
        box.set_facecolor(colors[i % len(colors)])
        box.set_alpha(0.6)

    # X-axis is now the scale
    ax.set_xlim(0, YLIM)
    ax.set_xlabel("Difference from baseline (%)")
    ax.set_title("BuboL")

    # Benchmarks on the left
    ax.set_yticklabels(benches)

    # Median labels: slightly ABOVE each box
    y_positions = range(1, len(benches) + 1)
    y_offset = 0.25  # vertical offset above the box

    for y, md in zip(y_positions, medians):
        ax.text(
            min(md, YLIM),      # keep x at the median
            y + y_offset,       # move label ABOVE the box
            f"{md:.1f}%",
            ha="center",
            va="bottom",
        )

    # Out-of-range indicators (right edge)
    for y, high in zip(y_positions, has_high):
        if high:
            ax.plot(YLIM, y, marker=">", markersize=6)

    fig.tight_layout()
    fig.savefig(out_pdf)
    plt.close(fig)

    print(f"[OK] Wrote plot: {out_pdf}")


def main() -> None:
    rows = collect_and_condense_csvs(PLOTS_DIR, CONDENSED_CSV)
    diffs = compute_absdiffs_by_benchmark(rows)
    print_summary(diffs)
    plot_boxplot_absdiffs(diffs, PLOT_PDF)


if __name__ == "__main__":
    main()