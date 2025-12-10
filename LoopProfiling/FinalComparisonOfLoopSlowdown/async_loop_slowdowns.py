#!/usr/bin/env python3
import os
import re
import csv
from collections import defaultdict
from typing import Dict, Tuple, List, Optional

import matplotlib.pyplot as plt

# --------------------------------------------------------------------
# Config
# --------------------------------------------------------------------

RUNTIME_CSV = "runtime_overheads.csv"
ASYNC_BASE_DIR = "Data/AsyncJfrSlowdownRuns_WithFullSetDebug"
OUTPUT_CSV_DIR = "AsyncLoopCSVs"
OUTPUT_PLOT_DIR = "AsyncLoopPlots"

# Only show loops that take at least this much of slowdown runtime (%)
RUNTIME_SHARE_THRESHOLD = 2.0  # percent

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------


def load_runtime_overheads(csv_path: str) -> Dict[str, float]:
    """
    Load runtime_overheads.csv and return:
        { benchmark -> slowdown_noBubo_pct }
    which is the program-level slowdown (no Bubo).
    """
    overheads = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bm = row["benchmark"]
            # Use the "slowdown_noBubo_pct" column you showed.
            slowdown_pct = float(row["slowdown_noBubo_pct"])
            overheads[bm] = slowdown_pct
    return overheads


_marker_re = re.compile(r"BuboAgentCompilerMarkers\.Marker(\d+)")
_delim_re = re.compile(r"BuboAgentCompilerMarkers\.MarkerDelimiter")


def decode_comp_loop_from_stack(frames: List[str]) -> Optional[Tuple[int, int]]:
    """
    Given a list of frame names (top of stack first), decode (comp_id, loop_id)
    from the Bubo marker prefix.

    IMPORTANT: markers BEFORE the delimiter encode the **loop_id**,
               markers AFTER the delimiter encode the **comp_id**.
    """
    pre_digits: List[int] = []   # loop id
    post_digits: List[int] = []  # comp id
    seen_delim = False

    for fn in frames:
        if _delim_re.search(fn):
            seen_delim = True
            continue

        m = _marker_re.search(fn)
        if not m:
            # Once we've started seeing markers, stop when we hit non-marker.
            if seen_delim or pre_digits:
                break
            else:
                continue

        digit = int(m.group(1))
        if not seen_delim:
            pre_digits.append(digit)
        else:
            post_digits.append(digit)

    if not pre_digits or not post_digits:
        return None

    loop_id = int("".join(str(d) for d in pre_digits))
    comp_id = int("".join(str(d) for d in post_digits))

    # NOTE: return order is (comp_id, loop_id)
    return comp_id, loop_id



def parse_async_report(path: str) -> Tuple[int, Dict[Tuple[int, int], int]]:
    """
    Parse an async-profiler GTAssignDebug text report and return:
        total_samples, { (comp_id, loop_id) -> samples }

    We assume blocks like:

        --- Execution profile ---
        Total samples       : 7520211
        skipped             : ...

        --- 66968961080 ns (88.61%), 6667886 samples
          [ 0] BuboAgentCompilerMarkers.Marker1
          [ 1] BuboAgentCompilerMarkers.MarkerDelimiter
          [ 2] BuboAgentCompilerMarkers.Marker3
          [ 3] BuboAgentCompilerMarkers.Marker7
          [ 4] BuboAgentCompilerMarkers.Marker1
          [ 5] Benchmark.innerBenchmarkLoop
          ...

    We:
        - read "Total samples" at the top
        - for each block, read sample count, grab the stack frames,
          decode markers into (comp_id, loop_id) and accumulate samples.
    """
    with open(path, "r") as f:
        lines = f.readlines()

    total_samples = 0
    loop_samples: Dict[Tuple[int, int], int] = defaultdict(int)

    # First, find the "Total samples" line
    for line in lines:
        m = re.match(r"\s*Total samples\s*:\s*(\d+)", line)
        if m:
            total_samples = int(m.group(1))
            break

    # Now parse blocks
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # Block header
        # Example: --- 66968961080 ns (88.61%), 6667886 samples
        m = re.match(r"^---\s+.*,\s*(\d+)\s+samples", line)
        if m:
            samples = int(m.group(1))
            i += 1

            frames: List[str] = []
            # Collect subsequent "[ i ] frame" lines until blank or next '---'
            while i < n:
                l2 = lines[i]
                if l2.strip() == "" or l2.startswith("---"):
                    break
                fm = re.match(r"\s*\[\s*\d+\]\s+(.+)", l2)
                if fm:
                    frames.append(fm.group(1).strip())
                i += 1

            key = decode_comp_loop_from_stack(frames)
            if key is not None:
                loop_samples[key] += samples

            # Don't forget NOT to skip the '---' of the next block if we broke on it.
            continue

        i += 1

    return total_samples, loop_samples


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


# --------------------------------------------------------------------
# Main analysis
# --------------------------------------------------------------------


def analyse_benchmark(
    benchmark: str,
    runtime_overheads: Dict[str, float],
) -> None:
    """
    For a single benchmark:
      - Read noSlow + slowdown async GTAssignDebug reports.
      - Compute per-loop slowdown vs baseline.
      - Filter by runtime share threshold.
      - Write CSV.
      - Create PNG plot.
    """
    bm_dir = os.path.join(ASYNC_BASE_DIR, benchmark)

    no_slow_txt = os.path.join(bm_dir, f"{benchmark}_noSlow_GTAssignDebug.txt")
    slow_txt = os.path.join(bm_dir, f"{benchmark}_slowdown_GTAssignDebug.txt")

    if not (os.path.isfile(no_slow_txt) and os.path.isfile(slow_txt)):
        print(f"[{benchmark}] Missing async reports, skipping.")
        return

    total_base, base_loops = parse_async_report(no_slow_txt)
    total_slow, slow_loops = parse_async_report(slow_txt)

    if total_base == 0 or total_slow == 0:
        print(f"[{benchmark}] Zero total samples, skipping.")
        return

    prog_slowdown_pct = runtime_overheads.get(benchmark)
    if prog_slowdown_pct is None:
        print(f"[{benchmark}] No runtime slowdown entry in {RUNTIME_CSV}, skipping.")
        return

    # Merge keys
    all_keys = sorted(set(base_loops.keys()) | set(slow_loops.keys()))

    rows = []
    for comp_id, loop_id in all_keys:
        b = base_loops.get((comp_id, loop_id), 0)
        s = slow_loops.get((comp_id, loop_id), 0)
        if b == 0:
            # Can't compute relative slowdown; skip for this plot/CSV.
            continue

        slowdown_pct = (s - b) / b * 100.0
        runtime_share_pct = s / total_slow * 100.0

        rows.append(
            {
                "benchmark": benchmark,
                "comp_id": comp_id,
                "loop_id": loop_id,
                "baseline_samples": b,
                "slowdown_samples": s,
                "slowdown_pct": slowdown_pct,
                "runtime_share_pct": runtime_share_pct,
                "total_samples_slowdown": total_slow,
                "prog_slowdown_noBubo_pct": prog_slowdown_pct,
            }
        )

    if not rows:
        print(f"[{benchmark}] No loops with baseline samples > 0, skipping.")
        return

    # Filter loops by threshold
    filtered = [
        r for r in rows if r["runtime_share_pct"] >= RUNTIME_SHARE_THRESHOLD
    ]
    if not filtered:
        # If nothing is big enough, take the top 10 by runtime share
        filtered = sorted(rows, key=lambda r: r["runtime_share_pct"], reverse=True)[:10]

    # Always order by runtime share (descending) for plotting
    filtered = sorted(filtered, key=lambda r: r["runtime_share_pct"], reverse=True)


    # ----------------------------------------------------------------
    # CSV output
    # ----------------------------------------------------------------
    ensure_dir(OUTPUT_CSV_DIR)
    csv_path = os.path.join(OUTPUT_CSV_DIR, f"AsyncLoops_{benchmark}.csv")
    fieldnames = list(filtered[0].keys())
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filtered)
    print(f"[{benchmark}] CSV written -> {csv_path}")

    # ----------------------------------------------------------------
    # Plot
    # ----------------------------------------------------------------
    ensure_dir(OUTPUT_PLOT_DIR)


    labels = [
        f"C{r['comp_id']}-L{r['loop_id']}\n{r['runtime_share_pct']:.2f}%"
        for r in filtered
    ]
    values = [r["slowdown_pct"] for r in filtered]


    plt.figure(figsize=(12, 6))
    x = range(len(filtered))
    plt.bar(x, values)

    # Horizontal dashed line for program slowdown (from noBubo harness)
    plt.axhline(
        y=prog_slowdown_pct,
        linestyle="--",
        linewidth=1.5,
    )
    plt.text(
        0,
        prog_slowdown_pct,
        f"  slowdown {prog_slowdown_pct:.1f}%",
        va="bottom",
        fontsize=9,
    )

    plt.xticks(x, labels, rotation=45, ha="right")
    plt.ylabel("Slowdown vs baseline (noSlow) [%]")
    plt.title(f"{benchmark} – Async per-loop slowdown (baseline = noSlow)")

    plt.tight_layout()
    plot_path = os.path.join(OUTPUT_PLOT_DIR, f"AsyncLoops_{benchmark}.png")
    plt.savefig(plot_path, dpi=200)
    plt.close()

    print(f"[{benchmark}] Plot written -> {plot_path}")


def main():
    if not os.path.isfile(RUNTIME_CSV):
        raise SystemExit(f"Cannot find {RUNTIME_CSV} in current directory.")

    runtime_overheads = load_runtime_overheads(RUNTIME_CSV)

    if not os.path.isdir(ASYNC_BASE_DIR):
        raise SystemExit(f"Cannot find async base dir: {ASYNC_BASE_DIR}")

    benchmarks = sorted(
        d
        for d in os.listdir(ASYNC_BASE_DIR)
        if os.path.isdir(os.path.join(ASYNC_BASE_DIR, d))
    )

    print("Benchmarks found:", ", ".join(benchmarks))

    for bm in benchmarks:
        analyse_benchmark(bm, runtime_overheads)


if __name__ == "__main__":
    main()
