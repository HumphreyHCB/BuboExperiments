#!/usr/bin/env python3
"""
Bubo loop-level slowdown visualisation tool.

- Uses runtime_overheads.csv for program-level slowdown (no Bubo).
- Parses Bubo baseline/slowdown outputs from:
    Data/BuboData/FourPhase_BuboSlowdownRuns/<Benchmark>

For each benchmark it:

  * Computes EXCLUSIVE cycles per (CompId, LoopId) from the loop encodings.
  * Computes % slowdown in exclusive cycles (slowdown vs baseline).
  * Computes % of total runtime using slowdown run total cycles.
  * Plots ONLY loops with > 2% of total runtime.
      - Blue bars: LoopCallCount == 0
      - Orange bars: LoopCallCount > 0
  * Adds a dotted horizontal line at the program slowdown (no Bubo), labelled:
        "Slowdown: XX%"

Outputs:
  - PNG per benchmark:  BuboLoopPlots/bubo_loops_<Benchmark>.png
  - CSV per benchmark:  BuboLoopCSVs/bubo_loops_<Benchmark>.csv

CSV contains enough data to recreate the plots later.
"""

import csv
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Tuple, List, Optional

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines


legend_handles = [
    mpatches.Patch(color="blue", label="Pure loops (LoopCallCount = 0)"),
    mpatches.Patch(color="Orange", label="Non-pure loops (LoopCallCount > 0)"),
    mlines.Line2D([], [], color="black", linestyle="--",
                  label="Program slowdown (from baseline)")
]

# ---------------- Configuration ----------------

RUNTIME_CSV = "runtime_overheads.csv"
BUBO_BASE_DIR = "Data/BuboData/FourPhase_BuboSlowdownRuns"
BUBO_PLOT_DIR = "BuboLoopPlots"
BUBO_CSV_DIR = "BuboLoopCSVs"

# Only loops with runtime share (%) >= this are plotted
RUNTIME_SHARE_THRESHOLD = 2.0  # percent


# ---------------- Data structures ----------------

@dataclass
class LoopRecord:
    comp_id: int
    loop_id: int
    inclusive_cycles: int
    exclusive_cycles: int
    activation_count: int
    loop_call_count: int
    source: str = ""


@dataclass
class FileLoops:
    loops: Dict[Tuple[int, int], LoopRecord] = field(default_factory=dict)
    total_cycles: Optional[int] = None


# ---------------- Helpers: parsing ----------------

def load_runtime_overheads(csv_path: str) -> Dict[str, float]:
    """
    Load program-level slowdown (no Bubo) from runtime_overheads.csv.

    Uses the 'slowdown_noBubo_pct' column.
    Returns mapping: benchmark -> slowdown_noBubo_pct
    """
    slowdown_map: Dict[str, float] = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bm = row["benchmark"]
            slowdown_pct = float(row["slowdown_noBubo_pct"])
            slowdown_map[bm] = slowdown_pct
    return slowdown_map


def parse_bubo_file(path: str) -> FileLoops:
    """
    Parse one Bubo output file.

    Extracts:
      - Loop blocks with Comp/Found Encoding/loop lines.
      - Total program cycles from:
            Bubo.RDTSC.Harness.main Total RDTSC cycles: <N>

    Computes EXCLUSIVE cycles per loop using the encoding tree.
    Returns FileLoops with loops keyed by (compId, loopId).
    """
    comp_id: Optional[int] = None
    # Per-comp temporary storage
    comp_to_parents: Dict[int, Dict[int, int]] = {}
    comp_to_inclusive: Dict[int, Dict[int, LoopRecord]] = {}

    total_cycles: Optional[int] = None

    re_comp = re.compile(r"^Comp\s+(\d+)\s*\(")
    re_encoding = re.compile(r"^Found Encoding\s*:\s*(.*)")
    re_loop = re.compile(
        r"loop\s+(\d+)\s+Cycles:\s*([0-9]+)\s*\|\|\s*Activation Count:([0-9]+)\| LoopCallCount:\s*([0-9]+)\s*\|"
    )
    re_total = re.compile(
        r"Bubo\.RDTSC\.Harness\.main Total RDTSC cycles:\s*([0-9]+)"
    )

    with open(path, "r") as f:
        for line in f:
            line = line.rstrip("\n")

            m_total = re_total.search(line)
            if m_total:
                total_cycles = int(m_total.group(1))
                continue

            m_comp = re_comp.match(line)
            if m_comp:
                comp_id = int(m_comp.group(1))
                if comp_id not in comp_to_parents:
                    comp_to_parents[comp_id] = {}
                    comp_to_inclusive[comp_id] = {}
                continue

            if comp_id is not None:
                m_enc = re_encoding.match(line)
                if m_enc:
                    enc_str = m_enc.group(1)
                    parent_map: Dict[int, int] = {}
                    for part in enc_str.split(","):
                        part = part.strip()
                        if not part:
                            continue
                        if ":" not in part:
                            continue
                        lid_s, parent_s = part.split(":", 1)
                        try:
                            lid = int(lid_s)
                            parent = int(parent_s)
                            parent_map[lid] = parent
                        except ValueError:
                            continue
                    comp_to_parents[comp_id] = parent_map
                    continue

                m_loop = re_loop.search(line)
                if m_loop:
                    lid = int(m_loop.group(1))
                    cycles = int(m_loop.group(2))
                    act = int(m_loop.group(3))
                    lcc = int(m_loop.group(4))

                    rec = LoopRecord(
                        comp_id=comp_id,
                        loop_id=lid,
                        inclusive_cycles=cycles,
                        exclusive_cycles=cycles,  # temp, will fix
                        activation_count=act,
                        loop_call_count=lcc,
                        source="",  # not needed for now
                    )
                    comp_to_inclusive[comp_id][lid] = rec
                    continue

    # Compute exclusive cycles per loop using the parent maps
    loops_final: Dict[Tuple[int, int], LoopRecord] = {}

    for cid, loops_dict in comp_to_inclusive.items():
        parents = comp_to_parents.get(cid, {})
        # Build children map
        children: Dict[int, List[int]] = {}
        for lid in loops_dict.keys():
            children.setdefault(lid, [])
        for lid, parent in parents.items():
            if parent != -1:
                children.setdefault(parent, []).append(lid)

        # Find roots (parent == -1 or missing)
        roots = [
            lid for lid in loops_dict.keys()
            if parents.get(lid, -1) == -1
        ]

        inclusive_map = {lid: rec.inclusive_cycles for lid, rec in loops_dict.items()}
        exclusive_map: Dict[int, int] = {}

        def compute_exclusive(lid: int) -> int:
            child_ids = children.get(lid, [])
            child_sum = 0
            for ch in child_ids:
                child_sum += compute_exclusive(ch)
            excl = inclusive_map[lid] - child_sum
            if excl < 0:
                excl = 0
            exclusive_map[lid] = excl
            return inclusive_map[lid]

        for root in roots:
            compute_exclusive(root)

        # Fill final records
        for lid, rec in loops_dict.items():
            excl = exclusive_map.get(lid, rec.inclusive_cycles)
            rec.exclusive_cycles = excl
            loops_final[(cid, lid)] = rec

    return FileLoops(loops=loops_final, total_cycles=total_cycles)


# ---------------- Plot + CSV ----------------

def make_plot_and_csv_for_benchmark(
    benchmark: str,
    slowdown_pct_map: Dict[str, float],
) -> None:
    """
    For one benchmark:
      - Parse baseline_withBubo and slowdown_withBubo outputs.
      - Compute exclusive cycles per loop.
      - Compute % slowdown and % runtime share.
      - Plot only loops with > RUNTIME_SHARE_THRESHOLD runtime share.
      - Save CSV with all loops.
    """
    bm_dir = os.path.join(BUBO_BASE_DIR, benchmark)
    base_path = os.path.join(bm_dir, f"{benchmark}_baseline_withBubo.out")
    slow_path = os.path.join(bm_dir, f"{benchmark}_slowdown_withBubo.out")

    if not os.path.isfile(base_path) or not os.path.isfile(slow_path):
        print(f"[{benchmark}] Missing Bubo files, skipping.")
        return

    base_loops = parse_bubo_file(base_path)
    slow_loops = parse_bubo_file(slow_path)

    if slow_loops.total_cycles is None:
        print(f"[{benchmark}] No total cycles found in slowdown file, skipping.")
        return

    total_cycles_slow = slow_loops.total_cycles

    # Program slowdown (no Bubo) from CSV
    prog_slowdown_pct = slowdown_pct_map.get(benchmark)
    if prog_slowdown_pct is None:
        print(f"[{benchmark}] No slowdown_noBubo_pct in runtime_overheads.csv, using 0.")
        prog_slowdown_pct = 0.0

    rows_for_csv = []
    plot_entries = []

    # Use keys that appear in either baseline or slowdown
    all_keys = set(base_loops.loops.keys()) | set(slow_loops.loops.keys())

    for key in sorted(all_keys):
        cid, lid = key
        b_rec = base_loops.loops.get(key)
        s_rec = slow_loops.loops.get(key)

        if b_rec is None or s_rec is None:
            # Need both baseline and slowdown to compute slowdown %
            continue

        base_excl = b_rec.exclusive_cycles
        slow_excl = s_rec.exclusive_cycles

        if base_excl <= 0:
            # Can't compute meaningful slowdown %
            continue

        slowdown_pct = (slow_excl - base_excl) / base_excl * 100.0
        runtime_share_pct = (slow_excl / total_cycles_slow) * 100.0

        row = {
            "benchmark": benchmark,
            "comp_id": cid,
            "loop_id": lid,
            "loop_call_count": s_rec.loop_call_count,
            "baseline_exclusive_cycles": base_excl,
            "slowdown_exclusive_cycles": slow_excl,
            "slowdown_pct": slowdown_pct,
            "runtime_share_pct": runtime_share_pct,
            "total_cycles_slowdown": total_cycles_slow,
            "prog_slowdown_noBubo_pct": prog_slowdown_pct,
        }
        rows_for_csv.append(row)

        # Only loops above threshold go into the plot
        if runtime_share_pct >= RUNTIME_SHARE_THRESHOLD:
            plot_entries.append(row)

    # Write CSV (all loops, not just plotted ones)
    os.makedirs(BUBO_CSV_DIR, exist_ok=True)
    csv_path = os.path.join(BUBO_CSV_DIR, f"bubo_loops_{benchmark}.csv")
    with open(csv_path, "w", newline="") as f:
        fieldnames = [
            "benchmark",
            "comp_id",
            "loop_id",
            "loop_call_count",
            "baseline_exclusive_cycles",
            "slowdown_exclusive_cycles",
            "slowdown_pct",
            "runtime_share_pct",
            "total_cycles_slowdown",
            "prog_slowdown_noBubo_pct",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_for_csv)

    if not plot_entries:
        print(f"[{benchmark}] No loops above {RUNTIME_SHARE_THRESHOLD}% runtime share, no plot.")
        return

    # Sort plot entries by runtime share descending
    plot_entries.sort(key=lambda r: r["runtime_share_pct"], reverse=True)

    labels = [
        f"C{row['comp_id']}-L{row['loop_id']}\n{row['runtime_share_pct']:.1f}%"
        for row in plot_entries
    ]
    values = [row["slowdown_pct"] for row in plot_entries]
    colors = [
        "tab:blue" if row["loop_call_count"] == 0 else "tab:orange"
        for row in plot_entries
    ]

    x = list(range(len(plot_entries)))

    os.makedirs(BUBO_PLOT_DIR, exist_ok=True)
    plt.figure(figsize=(max(8, len(plot_entries) * 0.6), 6))
    ax = plt.gca()

    ax.bar(x, values, color=colors)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")

    ax.set_ylabel("Loop slowdown (% change in exclusive Bubo cycles)")
    ax.set_title(f"{benchmark}: Loop-level slowdown vs baseline (Bubo)")

    # Dotted line for program slowdown (no Bubo)
    ax.axhline(
        prog_slowdown_pct,
        linestyle="--",
        linewidth=1,
        label=f"Slowdown: {prog_slowdown_pct:.1f}%"
    )

    plt.legend(handles=legend_handles, fontsize=12, loc="upper left")
    plt.tight_layout()

    png_path = os.path.join(BUBO_PLOT_DIR, f"bubo_loops_{benchmark}.png")
    plt.savefig(png_path, dpi=200)
    plt.close()

    print(f"[{benchmark}] Wrote {png_path} and {csv_path}")


# ---------------- Main ----------------

def main():
    if not os.path.isfile(RUNTIME_CSV):
        raise SystemExit(f"Cannot find {RUNTIME_CSV} in current directory.")

    slowdown_pct_map = load_runtime_overheads(RUNTIME_CSV)

    if not os.path.isdir(BUBO_BASE_DIR):
        raise SystemExit(f"Cannot find Bubo data directory: {BUBO_BASE_DIR}")

    benchmarks = sorted(
        d for d in os.listdir(BUBO_BASE_DIR)
        if os.path.isdir(os.path.join(BUBO_BASE_DIR, d))
    )

    print("Benchmarks found:", ", ".join(benchmarks))

    for bm in benchmarks:
        make_plot_and_csv_for_benchmark(bm, slowdown_pct_map)


if __name__ == "__main__":
    main()
