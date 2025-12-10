#!/usr/bin/env python3
import os
import csv
import re
import subprocess
from collections import defaultdict
from typing import Dict, Tuple, List, Optional

import matplotlib.pyplot as plt

# --------------------------------------------------------------------
# Config
# --------------------------------------------------------------------

RUNTIME_CSV = "runtime_overheads.csv"
JFR_BASE_DIR = "Data/AsyncJfrSlowdownRuns_WithFullSetDebug"

OUTPUT_CSV_DIR = "JfrLoopCSVs"
OUTPUT_PLOT_DIR = "JfrLoopPlots"

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
            slowdown_pct = float(row["slowdown_noBubo_pct"])
            overheads[bm] = slowdown_pct
    return overheads


_marker_re = re.compile(r"BuboAgentCompilerMarkers\.Marker(\d+)")
_delim_re = re.compile(r"BuboAgentCompilerMarkers\.MarkerDelimiter")


def decode_comp_loop_from_stack(frames: List[str]) -> Optional[Tuple[int, int]]:
    """
    Given a list of frame names (top-of-stack first), decode (comp_id, loop_id)
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

    # Return in (comp_id, loop_id) order
    return comp_id, loop_id


def jfr_print_exec_samples(jfr_path: str) -> List[str]:
    """
    Run `jfr print --events jdk.ExecutionSample <file>` and return stdout lines.
    """
    proc = subprocess.run(
        ["jfr", "print", "--events", "jdk.ExecutionSample", jfr_path],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.splitlines()


def parse_jfr_report(jfr_path: str) -> Tuple[int, Dict[Tuple[int, int], int]]:
    """
    Parse a JFR file by running `jfr print` and scanning jdk.ExecutionSample blocks.

    We treat each jdk.ExecutionSample's stackTrace block as ONE sample
    with weight 1. For each sample, we decode markers into (comp_id, loop_id).

    Returns:
        total_samples, { (comp_id, loop_id) -> samples }
    """
    if not os.path.isfile(jfr_path):
        raise FileNotFoundError(jfr_path)

    lines = jfr_print_exec_samples(jfr_path)

    total_samples = 0
    loop_samples: Dict[Tuple[int, int], int] = defaultdict(int)

    in_stack = False
    current_stack: List[str] = []

    def flush_stack():
        nonlocal total_samples, current_stack
        if not current_stack:
            return
        key = decode_comp_loop_from_stack(current_stack)
        if key is not None:
            loop_samples[key] += 1
            total_samples += 1
        current_stack = []

    for line in lines:
        s = line.strip()

        # Start of a new ExecutionSample – flush any previous stack
        if s.startswith("jdk.ExecutionSample {"):
            flush_stack()
            in_stack = False
            continue

        # Start of stackTrace block
        if "stackTrace = [" in s:
            in_stack = True
            current_stack = []
            continue

        if in_stack:
            # End of stackTrace block
            if s.startswith("]"):
                flush_stack()
                in_stack = False
                continue

            # Stack frame line, e.g.
            #   BuboAgentCompilerMarkers.Marker2()
            #   Mandelbrot.mandelbrot(int)
            #   ...
            if s:
                frame = s.rstrip(",")
                current_stack.append(frame)
            continue

        # Outside stackTrace: ignore other lines

    # Flush any leftover stack at EOF
    flush_stack()

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
      - Read noSlow + slowdown JFR files.
      - Compute per-loop slowdown vs baseline (samples).
      - Filter by runtime share threshold.
      - Write CSV.
      - Create PNG plot.
    """
    bm_dir = os.path.join(JFR_BASE_DIR, benchmark)

    no_slow_jfr = os.path.join(bm_dir, f"{benchmark}_noSlow.jfr")
    slow_jfr = os.path.join(bm_dir, f"{benchmark}_slowdown.jfr")

    if not (os.path.isfile(no_slow_jfr) and os.path.isfile(slow_jfr)):
        print(f"[{benchmark}] Missing JFR files, skipping.")
        return

    total_base, base_loops = parse_jfr_report(no_slow_jfr)
    total_slow, slow_loops = parse_jfr_report(slow_jfr)

    if total_base == 0 or total_slow == 0:
        print(f"[{benchmark}] Zero total JFR samples, skipping.")
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
            # Can't compute relative slowdown; skip.
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
    csv_path = os.path.join(OUTPUT_CSV_DIR, f"JfrLoops_{benchmark}.csv")
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
    plt.ylabel("Slowdown vs baseline (noSlow JFR) [%]")
    plt.title(f"{benchmark} – JFR per-loop slowdown (baseline = noSlow)")

    plt.tight_layout()
    plot_path = os.path.join(OUTPUT_PLOT_DIR, f"JfrLoops_{benchmark}.png")
    plt.savefig(plot_path, dpi=200)
    plt.close()

    print(f"[{benchmark}] Plot written -> {plot_path}")


def main():
    if not os.path.isfile(RUNTIME_CSV):
        raise SystemExit(f"Cannot find {RUNTIME_CSV} in current directory.")

    runtime_overheads = load_runtime_overheads(RUNTIME_CSV)

    if not os.path.isdir(JFR_BASE_DIR):
        raise SystemExit(f"Cannot find JFR base dir: {JFR_BASE_DIR}")

    benchmarks = sorted(
        d
        for d in os.listdir(JFR_BASE_DIR)
        if os.path.isdir(os.path.join(JFR_BASE_DIR, d))
    )

    print("Benchmarks found:", ", ".join(benchmarks))

    for bm in benchmarks:
        analyse_benchmark(bm, runtime_overheads)


if __name__ == "__main__":
    main()
