#!/usr/bin/env python3
"""
Compatible version for Python < 3.10 (no | type unions)

Now also:
- Sorts loops by baseline cycles (NoSlowdown) descending.
- Computes BaselineSharePercent = share of total baseline loop cycles.
- Adds BaselineSharePercent to CSV.
- Shows share in x-axis labels, e.g. "C119-L0 (82.3%)".
- Highlights bars that together account for the first 95% of baseline cycles in red.
- Can use either:
    NoSlowdown.txt / Slowdown.txt
  or
    *_LIR_false.out / *_LIR_true.out
  with False = NoSlowdown (baseline) and True = Slowdown.
- Additionally computes an EXCLUSIVE view of loop cycles using the
  "Found Encoding : 0:-1,1:0,2:1" nesting info and writes:
    loop_cycle_percent_change_exclusive.csv
    slowdown_percent_plot_exclusive.png

Exclusive view:
- Loops are sorted by EXCLUSIVE baseline cycles.
- Bars are coloured red (core) vs blue (non-core) based on 95% cumulative
  exclusive share.
- Only the most significant loops are shown: up to MAX_EXCLUSIVE_LOOPS or
  until EXCLUSIVE_COVERAGE_THRESHOLD % of total exclusive baseline time.
"""

from pathlib import Path
import re
import csv
import math
import matplotlib.pyplot as plt

# Empty string = current directory (so you can run from inside FirstTest)
ROOT = Path("")

TOTAL_RE = re.compile(r"Total Runtime:\s+(\d+)us")
COMP_RE  = re.compile(r"^Comp\s+(\d+)\s+\((.+?)\)\s+loops:")
LOOP_RE  = re.compile(r"loop\s+(\d+)\s+Cycles:\s+(\d+)")
ENCODING_RE = re.compile(r"Found Encoding\s*:\s*(.*)")

# How aggressively to trim the exclusive plot
MAX_EXCLUSIVE_LOOPS = 40
EXCLUSIVE_COVERAGE_THRESHOLD = 99.0  # percent of total exclusive baseline time


def parse_result_file(path):
    """
    Parse NoSlowdown/Slowdown style files.

    Returns:
      total_runtime_us: int
      loops:   dict[(comp_id, method_name, loop_id)] = inclusive_cycles
      parents: dict[(comp_id, loop_id)] = parent_loop_id or None
               (based on "Found Encoding : 0:-1,1:0,2:1")
    """
    total_runtime = None
    loops = {}
    parents = {}

    current_comp_id = None
    current_method = None

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")

            # Total runtime
            m = TOTAL_RE.search(line)
            if m:
                total_runtime = int(m.group(1))
                continue

            # Compilation header
            m = COMP_RE.match(line)
            if m:
                current_comp_id = int(m.group(1))
                current_method = m.group(2).strip()
                continue

            # Encoding line (parent/child loops)
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

            # Loop lines
            if current_comp_id is not None:
                m = LOOP_RE.search(line)
                if m:
                    loop_id = int(m.group(1))
                    cycles = int(m.group(2))
                    loops[(current_comp_id, current_method, loop_id)] = cycles
                    continue

            # Blank line resets current comp/method
            if not line.strip():
                current_comp_id = None
                current_method = None

    if total_runtime is None:
        raise ValueError("Missing 'Total Runtime' in %s" % path)

    return total_runtime, loops, parents


def percent_change(baseline, new):
    if baseline == 0:
        return float("nan")
    return (new - baseline) / float(baseline) * 100.0


def create_benchmark_plot(bench_name, labels, pct_values, total_pct_change,
                          core_mask, out_path, title_suffix=""):
    """
    Plot per-loop % change in cycles.
    core_mask: list[bool] indicating which bars are in the "top 95%" by baseline share.
    title_suffix: optional extra string for the title, e.g. "(exclusive)".
    """
    x = list(range(len(labels)))

    # Make plot a bit wider for many loops
    fig_width = max(10, len(labels) * 0.6)
    fig, ax = plt.subplots(figsize=(fig_width, 5))

    # Colors: red for "core" loops, blue for others
    colors = []
    for is_core in core_mask:
        if is_core:
            colors.append("red")        # highlighted loops
        else:
            colors.append("tab:blue")   # non-core loops

    ax.bar(x, pct_values, color=colors)

    ax.set_xticks(x)
    # No rotation, keep labels horizontal
    ax.set_xticklabels(labels)

    ax.set_ylabel("% change in cycles")

    if not math.isnan(total_pct_change):
        ax.axhline(total_pct_change, linestyle="--",
                   label="Total runtime: %.1f%%" % total_pct_change)
        ax.legend()

    title = "%s – Loop Cycle %% Change (sorted by baseline cycles)" % bench_name
    if title_suffix:
        title = title + " " + title_suffix
    ax.set_title(title)

    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


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


def analyze_benchmark(bench_dir):
    bench_name = bench_dir.name

    # --- Try standard NoSlowdown/Slowdown names first ---
    ns = bench_dir / "NoSlowdown.txt"
    sl = bench_dir / "Slowdown.txt"

    # If those don't exist, fall back to *_LIR_false.out / *_LIR_true.out
    if not ns.exists() or not sl.exists():
        false_files = sorted(bench_dir.glob("*LIR_false.out"))
        true_files  = sorted(bench_dir.glob("*LIR_true.out"))

        if false_files and true_files:
            # False = baseline (NoSlowdown), True = Slowdown
            ns = false_files[0]
            sl = true_files[0]
            print(f"[INFO] {bench_name}: using LIR false/true files -> "
                  f"{ns.name} (NoSlowdown), {sl.name} (Slowdown)")
        else:
            print("[WARN] Missing slowdown / no-slowdown in", bench_name)
            return None

    print("[INFO] Parsing", bench_name)

    no_total, no_loops, no_parents = parse_result_file(ns)
    slow_total, slow_loops, _ = parse_result_file(sl)

    total_pct = percent_change(no_total, slow_total)

    # Keys present in both runs
    common_keys = set(no_loops.keys()) & set(slow_loops.keys())
    if not common_keys:
        print("[WARN] No common loops in", bench_name)
        return None

    # Sort by baseline cycles descending to reflect significance (INCLUSIVE view)
    common = sorted(
        common_keys,
        key=lambda k: no_loops[k],
        reverse=True
    )

    # ----------------------------------------------------------------------
    # Inclusive view (existing behaviour)
    # ----------------------------------------------------------------------

    # Total baseline cycles across all common loops (INCLUSIVE)
    total_baseline_cycles = sum(no_loops[k] for k in common)

    rows = []
    labels = []
    pct_values = []
    shares = []

    for (comp_id, method, loop_id) in common:
        base = no_loops[(comp_id, method, loop_id)]
        slow = slow_loops[(comp_id, method, loop_id)]
        pct = percent_change(base, slow)

        if total_baseline_cycles > 0:
            share = base / float(total_baseline_cycles) * 100.0
        else:
            share = float("nan")

        shares.append(share)

        rows.append({
            "CompId": comp_id,
            "Method": method,
            "LoopId": loop_id,
            "BaselineCycles": base,
            "SlowdownCycles": slow,
            "PercentChangeCycles": pct,
            "BaselineSharePercent": share,
        })

        # Label with comp/loop and share, e.g. "C119-L0 (82.3%)"
        if math.isnan(share):
            label = "C%d-L%d \n" % (comp_id, loop_id)
        else:
            label = "C%d-L%d \n (%.1f%%)" % (comp_id, loop_id, share)

        labels.append(label)
        pct_values.append(pct)

    # Compute which loops make up the first 95% of baseline cycles (cumulative)
    core_mask = []
    cumulative = 0.0
    for s in shares:
        if math.isnan(s):
            core = False
        else:
            if cumulative < 95.0:
                core = True
            else:
                core = False
            cumulative += s
        core_mask.append(core)

    # write inclusive csv
    out_csv = bench_dir / "loop_cycle_percent_change.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "CompId",
            "Method",
            "LoopId",
            "BaselineCycles",
            "SlowdownCycles",
            "PercentChangeCycles",
            "BaselineSharePercent",
        ])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print("  -> Wrote", out_csv)

    # inclusive plot
    if pct_values:
        out_png = bench_dir / "slowdown_percent_plot.png"
        create_benchmark_plot(bench_name, labels, pct_values, total_pct,
                              core_mask, out_png)
        print("  -> Wrote", out_png)

    # ----------------------------------------------------------------------
    # Exclusive view (new, significance-aware and trimmed)
    # ----------------------------------------------------------------------

    # Compute per-loop exclusive cycles for baseline and slowdown
    exclusive_no = compute_exclusive_cycles(no_loops, no_parents)
    exclusive_slow = compute_exclusive_cycles(slow_loops, no_parents)

    # Sort keys by EXCLUSIVE baseline cycles (descending)
    sorted_excl_keys = sorted(
        common_keys,
        key=lambda k: exclusive_no.get(k, 0),
        reverse=True,
    )

    # Total exclusive baseline cycles across all common loops
    total_excl_all = sum(exclusive_no.get(k, 0) for k in sorted_excl_keys)

    # Now select only the most significant loops for plotting:
    #  - up to MAX_EXCLUSIVE_LOOPS
    #  - and until cumulative coverage reaches EXCLUSIVE_COVERAGE_THRESHOLD%
    selected_keys = []
    coverage = 0.0
    for i, k in enumerate(sorted_excl_keys):
        if i >= MAX_EXCLUSIVE_LOOPS:
            break

        base_excl = exclusive_no.get(k, 0)
        if total_excl_all > 0:
            share = base_excl / float(total_excl_all) * 100.0
        else:
            share = 0.0

        selected_keys.append(k)
        coverage += share

        if coverage >= EXCLUSIVE_COVERAGE_THRESHOLD:
            break

    if not selected_keys:
        print("  [WARN] No exclusive loops selected for", bench_name)
        return True

    rows_excl = []
    labels_excl = []
    pct_values_excl = []
    shares_excl = []

    for (comp_id, method, loop_id) in selected_keys:
        base_excl = exclusive_no.get((comp_id, method, loop_id), 0)
        slow_excl = exclusive_slow.get((comp_id, method, loop_id), 0)
        pct_excl = percent_change(base_excl, slow_excl)

        if total_excl_all > 0:
            share_excl = base_excl / float(total_excl_all) * 100.0
        else:
            share_excl = float("nan")

        shares_excl.append(share_excl)

        rows_excl.append({
            "CompId": comp_id,
            "Method": method,
            "LoopId": loop_id,
            "BaselineCyclesExclusive": base_excl,
            "SlowdownCyclesExclusive": slow_excl,
            "PercentChangeCyclesExclusive": pct_excl,
            "BaselineSharePercentExclusive": share_excl,
        })

        if math.isnan(share_excl):
            label = "C%d-L%d \n" % (comp_id, loop_id)
        else:
            label = "C%d-L%d \n (%.1f%% excl)" % (comp_id, loop_id, share_excl)

        labels_excl.append(label)
        pct_values_excl.append(pct_excl)

    # 95% core mask for EXCLUSIVE view (based on exclusive share)
    core_mask_excl = []
    cumulative_excl = 0.0
    for s in shares_excl:
        if math.isnan(s):
            core = False
        else:
            if cumulative_excl < 95.0:
                core = True
            else:
                core = False
            cumulative_excl += s
        core_mask_excl.append(core)

    # write exclusive csv (only for selected loops)
    out_csv_excl = bench_dir / "loop_cycle_percent_change_exclusive.csv"
    with open(out_csv_excl, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "CompId",
            "Method",
            "LoopId",
            "BaselineCyclesExclusive",
            "SlowdownCyclesExclusive",
            "PercentChangeCyclesExclusive",
            "BaselineSharePercentExclusive",
        ])
        w.writeheader()
        for r in rows_excl:
            w.writerow(r)

    print("  -> Wrote", out_csv_excl)

    # exclusive plot (uses same total_pct line, because runtime change is global)
    if pct_values_excl:
        out_png_excl = bench_dir / "slowdown_percent_plot_exclusive.png"
        create_benchmark_plot(
            bench_name,
            labels_excl,
            pct_values_excl,
            total_pct,
            core_mask_excl,
            out_png_excl,          # <--- this was missing
            title_suffix="(exclusive)"
        )
        print("  -> Wrote", out_png_excl)

    return True


def main():
    if not ROOT.exists():
        print("ERROR: root directory not found:", ROOT)
        return

    for bench in sorted(ROOT.iterdir()):
        if bench.is_dir():
            analyze_benchmark(bench)


if __name__ == "__main__":
    main()
