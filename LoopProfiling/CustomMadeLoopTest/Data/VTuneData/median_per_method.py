#!/usr/bin/env python3
import csv
import re
import statistics
from dataclasses import dataclass
from typing import Dict, List, Tuple
from collections import defaultdict

# --------------------------------------------------------------------
# EDIT THESE TWO PATHS WHENEVER YOU WANT
# --------------------------------------------------------------------
INPUT_FILE = "2025_12_15_10_40_53_LoopBenchmarks_SlowdownTest_TestnoemitOps.txt"
OUTPUT_CSV = "medians_95pct_weighted.csv"

# Keep blocks until they cover this fraction of total NORMAL time per method
COVERAGE_FRACTION = 0.90

# If a method has total normal time <= this, we treat it as "too tiny/noisy"
# and just fall back to using all blocks (or you can change behaviour below).
MIN_TOTAL_NORMAL_TIME = 0.0  # set e.g. 1e-6 if you want

# --------------------------------------------------------------------

LINE_RE = re.compile(
    r"^Method:\s*(?P<method>.*?),\s*"
    r"Block ID:\s*(?P<block>\d+),\s*"
    r"Normal Time:\s*(?P<normal>-?\d+(?:\.\d+)?),\s*"
    r"Slowdown Time:\s*(?P<slow>-?\d+(?:\.\d+)?),\s*"
    r"Percentage Increase:\s*(?P<pct>-?\d+(?:\.\d+)?)(?:%)?\s*$"
)

@dataclass(frozen=True)
class BlockRow:
    block_id: int
    normal_time: float
    slowdown_time: float
    pct_increase: float


def parse_file(path: str) -> Tuple[Dict[str, List[BlockRow]], int, int]:
    method_to_rows: Dict[str, List[BlockRow]] = defaultdict(list)
    matched_lines = 0
    total_lines = 0

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            total_lines += 1
            line = raw.strip()
            m = LINE_RE.match(line)
            if not m:
                continue

            method = m.group("method").strip()
            row = BlockRow(
                block_id=int(m.group("block")),
                normal_time=float(m.group("normal")),
                slowdown_time=float(m.group("slow")),
                pct_increase=float(m.group("pct")),
            )
            method_to_rows[method].append(row)
            matched_lines += 1

    return method_to_rows, matched_lines, total_lines

def clamp_non_negative(x: float) -> float:
    return x if x > 0.0 else 0.0


def select_top_coverage_blocks(rows: List[BlockRow], coverage_fraction: float) -> Tuple[List[BlockRow], float, float]:
    """
    Returns (kept_rows, total_normal, kept_normal)
    kept_rows are the largest-normal-time blocks whose cumulative normal time
    reaches at least coverage_fraction of total_normal.
    """
    total_normal = sum(r.normal_time for r in rows)
    if total_normal <= 0.0:
        # Nothing to weight by; keep everything (median will still work)
        return rows[:], total_normal, total_normal

    # Sort by normal time descending
    sorted_rows = sorted(rows, key=lambda r: r.normal_time, reverse=True)

    target = total_normal * coverage_fraction
    kept: List[BlockRow] = []
    cum = 0.0
    for r in sorted_rows:
        kept.append(r)
        cum += r.normal_time
        if cum >= target:
            break

    return kept, total_normal, cum


def median(xs: List[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def main() -> None:
    method_to_rows, matched, total = parse_file(INPUT_FILE)

    out_rows = []
    print(f"Read {total} lines, matched {matched} per-block lines.")
    print(f"Found {len(method_to_rows)} methods.\n")

    for method, rows in sorted(method_to_rows.items(), key=lambda kv: -len(kv[1])):
        total_blocks = len(rows)
        total_normal = sum(r.normal_time for r in rows)

        if total_blocks == 0:
            continue

        # Coverage-based selection
        if total_normal <= MIN_TOTAL_NORMAL_TIME:
            kept = rows[:]
            kept_normal = total_normal
        else:
            kept, total_normal2, kept_normal = select_top_coverage_blocks(rows, COVERAGE_FRACTION)
            # total_normal2 == total_normal, but keep it clear
            total_normal = total_normal2

        all_median = median([clamp_non_negative(r.pct_increase) for r in rows])
        kept_median = median([clamp_non_negative(r.pct_increase) for r in kept])

        kept_blocks = len(kept)
        kept_fraction = (kept_normal / total_normal) if total_normal > 0 else 1.0

        out_rows.append((
            method,
            total_blocks,
            kept_blocks,
            total_normal,
            kept_normal,
            kept_fraction,
            all_median,
            kept_median,
        ))

    # Sort output by kept_normal (where the time actually is), descending
    out_rows.sort(key=lambda r: r[4], reverse=True)

    # Print summary
    for (method, total_blocks, kept_blocks, total_normal, kept_normal, kept_fraction, all_med, kept_med) in out_rows:
        print(
            f"{method} | blocks={total_blocks} | kept={kept_blocks} "
            f"| normal_total={total_normal:.6g} | kept_normal={kept_normal:.6g} ({kept_fraction*100:.2f}%) "
            f"| median_all={all_med:.6g} | median_95pct={kept_med:.6g}"
        )

    # Write CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Method",
            "TotalBlocks",
            "KeptBlocks_95pctNormal",
            "TotalNormalTime",
            "KeptNormalTime",
            "KeptNormalFraction",
            "MedianPct_AllBlocks",
            "MedianPct_95pctNormalBlocks",
        ])
        for r in out_rows:
            w.writerow(r)

    print(f"\nWrote CSV: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
