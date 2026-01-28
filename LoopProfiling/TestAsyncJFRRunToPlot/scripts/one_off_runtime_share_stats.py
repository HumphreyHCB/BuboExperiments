#!/usr/bin/env python3
"""
one_off_runtime_share_stats_async_only.py

One-off stats script (read-only).
Assumes this file already exists:
  /home/hb478/repos/BuboExperiments/LoopProfiling/TestAsyncJFRRunToPlot/plots/condensed_vtune_async_jfr_shareGT2.csv

Goal:
  Compute slow_run_share_pct stats WITHOUT double-counting the JFR-only rows.

Default behaviour:
  - Use ONLY rows that have async_pct present (these are your "VTune, Async" baseline rows).
  - This excludes JFR-only rows (where vtune_pct/async_pct are blank and only jfr_pct is filled).

Printed output:
1) Per benchmark:
   - n rows
   - min / median / max of slow_run_share_pct
   - sum of slow_run_share_pct (coverage of selected rows)

2) Across all selected rows:
   - min / median / max of slow_run_share_pct

3) Per-benchmark aggregates:
   - median of per-benchmark medians, plus min and max
   - median of per-benchmark totals (sum), plus min and max
"""

from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Dict, List, Optional

CONDENSED_CSV = Path(
    "/home/hb478/repos/BuboExperiments/LoopProfiling/TestAsyncJFRRunToPlot/plots/condensed_vtune_async_jfr_shareGT2.csv"
)

# Select which rows count toward runtime share stats:
#   "async" -> only rows with async_pct present (recommended for your case)
#   "vtune" -> only rows with vtune_pct present
#   "jfr"   -> only rows with jfr_pct present
#   "any"   -> all rows (will double count in your current condensed CSV)
ROW_MODE = "async"


def safe_float(s: str) -> Optional[float]:
    s = (s or "").strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def has_value(row: dict, key: str) -> bool:
    return (row.get(key) or "").strip() != ""


def row_selected(row: dict) -> bool:
    if ROW_MODE == "async":
        return has_value(row, "async_pct")
    if ROW_MODE == "vtune":
        return has_value(row, "vtune_pct")
    if ROW_MODE == "jfr":
        return has_value(row, "jfr_pct")
    if ROW_MODE == "any":
        return True
    raise SystemExit(f"[ERROR] Unknown ROW_MODE: {ROW_MODE!r}")


def load_rows(path: Path) -> List[dict]:
    if not path.is_file():
        raise SystemExit(f"[ERROR] Condensed CSV not found: {path}")

    out: List[dict] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            if not row:
                continue
            if all(((v or "").strip() == "") for v in row.values()):
                continue
            out.append(row)
    return out


def main() -> None:
    rows = load_rows(CONDENSED_CSV)

    by_bench: Dict[str, List[float]] = {}
    all_shares: List[float] = []

    skipped_not_selected = 0
    skipped_no_share = 0

    for row in rows:
        if not row_selected(row):
            skipped_not_selected += 1
            continue

        bench = (row.get("benchmark") or "").strip()
        if bench == "":
            continue

        share = safe_float(row.get("slow_run_share_pct", ""))
        if share is None:
            skipped_no_share += 1
            continue

        by_bench.setdefault(bench, []).append(share)
        all_shares.append(share)

    print(f"Using ROW_MODE={ROW_MODE!r}")
    print(f"Rows total: {len(rows)}")
    print(f"Rows skipped (not selected): {skipped_not_selected}")
    print(f"Rows skipped (missing share): {skipped_no_share}")

    if not by_bench:
        print("[WARN] No usable rows found after filtering.")
        return

    print("\n=== Per-benchmark slow_run_share_pct stats ===")
    bench_medians: List[float] = []
    bench_totals: List[float] = []

    for bench in sorted(by_bench.keys()):
        vals = by_bench[bench]
        if not vals:
            continue

        mn = min(vals)
        md = statistics.median(vals)
        mx = max(vals)
        total = sum(vals)

        bench_medians.append(md)
        bench_totals.append(total)

        print(
            f"{bench}: n={len(vals)}  "
            f"min={mn:.6f}%  median={md:.6f}%  max={mx:.6f}%  "
            f"total={total:.6f}%"
        )

    print("\n=== Across all selected rows ===")
    print(
        f"ALL: n={len(all_shares)}  "
        f"min={min(all_shares):.6f}%  median={statistics.median(all_shares):.6f}%  max={max(all_shares):.6f}%"
    )

    print("\n=== Per-benchmark aggregates ===")
    print(
        f"Median of benchmark medians: {statistics.median(bench_medians):.6f}%  "
        f"(min={min(bench_medians):.6f}%, max={max(bench_medians):.6f}%)"
    )
    print(
        f"Benchmark totals (sum) median: {statistics.median(bench_totals):.6f}%  "
        f"(min={min(bench_totals):.6f}%, max={max(bench_totals):.6f}%)"
    )


if __name__ == "__main__":
    main()