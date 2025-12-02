#!/usr/bin/env python3

"""
ThirdTest_SimRuns: Bubo vs Async per (CompId, LoopId) comparison,
plus overall runtime overhead from AWFY harness output.

Features:

  - Filters to loops with LoopCallCount == 0 in the Bubo baseline dump.
  - For each (CompId, LoopId), computes PERCENT CHANGE in raw units
    when going from baseline (no slowdown) to slowdown:

      * BuboPercentChange  = (exclusive_cycles_slow - exclusive_cycles_base)
                             / exclusive_cycles_base * 100
      * AsyncPercentChange = (samples_slow - samples_base)
                             / samples_base * 100

  - Overall runtime overhead (from harness averages) is shown as a dotted
    horizontal line in the plot, not in the title.

  - X tick labels show, for the SLOWDOWN run, each loop’s contribution
    according to Bubo and Async:

      line 1: "B xx.x% / A yy.y%"
      line 2: "C<comp>-L<loop>"
"""

from pathlib import Path
import re
import csv

import matplotlib.pyplot as plt


# -----------------------------------------------------------------------------
# Bubo parsing – inclusive loops + parents + LoopCallCount
# -----------------------------------------------------------------------------

TOTAL_RUNTIME_RE = re.compile(r"Total Runtime:\s+(\d+)us")
COMP_RE = re.compile(r"^Comp\s+(\d+)\s+\((.+?)\)\s+loops:")
LOOP_RE = re.compile(r"loop\s+(\d+)\s+Cycles:\s+(\d+)")
LOOP_CALL_RE = re.compile(r"LoopCallCount:\s*(\d+)")
ENCODING_RE = re.compile(r"Found Encoding\s*:\s*(.*)")

HARNESS_AVG_RE = re.compile(
    r"average:\s+(\d+)us\s+total:\s+(\d+)us"
)


def parse_bubo_file(path):
    """
    Parse <Bench>_LIR_false.out-like file (or LIR_true.out).

    Returns:
      total_runtime_us: int or None
      loops:        dict[(comp_id, method_name, loop_id)] = inclusive_cycles
      parents:      dict[(comp_id, loop_id)] = parent_loop_id or None
      loop_calls:   dict[(comp_id, loop_id)] = LoopCallCount (int)
    """
    total_runtime = None
    loops = {}
    parents = {}
    loop_calls = {}

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

                    # LoopCallCount is on the same line
                    call_match = LOOP_CALL_RE.search(line)
                    if call_match:
                        try:
                            call_count = int(call_match.group(1))
                        except ValueError:
                            call_count = 0
                    else:
                        call_count = 0

                    loops[(current_comp_id, current_method, loop_id)] = cycles
                    loop_calls[(current_comp_id, loop_id)] = call_count
                    continue

            if not line.strip():
                current_comp_id = None
                current_method = None

    return total_runtime, loops, parents, loop_calls


def compute_exclusive_cycles(loops, parents):
    """
    Given:
      loops:   dict[(comp_id, method, loop_id)] = inclusive_cycles
      parents: dict[(comp_id, loop_id)] = parent_loop_id or None

    Return:
      exclusive: dict[(comp_id, method, loop_id)] = exclusive_cycles
    """
    key_by_id = {}
    for (comp_id, method, loop_id) in loops.keys():
        key_by_id[(comp_id, loop_id)] = (comp_id, method, loop_id)

    children = {}
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
# Async parsing – using marker layout
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
    for f in funcs[d + 1:]:
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
COVERAGE_THRESHOLD = 99.0  # percent of total baseline Bubo (for selection only)


def create_benchmark_plot(
    bench_name,
    rows,
    out_png: Path,
    overhead_percent=None,
):
    """
    rows: list of dicts with keys (among others):
      CompId, LoopId,
      BuboPercentChange,
      AsyncPercentChange,
      BuboSlowdownSharePercent,
      AsyncSlowdownSharePercent,
      OverheadPercent
    """
    if not rows:
        print(f"  [WARN] No loops to plot for {bench_name}")
        return

    k = len(rows)
    x = list(range(k))
    width = 0.35  # 2 bars per group

    bubo_changes = [r["BuboPercentChange"] for r in rows]
    async_changes = [r["AsyncPercentChange"] for r in rows]

    fig_width = max(10, k * 0.6)
    fig, ax = plt.subplots(figsize=(fig_width, 6))

    x_bubo = [i - width / 2 for i in x]
    x_async = [i + width / 2 for i in x]

    ax.bar(
        x_bubo,
        bubo_changes,
        width=width,
        label="Bubo (exclusive cycles % change)",
        color="tab:blue",
    )
    ax.bar(
        x_async,
        async_changes,
        width=width,
        label="Async (samples % change)",
        color="tab:green",
    )

    # Overall runtime overhead as dotted horizontal line
    if overhead_percent is not None:
        ax.axhline(
            overhead_percent,
            linestyle="--",
            linewidth=1.5,
            color="black",
            label=f"Runtime overhead {overhead_percent:+.1f}%",
        )

    # X tick labels: slowdown shares + C<comp>-L<loop>
    labels = []
    for r in rows:
        comp = r["CompId"]
        loop = r["LoopId"]
        b_slow_share = r.get("BuboSlowdownSharePercent", 0.0)
        a_slow_share = r.get("AsyncSlowdownSharePercent", 0.0)

        line1 = f"B {b_slow_share:.1f}% / A {a_slow_share:.1f}%"
        line2 = f"C{comp}-L{loop}"
        labels.append(line1 + "\n" + line2)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    for lbl in ax.get_xticklabels():
        lbl.set_fontsize(8)
        lbl.set_rotation(45)

    ax.set_ylabel("Percent change vs baseline [%]\n((slowdown - baseline) / baseline × 100)")
    ax.set_xlabel("Compilation unit + loop id\n(label shares use slowdown run)")

    title = f"{bench_name} – Bubo vs Async: percent change in raw units"
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
    # 2) Bubo vs Async per (CompId, LoopId)
    #    Only loops with LoopCallCount == 0 in the baseline are considered.
    # -------------------------------------------------------------------------

    # Bubo baseline
    _, loops_base, parents_base, loop_calls_base = parse_bubo_file(bubo_baseline_path)
    exclusive_base = compute_exclusive_cycles(loops_base, parents_base)

    # Keep only loops with LoopCallCount == 0 in the baseline
    bubo_baseline_map = {}
    for (comp_id, method, loop_id), cycles in exclusive_base.items():
        call_count = loop_calls_base.get((comp_id, loop_id), None)
        # If LoopCallCount is missing, drop it
        if call_count == 0:
            bubo_baseline_map[(comp_id, loop_id)] = (method, cycles)

    if not bubo_baseline_map:
        print("  [WARN] No baseline Bubo loops with LoopCallCount == 0, skipping.")
        return

    # Bubo slowdown
    bubo_slow_map = {}
    if bubo_slowdown_path.exists():
        _, loops_slow, parents_slow, _ = parse_bubo_file(bubo_slowdown_path)
        exclusive_slow = compute_exclusive_cycles(loops_slow, parents_slow)
        for (comp_id, method, loop_id), cycles in exclusive_slow.items():
            if (comp_id, loop_id) in bubo_baseline_map:
                base_method, _ = bubo_baseline_map[(comp_id, loop_id)]
                bubo_slow_map[(comp_id, loop_id)] = (base_method, cycles)

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

    # Common keys: loops that both tools see in the baseline,
    # and that have LoopCallCount == 0 (via bubo_baseline_map)
    common_keys = sorted(
        set(bubo_baseline_map.keys()) & set(async_baseline_map.keys()),
        key=lambda k: bubo_baseline_map[k][1],  # sort by baseline Bubo exclusive cycles
        reverse=True,
    )

    if not common_keys:
        print("  [WARN] No common (CompId, LoopId) between Bubo (LoopCallCount==0) and Async baseline.")
        return

    # Totals restricted to common keys:
    # - total baseline Bubo: for coverage selection
    # - total slowdown Bubo/Async: for slowdown shares used in labels
    total_bubo_base = sum(bubo_baseline_map[k][1] for k in common_keys)
    if total_bubo_base == 0:
        print("  [WARN] Zero total baseline Bubo, skipping.")
        return

    total_bubo_slow_all = sum(bubo_slow_map.get(k, (None, 0))[1] for k in common_keys)
    total_async_slow_all = sum(async_slow_map.get(k, 0) for k in common_keys)

    selected_rows = []
    coverage = 0.0

    for i, (comp_id, loop_id) in enumerate(common_keys):
        if i >= MAX_LOOPS:
            break

        method, b_cycles_base = bubo_baseline_map[(comp_id, loop_id)]

        # Bubo slowdown (may be missing -> 0)
        _, b_cycles_slow = bubo_slow_map.get((comp_id, loop_id), (method, 0))

        # Async baseline
        a_samples_base = async_baseline_map[(comp_id, loop_id)]

        # Async slowdown (may be missing -> 0)
        a_samples_slow = async_slow_map.get((comp_id, loop_id), 0)

        # Percent changes in raw units
        if b_cycles_base > 0:
            bubo_pct_change = (b_cycles_slow - b_cycles_base) / float(b_cycles_base) * 100.0
        else:
            bubo_pct_change = 0.0

        if a_samples_base > 0:
            async_pct_change = (a_samples_slow - a_samples_base) / float(a_samples_base) * 100.0
        else:
            async_pct_change = 0.0

        # Slowdown shares (for labels)
        if total_bubo_slow_all > 0:
            b_slow_share = (b_cycles_slow / float(total_bubo_slow_all)) * 100.0
        else:
            b_slow_share = 0.0

        if total_async_slow_all > 0:
            a_slow_share = (a_samples_slow / float(total_async_slow_all)) * 100.0
        else:
            a_slow_share = 0.0

        # For coverage selection, use baseline Bubo share
        b_share_base = (b_cycles_base / float(total_bubo_base)) * 100.0

        row = {
            "CompId": comp_id,
            "LoopId": loop_id,
            "Method": method,
            "BuboExclusiveCyclesBaseline": b_cycles_base,
            "BuboExclusiveCyclesSlowdown": b_cycles_slow,
            "AsyncSamplesBaseline": a_samples_base,
            "AsyncSamplesSlowdown": a_samples_slow,
            "BuboPercentChange": bubo_pct_change,
            "AsyncPercentChange": async_pct_change,
            "BuboSlowdownSharePercent": b_slow_share,
            "AsyncSlowdownSharePercent": a_slow_share,
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
                "BuboExclusiveCyclesSlowdown",
                "AsyncSamplesBaseline",
                "AsyncSamplesSlowdown",
                "BuboPercentChange",
                "AsyncPercentChange",
                "BuboSlowdownSharePercent",
                "AsyncSlowdownSharePercent",
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
