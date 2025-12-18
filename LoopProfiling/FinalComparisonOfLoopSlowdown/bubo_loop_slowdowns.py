#!/usr/bin/env python3
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

# Set THIS to either runtime_overheads.csv or cycles_overhead_three_bars.csv
PROGRAM_OVERHEAD_CSV = "cycles_overhead.csv"
# PROGRAM_OVERHEAD_CSV = "cycles_overhead_three_bars.csv"

BUBO_BASE_DIR = "Data/BuboData/FourPhase_BuboSlowdownRuns"
BUBO_PLOT_DIR = "BuboLoopPlots"
BUBO_CSV_DIR = "BuboLoopCSVs"

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


# ---------------- Helpers: program-overhead CSV ----------------

def detect_program_overhead_columns(fieldnames: List[str]) -> Tuple[str, str]:
    """
    Returns (benchmark_column, slowdown_column).

    Detects:
      - runtime_overheads.csv style: slowdown_noBubo_pct
      - cycles_overhead_three_bars.csv style: pct_slowdown_noBubo_vs_baseline
    """
    if not fieldnames:
        raise ValueError("CSV has no headers")

    bench_col = None
    for cand in ("benchmark", "Benchmark"):
        if cand in fieldnames:
            bench_col = cand
            break
    if bench_col is None:
        raise ValueError(f"Could not find benchmark column. Expected 'benchmark' or 'Benchmark'. Found: {fieldnames}")

    if "slowdown_noBubo_pct" in fieldnames:
        return bench_col, "slowdown_noBubo_pct"

    if "pct_slowdown_noBubo_vs_baseline" in fieldnames:
        return bench_col, "pct_slowdown_noBubo_vs_baseline"

    raise ValueError(
        "Could not detect overhead type. Expected either column "
        "'slowdown_noBubo_pct' (runtime_overheads.csv) or "
        "'pct_slowdown_noBubo_vs_baseline' (cycles_overhead...). "
        f"Found headers: {fieldnames}"
    )


def load_program_overheads(csv_path: str) -> Dict[str, float]:
    """
    Load program-level slowdown from PROGRAM_OVERHEAD_CSV.
    Auto-detects runtime vs cycles CSV format.
    Returns: benchmark -> slowdown_pct
    """
    slowdown_map: Dict[str, float] = {}

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        bench_col, slow_col = detect_program_overhead_columns(reader.fieldnames or [])

        for row in reader:
            bm = (row.get(bench_col) or "").strip()
            if not bm:
                continue
            val_s = (row.get(slow_col) or "").strip()
            if not val_s:
                continue
            try:
                slowdown_map[bm] = float(val_s)
            except ValueError:
                continue

    print(f"[INFO] Program slowdown loaded from: {csv_path}")
    print(f"[INFO] Detected columns: bench='{bench_col}', slowdown='{slow_col}'")
    return slowdown_map


# ---------------- Helpers: parsing Bubo output ----------------

def parse_bubo_file(path: str) -> FileLoops:
    comp_id: Optional[int] = None
    comp_to_parents: Dict[int, Dict[int, int]] = {}
    comp_to_inclusive: Dict[int, Dict[int, LoopRecord]] = {}

    total_cycles: Optional[int] = None

    re_comp = re.compile(r"^Comp\s+(\d+)\s*\(")
    re_encoding = re.compile(r"^Found Encoding\s*:\s*(.*)")
    re_loop = re.compile(
        r"loop\s+(\d+)\s+Cycles:\s*([0-9]+)\s*\|\|\s*"
        r"Activation Count:\s*([0-9]+)\s*\|\s*LoopCallCount:\s*([0-9]+)\s*\|"
    )
    re_total = re.compile(r"Bubo\.RDTSC\.Harness\.main Total RDTSC cycles:\s*([0-9]+)")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")

            m_total = re_total.search(line)
            if m_total:
                total_cycles = int(m_total.group(1))
                continue

            m_comp = re_comp.match(line)
            if m_comp:
                comp_id = int(m_comp.group(1))
                comp_to_parents.setdefault(comp_id, {})
                comp_to_inclusive.setdefault(comp_id, {})
                continue

            if comp_id is None:
                continue

            m_enc = re_encoding.match(line)
            if m_enc:
                enc_str = m_enc.group(1)
                parent_map: Dict[int, int] = {}
                for part in enc_str.split(","):
                    part = part.strip()
                    if not part or ":" not in part:
                        continue
                    lid_s, parent_s = part.split(":", 1)
                    try:
                        parent_map[int(lid_s)] = int(parent_s)
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
                    exclusive_cycles=cycles,  # temp
                    activation_count=act,
                    loop_call_count=lcc,
                    source="",
                )
                comp_to_inclusive[comp_id][lid] = rec
                continue

    loops_final: Dict[Tuple[int, int], LoopRecord] = {}

    for cid, loops_dict in comp_to_inclusive.items():
        parents = comp_to_parents.get(cid, {})

        children: Dict[int, List[int]] = {lid: [] for lid in loops_dict.keys()}
        for lid, parent in parents.items():
            if parent != -1 and lid in loops_dict:
                children.setdefault(parent, []).append(lid)

        roots = [lid for lid in loops_dict.keys() if parents.get(lid, -1) == -1]
        inclusive_map = {lid: rec.inclusive_cycles for lid, rec in loops_dict.items()}
        exclusive_map: Dict[int, int] = {}

        def walk(lid: int) -> int:
            child_ids = children.get(lid, [])
            child_inclusive_sum = 0
            for ch in child_ids:
                child_inclusive_sum += walk(ch)
            excl = inclusive_map[lid] - child_inclusive_sum
            if excl < 0:
                excl = 0
            exclusive_map[lid] = excl
            return inclusive_map[lid]

        for root in roots:
            walk(root)

        for lid, rec in loops_dict.items():
            rec.exclusive_cycles = exclusive_map.get(lid, rec.inclusive_cycles)
            loops_final[(cid, lid)] = rec

    return FileLoops(loops=loops_final, total_cycles=total_cycles)


# ---------------- Plot + CSV ----------------

def make_plot_and_csv_for_benchmark(benchmark: str, prog_slowdown_map: Dict[str, float]) -> None:
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
    prog_slowdown_pct = prog_slowdown_map.get(benchmark, 0.0)

    rows_for_csv = []
    plot_entries = []

    all_keys = set(base_loops.loops.keys()) | set(slow_loops.loops.keys())

    for cid, lid in sorted(all_keys):
        key = (cid, lid)
        b_rec = base_loops.loops.get(key)
        s_rec = slow_loops.loops.get(key)

        if b_rec is None or s_rec is None:
            continue

        base_excl = b_rec.exclusive_cycles
        slow_excl = s_rec.exclusive_cycles

        if base_excl <= 0:
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
            "prog_slowdown_pct": prog_slowdown_pct,
        }
        rows_for_csv.append(row)

        if runtime_share_pct >= RUNTIME_SHARE_THRESHOLD:
            plot_entries.append(row)

    os.makedirs(BUBO_CSV_DIR, exist_ok=True)
    csv_path = os.path.join(BUBO_CSV_DIR, f"bubo_loops_{benchmark}.csv")
    with open(csv_path, "w", newline="") as f:
        fieldnames = [
            "benchmark", "comp_id", "loop_id", "loop_call_count",
            "baseline_exclusive_cycles", "slowdown_exclusive_cycles",
            "slowdown_pct", "runtime_share_pct", "total_cycles_slowdown",
            "prog_slowdown_pct",
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows_for_csv)

    if not plot_entries:
        print(f"[{benchmark}] No loops above {RUNTIME_SHARE_THRESHOLD}% runtime share, no plot.")
        return

    plot_entries.sort(key=lambda r: r["runtime_share_pct"], reverse=True)

    labels = [f"C{r['comp_id']}-L{r['loop_id']}\n{r['runtime_share_pct']:.1f}%" for r in plot_entries]
    values = [r["slowdown_pct"] for r in plot_entries]
    colors = ["tab:blue" if r["loop_call_count"] == 0 else "tab:orange" for r in plot_entries]

    x = list(range(len(plot_entries)))

    os.makedirs(BUBO_PLOT_DIR, exist_ok=True)
    plt.figure(figsize=(max(8, len(plot_entries) * 0.6), 6))
    ax = plt.gca()
    ax.bar(x, values, color=colors)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_ylabel("Loop slowdown (% change in exclusive Bubo cycles)")
    ax.set_title(f"{benchmark}: Loop-level slowdown vs baseline (Bubo)")

    ax.axhline(prog_slowdown_pct, linestyle="--", linewidth=1,
               label=f"Slowdown: {prog_slowdown_pct:.1f}%")

    plt.legend(handles=legend_handles, fontsize=12, loc="upper left")
    plt.tight_layout()

    png_path = os.path.join(BUBO_PLOT_DIR, f"bubo_loops_{benchmark}.png")
    plt.savefig(png_path, dpi=200)
    plt.close()

    print(f"[{benchmark}] Wrote {png_path} and {csv_path}")


# ---------------- Main ----------------

def main():
    if not os.path.isfile(PROGRAM_OVERHEAD_CSV):
        raise SystemExit(f"Cannot find program overhead CSV: {PROGRAM_OVERHEAD_CSV}")

    prog_slowdown_map = load_program_overheads(PROGRAM_OVERHEAD_CSV)

    if not os.path.isdir(BUBO_BASE_DIR):
        raise SystemExit(f"Cannot find Bubo data directory: {BUBO_BASE_DIR}")

    benchmarks = sorted(
        d for d in os.listdir(BUBO_BASE_DIR)
        if os.path.isdir(os.path.join(BUBO_BASE_DIR, d))
    )

    print("Benchmarks found:", ", ".join(benchmarks))

    for bm in benchmarks:
        make_plot_and_csv_for_benchmark(bm, prog_slowdown_map)


if __name__ == "__main__":
    main()
