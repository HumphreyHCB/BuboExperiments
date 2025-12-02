#!/usr/bin/env python3

"""
ThirdTest_SimRuns: Bubo vs Async per (CompId, LoopId) comparison,
plus overall runtime overhead from AWFY harness output.

Expected layout (run this script from LoopProfiling/ThirdTest_SimRuns):

  Bounce/
    Bounce_LIR_false_GTAssignDebug.txt   # Async text, baseline (no slowdown)
    Bounce_LIR_true_GTAssignDebug.txt    # Async text, slowdown run
    Bounce_LIR_false.out                 # Bubo + harness output, baseline
    Bounce_LIR_true.out                  # Bubo + harness output, slowdown

  Mandelbrot/
    Mandelbrot_LIR_false_GTAssignDebug.txt
    Mandelbrot_LIR_true_GTAssignDebug.txt
    Mandelbrot_LIR_false.out
    Mandelbrot_LIR_true.out
  ...

For each benchmark directory, this script:

  1. Uses the baseline pair:
       - <Bench>_LIR_false.out               -> Bubo loops (baseline)
       - <Bench>_LIR_false_GTAssignDebug.txt -> Async text report (baseline)

     to compute per-(CompId, LoopId):

       - BuboExclusiveCyclesBaseline
       - BuboSharePercentBaseline      = share of total baseline Bubo exclusive cycles
       - AsyncSamplesBaseline
       - AsyncSharePercentBaseline     = share of total baseline Async samples

  2. Uses the slowdown pair:
       - <Bench>_LIR_true.out
       - <Bench>_LIR_true_GTAssignDebug.txt

     to compute per-(CompId, LoopId):

       - BuboExclusiveCyclesSlowdown
       - BuboSharePercentSlowdown      = share of total slowdown Bubo exclusive cycles
       - AsyncSamplesSlowdown
       - AsyncSharePercentSlowdown     = share of total slowdown Async samples

  3. Uses BOTH .out files:

       - <Bench>_LIR_false.out
       - <Bench>_LIR_true.out

     to extract the final harness line of the form:

       "<BenchName>: iterations=... average: XXXus total: YYYus"

     and computes a single runtime overhead:

       overhead_percent = (avg_true - avg_false) / avg_false * 100

  4. Sorts loops by BuboSharePercentBaseline descending and keeps only the most
     significant loops:
       - up to MAX_LOOPS
       - or until cumulative baseline Bubo share >= COVERAGE_THRESHOLD (%)

  5. Writes:
       <Bench>_BuboAsync_CompLoopShares.csv
       <Bench>_BuboAsync_CompLoopShares.png
     into the current directory (ThirdTest_SimRuns).

The CSV has, per (CompId, LoopId):

  CompId, LoopId, Method,
  BuboExclusiveCyclesBaseline,  BuboSharePercentBaseline,
  BuboExclusiveCyclesSlowdown,  BuboSharePercentSlowdown,
  AsyncSamplesBaseline,         AsyncSharePercentBaseline,
  AsyncSamplesSlowdown,         AsyncSharePercentSlowdown,
  BaselineAvgUs, SlowdownAvgUs, OverheadPercent

Plot:

  - For each (CompId, LoopId) (x-axis group), four bars:

       1) Bubo baseline   (exclusive %, no slowdown)
       2) Bubo slowdown   (exclusive %, slowdown)
       3) Async baseline  (samples %, no slowdown)
       4) Async slowdown  (samples %, slowdown)

  - Tick label:
       line 1: "B xx.x% / A yy.y%"   (baseline shares)
       line 2: "C<comp>-L<loop>"

  - Title:
       "<Bench> – Bubo vs Async per Comp/Loop
        (baseline vs slowdown, overhead +ZZ.Z%)"
"""

from pathlib import Path
import re
import csv

import matplotlib.pyplot as plt


# -----------------------------------------------------------------------------
# Bubo (BuboSlowdown part inside *.out) parsing – inclusive loops + parents
# -----------------------------------------------------------------------------


TOTAL_RUNTIME_RE = re.compile(r"Total Runtime:\s+(\d+)us")
COMP_RE = re.compile(r"^Comp\s+(\d+)\s+\((.+?)\)\s+loops:")
LOOP_RE = re.compile(r"loop\s+(\d+)\s+Cycles:\s+(\d+)")
ENCODING_RE = re.compile(r"Found Encoding\s*:\s*(.*)")

# Harness summary line, e.g.:
# Richards: iterations=500 average: 195888us total: 97944274us
HARNESS_AVG_RE = re.compile(
    r"average:\s+(\d+)us\s+total:\s+(\d+)us"
)


def parse_bubo_file(path):
    """
    Parse <Bench>_LIR_false.out-like file (or LIR_true.out).

    Returns:
      total_runtime_us: int or None
      loops:   dict[(comp_id, method_name, loop_id)] = inclusive_cycles
      parents: dict[(comp_id, loop_id)] = parent_loop_id or None
    """
    total_runtime = None
    loops = {}
    parents = {}

    current_comp_id = None
    current_method = None

    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")

            m = TOTAL_RUNTIME_RE.search(line)
            if m:
                try:
                    total_runtime = int(m.group(1))
                except ValueError:
                    pass
                continue

            m = COMP_RE.match(line)
            if m:
                current_comp_id = int(m.group(1))
                current_method = m.group(2).strip()
                continue

            m = ENCODING_RE.search(line)
            if m and current_comp_id is not None:
                enc_str = m.group(1).strip()
                if enc_str:
                    parts = enc_str.split(",")
                    for p in parts:
                        p = p.strip()
                        if not p or ":" not in p:
                            continue
                        lid_str, pid_str = p.split(":", 1)
                        try:
                            lid = int(lid_str)
                            pid = int(pid_str)
                        except ValueError:
                            continue
                        parent = None if pid < 0 else pid
                        parents[(current_comp_id, lid)] = parent
                continue

            if current_comp_id is not None:
                m = LOOP_RE.search(line)
                if m:
                    loop_id = int(m.group(1))
                    cycles = int(m.group(2))
                    loops[(current_comp_id, current_method, loop_id)] = cycles
                    continue

            if not line.strip():
                current_comp_id = None
                current_method = None

    return total_runtime, loops, parents


def compute_exclusive_cycles(loops, parents):
    """
    Given:
      loops:   dict[(comp_id, method, loop_id)] = inclusive_cycles
      parents: dict[(comp_id, loop_id)] = parent_loop_id or None

    Return:
      exclusive: dict[(comp_id, method, loop_id)] = exclusive_cycles
    """
    # Map (comp_id, loop_id) -> (comp_id, method, loop_id)
    key_by_id = {}
    for (comp_id, method, loop_id) in loops.keys():
        key_by_id[(comp_id, loop_id)] = (comp_id, method, loop_id)

    # Build children map from parents
    children = {}  # (comp_id, parent_loop_id) -> [child_loop_id,...]
    for (comp_id, loop_id), parent in parents.items():
        if parent is None:
            continue
        children.setdefault((comp_id, parent), []).append(loop_id)

    exclusive = {}

    for (comp_id, method, loop_id), inclusive in loops.items():
        child_ids = children.get((comp_id, loop_id), [])
        child_sum = 0
        for cid in child_ids:
            child_key = key_by_id.get((comp_id, cid))
            if child_key is not None:
                child_sum += loops.get(child_key, 0)

        excl = inclusive - child_sum
        if excl < 0:
            excl = 0
        exclusive[(comp_id, method, loop_id)] = excl

    return exclusive


def extract_average_runtime_us(path: Path):
    """
    From a *.out file, find the last line containing
      'average: XXXus total: YYYus'
    and return XXX as int, or None if not found.
    """
    avg = None
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = HARNESS_AVG_RE.search(line)
            if m:
                try:
                    avg = int(m.group(1))
                except ValueError:
                    continue
    return avg


# -----------------------------------------------------------------------------
# Async (LIR_*_GTAssignDebug.txt) parsing – using marker layout
# -----------------------------------------------------------------------------

TOTAL_SAMPLES_RE = re.compile(r"^Total samples\s*:\s*(\d+)")
BLOCK_HEADER_RE = re.compile(
    r"^---\s+(\d+)\s+ns\s+\(([0-9.]+)%\),\s+(\d+)\s+samples"
)
FRAME_RE = re.compile(r"^\s*\[\s*\d+\s*\]\s+(.*)$")

MARKER_DELIM = "BuboAgentCompilerMarkers.MarkerDelimiter"
MARKER_RE = re.compile(r"BuboAgentCompilerMarkers\.Marker(\d+)\b")


def parse_total_samples(lines):
    for l in lines:
        m = TOTAL_SAMPLES_RE.match(l)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def iter_blocks(lines):
    """Yield (header_line, frame_lines)."""
    i = 0
    n = len(lines)
    while i < n:
        m = BLOCK_HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue

        header = lines[i].rstrip("\n")
        i += 1
        frames = []
        while i < n and not lines[i].startswith("--- "):
            if FRAME_RE.match(lines[i]):
                frames.append(lines[i].rstrip("\n"))
            i += 1

        yield header, frames


def parse_block_samples(header_line):
    m = BLOCK_HEADER_RE.match(header_line)
    return int(m.group(3))  # sample count


def extract_marker_ids(frame_lines):
    """
    Extract (comp_id, loop_id) from stack frames with our marker layout:

      [0] ... Marker<digit> ...
      [1] ... MarkerDelimiter ...
      [2] ... Marker<digit> ...
      [3] ... Marker<digit> ...
      ...

    - Loop ID = marker immediately BEFORE the delimiter.
    - Comp ID digits = markers AFTER delimiter, concatenated and reversed.
    """
    funcs = []
    for fl in frame_lines:
        m = FRAME_RE.match(fl)
        if m:
            funcs.append(m.group(1))

    try:
        d = funcs.index(MARKER_DELIM)
    except ValueError:
        return None

    # loop id = marker before delimiter
    loop_id = None
    if d > 0:
        b = funcs[d - 1]
        mb = MARKER_RE.search(b)
        if mb:
            loop_id = int(mb.group(1))
    if loop_id is None:
        return None

    # comp id digits = markers after delimiter
    digits = []
    for f in funcs[d + 1 :]:
        mm = MARKER_RE.search(f)
        if not mm:
            break
        digits.append(mm.group(1))
    if not digits:
        return None

    comp_id = int("".join(reversed(digits)))
    return (comp_id, loop_id)


def parse_async_marker_file(path):
    """
    Parse <Bench>_LIR_*_GTAssignDebug.txt-like file.

    Returns:
      total_samples: int or None
      results: dict[(comp_id, loop_id)] = samples
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    total = parse_total_samples(lines)
    results = {}

    for hdr, frames in iter_blocks(lines):
        block_samples = parse_block_samples(hdr)
        ids = extract_marker_ids(frames)
        if ids is None:
            continue
        results[ids] = results.get(ids, 0) + block_samples

    return total, results


# -----------------------------------------------------------------------------
# Plotting + CSV
# -----------------------------------------------------------------------------

MAX_LOOPS = 40
COVERAGE_THRESHOLD = 99.0  # percent of total Bubo exclusive baseline


def create_benchmark_plot(
    bench_name,
    rows,
    out_png: Path,
    overhead_percent=None,
):
    """
    rows: list of dicts with keys:
      CompId, LoopId,
      BuboExclusiveCyclesBaseline,  BuboSharePercentBaseline,
      BuboExclusiveCyclesSlowdown,  BuboSharePercentSlowdown,
      AsyncSamplesBaseline,         AsyncSharePercentBaseline,
      AsyncSamplesSlowdown,         AsyncSharePercentSlowdown,
      BaselineAvgUs, SlowdownAvgUs, OverheadPercent
    """
    if not rows:
        print(f"  [WARN] No loops to plot for {bench_name}")
        return

    k = len(rows)
    x = list(range(k))
    width = 0.18  # 4 bars per group

    bubo_base_shares = [r["BuboSharePercentBaseline"] for r in rows]
    bubo_slow_shares = [r["BuboSharePercentSlowdown"] for r in rows]
    async_base_shares = [r["AsyncSharePercentBaseline"] for r in rows]
    async_slow_shares = [r["AsyncSharePercentSlowdown"] for r in rows]

    # Coverage based on baseline only
    bubo_covered = sum(bubo_base_shares)
    async_covered = sum(async_base_shares)

    fig_width = max(12, k * 0.7)
    fig, ax = plt.subplots(figsize=(fig_width, 6))

    # Colors (baseline vs slowdown)
    bubo_color_base = "tab:blue"
    bubo_color_slow = "lightskyblue"
    async_color_base = "tab:green"
    async_color_slow = "lightgreen"

    # x positions for 4 bars
    x_bubo_base = [i - 1.5 * width for i in x]
    x_bubo_slow = [i - 0.5 * width for i in x]
    x_async_base = [i + 0.5 * width for i in x]
    x_async_slow = [i + 1.5 * width for i in x]

    ax.bar(
        x_bubo_base,
        bubo_base_shares,
        width=width,
        label="Bubo baseline (exclusive %, no slowdown)",
        color=bubo_color_base,
    )
    ax.bar(
        x_bubo_slow,
        bubo_slow_shares,
        width=width,
        label="Bubo slowdown (exclusive %, slowdown)",
        color=bubo_color_slow,
    )
    ax.bar(
        x_async_base,
        async_base_shares,
        width=width,
        label="Async baseline (samples %, no slowdown)",
        color=async_color_base,
    )
    ax.bar(
        x_async_slow,
        async_slow_shares,
        width=width,
        label="Async slowdown (samples %, slowdown)",
        color=async_color_slow,
    )

    # X tick labels (show baseline shares in label for readability)
    labels = []
    for r in rows:
        b_share = r["BuboSharePercentBaseline"]
        a_share = r["AsyncSharePercentBaseline"]
        comp = r["CompId"]
        loop = r["LoopId"]

        line1 = f"B {b_share:.1f}% / A {a_share:.1f}%"
        line2 = f"C{comp}-L{loop}"
        labels.append(line1 + "\n" + line2)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    for lbl in ax.get_xticklabels():
        lbl.set_fontsize(8)

    ax.set_ylabel("Share of total [%] (baseline vs slowdown)")
    ax.set_xlabel("Compilation unit + loop id (4 bars: Bubo/Async × baseline/slowdown)")

    overhead_str = ""
    if overhead_percent is not None:
        overhead_str = f", runtime overhead {overhead_percent:+.1f}% (LIR_true vs LIR_false)"

    title = (
        f"{bench_name} – Bubo vs Async per Comp/Loop\n"
        f"(Baseline Bubo cover {bubo_covered:.1f}%, "
        f"baseline Async cover {async_covered:.1f}%{overhead_str})"
    )
    ax.set_title(title)

    ax.legend()
    fig.tight_layout()
    fig.savefig(out_png, dpi=200)
    plt.close(fig)

    print(f"  -> wrote plot: {out_png.name}")


# -----------------------------------------------------------------------------
# Per-benchmark compare
# -----------------------------------------------------------------------------

def analyze_benchmark(bench_dir: Path, root_out: Path):
    bench_name = bench_dir.name
    print(f"[INFO] Benchmark: {bench_name}")

    # File names
    bubo_baseline_path = bench_dir / f"{bench_name}_LIR_false.out"
    bubo_slowdown_path = bench_dir / f"{bench_name}_LIR_true.out"
    async_baseline_path = bench_dir / f"{bench_name}_LIR_false_GTAssignDebug.txt"
    async_slowdown_path = bench_dir / f"{bench_name}_LIR_true_GTAssignDebug.txt"

    # Check presence
    if not bubo_baseline_path.exists():
        print(f"  [WARN] Missing {bubo_baseline_path.name}, skipping.")
        return
    if not async_baseline_path.exists():
        print(f"  [WARN] Missing {async_baseline_path.name}, skipping.")
        return
    if not bubo_slowdown_path.exists():
        print(f"  [WARN] Missing {bubo_slowdown_path.name}, slowdown Bubo data will be zero.")
    if not async_slowdown_path.exists():
        print(f"  [WARN] Missing {async_slowdown_path.name}, slowdown Async data will be zero.")

    # -------------------------------------------------------------------------
    # 1) Overall runtime overhead from harness averages (baseline vs slowdown)
    # -------------------------------------------------------------------------
    baseline_avg = extract_average_runtime_us(bubo_baseline_path)
    slowdown_avg = extract_average_runtime_us(bubo_slowdown_path) if bubo_slowdown_path.exists() else None

    overhead_percent = None
    if baseline_avg is not None and slowdown_avg is not None and baseline_avg > 0:
        overhead_percent = (slowdown_avg - baseline_avg) / float(baseline_avg) * 100.0
        print(
            f"  [INFO] Runtime averages (us): "
            f"baseline={baseline_avg}, slowdown={slowdown_avg}, "
            f"overhead={overhead_percent:+.2f}%"
        )
    else:
        print("  [WARN] Could not compute overhead (missing or invalid averages).")

    # -------------------------------------------------------------------------
    # 2) Bubo vs Async per (CompId, LoopId) for baseline and slowdown runs
    # -------------------------------------------------------------------------

    # Bubo baseline
    _, loops_base, parents_base = parse_bubo_file(bubo_baseline_path)
    exclusive_base = compute_exclusive_cycles(loops_base, parents_base)
    bubo_baseline_map = {}
    for (comp_id, method, loop_id), cycles in exclusive_base.items():
        bubo_baseline_map[(comp_id, loop_id)] = (method, cycles)

    # Bubo slowdown
    bubo_slow_map = {}
    if bubo_slowdown_path.exists():
        _, loops_slow, parents_slow = parse_bubo_file(bubo_slowdown_path)
        exclusive_slow = compute_exclusive_cycles(loops_slow, parents_slow)
        for (comp_id, method, loop_id), cycles in exclusive_slow.items():
            # method should be the same, but keep baseline's if present
            if (comp_id, loop_id) in bubo_baseline_map:
                base_method, _ = bubo_baseline_map[(comp_id, loop_id)]
                bubo_slow_map[(comp_id, loop_id)] = (base_method, cycles)
            else:
                bubo_slow_map[(comp_id, loop_id)] = (method, cycles)

    # Async baseline
    async_total_base, async_baseline_map = parse_async_marker_file(async_baseline_path)

    # Async slowdown
    async_slow_map = {}
    if async_slowdown_path.exists():
        async_total_slow, async_slow_map = parse_async_marker_file(async_slowdown_path)
    else:
        async_total_slow = None

    if not async_baseline_map:
        print("  [WARN] No async baseline samples found, skipping.")
        return

    # Common keys: loops that both tools see in the baseline
    common_keys = sorted(
        set(bubo_baseline_map.keys()) & set(async_baseline_map.keys()),
        key=lambda k: bubo_baseline_map[k][1],  # sort by baseline Bubo exclusive cycles
        reverse=True,
    )

    if not common_keys:
        print("  [WARN] No common (CompId, LoopId) between Bubo and Async baseline.")
        return

    # Totals restricted to common keys for comparability
    total_bubo_base = sum(bubo_baseline_map[k][1] for k in common_keys)
    total_bubo_slow = sum(bubo_slow_map.get(k, (None, 0))[1] for k in common_keys)
    total_async_base = sum(async_baseline_map[k] for k in common_keys)
    total_async_slow = sum(async_slow_map.get(k, 0) for k in common_keys)

    if total_bubo_base == 0 or total_async_base == 0:
        print("  [WARN] Zero total baseline Bubo or Async, skipping.")
        return

    # Select most significant loops based on baseline Bubo share
    selected_rows = []
    coverage = 0.0

    for i, (comp_id, loop_id) in enumerate(common_keys):
        if i >= MAX_LOOPS:
            break

        # Bubo baseline
        method, b_cycles_base = bubo_baseline_map[(comp_id, loop_id)]
        b_share_base = (b_cycles_base / float(total_bubo_base)) * 100.0 if total_bubo_base > 0 else 0.0

        # Bubo slowdown (may be missing -> treat as 0)
        _, b_cycles_slow = bubo_slow_map.get((comp_id, loop_id), (method, 0))
        b_share_slow = (b_cycles_slow / float(total_bubo_slow)) * 100.0 if total_bubo_slow and total_bubo_slow > 0 else 0.0

        # Async baseline
        a_samples_base = async_baseline_map[(comp_id, loop_id)]
        a_share_base = (a_samples_base / float(total_async_base)) * 100.0 if total_async_base > 0 else 0.0

        # Async slowdown (may be missing -> treat as 0)
        a_samples_slow = async_slow_map.get((comp_id, loop_id), 0)
        a_share_slow = (a_samples_slow / float(total_async_slow)) * 100.0 if total_async_slow and total_async_slow > 0 else 0.0

        row = {
            "CompId": comp_id,
            "LoopId": loop_id,
            "Method": method,
            "BuboExclusiveCyclesBaseline": b_cycles_base,
            "BuboSharePercentBaseline": b_share_base,
            "BuboExclusiveCyclesSlowdown": b_cycles_slow,
            "BuboSharePercentSlowdown": b_share_slow,
            "AsyncSamplesBaseline": a_samples_base,
            "AsyncSharePercentBaseline": a_share_base,
            "AsyncSamplesSlowdown": a_samples_slow,
            "AsyncSharePercentSlowdown": a_share_slow,
            "BaselineAvgUs": baseline_avg,
            "SlowdownAvgUs": slowdown_avg,
            "OverheadPercent": overhead_percent,
        }
        selected_rows.append(row)

        coverage += b_share_base
        if coverage >= COVERAGE_THRESHOLD:
            break

    if not selected_rows:
        print("  [WARN] No rows selected after filtering, skipping.")
        return

    # Write CSV and plot in root_out
    out_csv = root_out / f"{bench_name}_BuboAsync_CompLoopShares.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "CompId",
                "LoopId",
                "Method",
                "BuboExclusiveCyclesBaseline",
                "BuboSharePercentBaseline",
                "BuboExclusiveCyclesSlowdown",
                "BuboSharePercentSlowdown",
                "AsyncSamplesBaseline",
                "AsyncSharePercentBaseline",
                "AsyncSamplesSlowdown",
                "AsyncSharePercentSlowdown",
                "BaselineAvgUs",
                "SlowdownAvgUs",
                "OverheadPercent",
            ],
        )
        w.writeheader()
        for r in selected_rows:
            w.writerow(r)

    print(f"  -> wrote CSV: {out_csv.name}")

    out_png = root_out / f"{bench_name}_BuboAsync_CompLoopShares.png"
    create_benchmark_plot(bench_name, selected_rows, out_png, overhead_percent=overhead_percent)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    root = Path(".").resolve()
    print("[INFO] ThirdTest_SimRuns root:", root)

    for bench_dir in sorted(root.iterdir()):
        if bench_dir.is_dir() and not bench_dir.name.startswith("."):
            analyze_benchmark(bench_dir, root)


if __name__ == "__main__":
    main()
