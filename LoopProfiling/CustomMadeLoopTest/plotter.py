#!/usr/bin/env python3
import csv
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Tuple, List, Optional

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines

# ---------------- Legend ----------------
legend_handles = [
    mpatches.Patch(color="tab:blue", label="Per-loop slowdown (pure loops, LCC=0)"),
    mpatches.Patch(color="tab:orange", label="Per-loop slowdown (non-pure, LCC>0)"),
    mpatches.Patch(color="tab:gray", label="Per-loop median (VTune)"),
    mlines.Line2D([], [], color="black", linestyle="--", label="Program slowdown"),
]

# ---------------- Configuration ----------------
PROGRAM_OVERHEAD_CSV = "cycles_overhead.csv"
BENCHMARK = "LoopBenchmarks"  # or LoopBenchmarksLFence etc

BUBO_BASE_DIR = "/home/hb478/repos/BuboExperiments/LoopProfiling/CustomMadeLoopTest/Data/BuboData/BuboOwnSlowdown_bubo6_DevineWithProbe"

# IMPORTANT: keep these names the same as you requested
OUT_PNG = "LoopBenchmarks_bubo_loops.png"
OUT_CSV = "LoopBenchmarks_bubo_loops.csv"

# NEW: per-loop medians file
LOOP_MEDIANS_CSV = "Data/VTuneData/total_pct_slowdown_per_loop.csv"

RUNTIME_SHARE_THRESHOLD = 2.0  # percent
FIGSIZE = (20, 14.5)

# If True, only accept LoopBenchmarks.* methods from Bubo output (skip java.util.* etc)
REQUIRE_LOOPBENCHMARKS_PREFIX = True


# ---------------- Data structures ----------------
@dataclass
class LoopRecord:
    comp_id: int
    loop_id: int
    comp_name: str
    inclusive_cycles: int
    exclusive_cycles: int
    activation_count: int
    loop_call_count: int


@dataclass
class FileLoops:
    loops: Dict[Tuple[int, int], LoopRecord] = field(default_factory=dict)
    total_cycles: Optional[int] = None
    comp_names: Dict[int, str] = field(default_factory=dict)


# ---------------- Program overhead CSV ----------------
def detect_program_overhead_columns(fieldnames: List[str]) -> Tuple[str, str]:
    if not fieldnames:
        raise ValueError("CSV has no headers")

    bench_col = None
    for cand in ("benchmark", "Benchmark"):
        if cand in fieldnames:
            bench_col = cand
            break
    if bench_col is None:
        raise ValueError(f"Could not find benchmark column. Found: {fieldnames}")

    if "slowdown_noBubo_pct" in fieldnames:
        return bench_col, "slowdown_noBubo_pct"

    if "pct_slowdown_noBubo_vs_baseline" in fieldnames:
        return bench_col, "pct_slowdown_noBubo_vs_baseline"

    raise ValueError(
        "Could not detect overhead type. Expected either "
        "'slowdown_noBubo_pct' or 'pct_slowdown_noBubo_vs_baseline'. "
        f"Found: {fieldnames}"
    )


def load_program_overheads(csv_path: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
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
                out[bm] = float(val_s)
            except ValueError:
                pass
    print(f"[INFO] Program slowdown from {csv_path} ({slow_col})")
    return out


# ---------------- Method name normalisation ----------------
def normalize_method_name_dot(s: str) -> Optional[str]:
    """
    Normalises method names to the VTune CSV format:
        LoopBenchmarks.methodName

    Accepts inputs like:
      - "LoopBenchmarks.bubbleSortLoop(int[])-Re-Comp"
      - "LoopBenchmarks.bubbleSortLoop(int[],)"
      - "\"LoopBenchmarks.bubbleSortLoop(int[])-Re-Comp\""
      - "LoopBenchmarks::bubbleSortLoop"
    Returns:
      - "LoopBenchmarks.bubbleSortLoop"
    or None if it can't / shouldn't match.
    """
    if not s:
        return None

    s = s.strip()

    # Drop surrounding quotes
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1].strip()

    # If we ever get :: form, convert to dot form
    s = s.replace("::", ".")

    # Require LoopBenchmarks prefix (optional)
    if REQUIRE_LOOPBENCHMARKS_PREFIX and not s.startswith("LoopBenchmarks."):
        return None

    # Remove -Re-Comp suffix (anywhere)
    s = s.replace("-Re-Comp", "")

    # Chop at first "(" to remove args
    if "(" in s:
        s = s.split("(", 1)[0]

    # Clean trailing commas/spaces
    s = s.strip().rstrip(",")

    # Sanity: must still look like LoopBenchmarks.x
    if not s.startswith("LoopBenchmarks.") or s == "LoopBenchmarks.":
        return None

    return s


def comp_name_to_method_dot(comp_name: str) -> Optional[str]:
    """
    Takes Bubo comp_name like:
        LoopBenchmarks.bubbleSortLoop(int[])-Re-Comp
    and returns:
        LoopBenchmarks.bubbleSortLoop
    """
    return normalize_method_name_dot(comp_name)


# ---------------- Load per-loop medians (VTune) ----------------
def load_loop_medians(loop_medians_csv: str) -> Dict[Tuple[str, int], float]:
    """
    Reads median_slowdown_per_loop.csv with columns:
      comp_id, method, loop_id, ..., median_*

    We IGNORE comp_id and match on:
      (method, loop_id)

    Returns map:
      (LoopBenchmarks.methodName, loop_id) -> median slowdown
    """
    if not os.path.isfile(loop_medians_csv):
        raise SystemExit(f"Cannot find loop medians CSV: {loop_medians_csv}")

    with open(loop_medians_csv, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit(f"Loop medians CSV has no header: {loop_medians_csv}")

        needed = {"method", "loop_id"}
        if not needed.issubset(reader.fieldnames):
            raise SystemExit(
                f"Loop medians CSV missing required columns.\n"
                f"Found: {reader.fieldnames}\n"
                f"Need at least: {sorted(needed)}"
            )

        # detect median column automatically (e.g. median_pct_slowdown)
        median_col = None
        for c in reader.fieldnames:
            if c.startswith("median_"):
                median_col = c
                break
        if median_col is None:
            raise SystemExit(
                f"Could not find median column in {loop_medians_csv}.\n"
                f"Expected something like 'median_pct_slowdown'."
            )

        out: Dict[Tuple[str, int], float] = {}
        bad_method = 0

        for row in reader:
            try:
                lid = int(row["loop_id"])
                raw_method = (row["method"] or "").strip()
                val = float(row[median_col])
            except (ValueError, KeyError):
                continue

            method = normalize_method_name_dot(raw_method)
            if not method:
                bad_method += 1
                continue

            out[(method, lid)] = val

    print(f"[INFO] Loaded {len(out)} per-loop medians from {loop_medians_csv} (ignored comp_id)")
    if bad_method:
        print(f"[WARN] Skipped {bad_method} median rows due to unrecognised method names")
    return out


# ---------------- Parse Bubo output ----------------
def parse_bubo_file(path: str) -> FileLoops:
    comp_id: Optional[int] = None

    comp_to_name: Dict[int, str] = {}
    comp_to_parents: Dict[int, Dict[int, int]] = {}
    comp_to_loops: Dict[int, Dict[int, LoopRecord]] = {}
    total_cycles: Optional[int] = None

    re_comp = re.compile(r"^Comp\s+(\d+)\s*\((.*)\)\s*loops:\s*$")
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
                comp_name = m_comp.group(2).strip()
                comp_to_name[comp_id] = comp_name
                comp_to_parents.setdefault(comp_id, {})
                comp_to_loops.setdefault(comp_id, {})
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
                    comp_name=comp_to_name.get(comp_id, "<unknown>"),
                    inclusive_cycles=cycles,
                    exclusive_cycles=cycles,  # temp
                    activation_count=act,
                    loop_call_count=lcc,
                )
                comp_to_loops[comp_id][lid] = rec
                continue

    # exclusive cycles: inclusive - sum(inclusive(children))
    loops_final: Dict[Tuple[int, int], LoopRecord] = {}

    for cid, loops_dict in comp_to_loops.items():
        parents = comp_to_parents.get(cid, {})

        children: Dict[int, List[int]] = {lid: [] for lid in loops_dict.keys()}
        for lid, parent in parents.items():
            if parent != -1 and lid in loops_dict:
                children.setdefault(parent, []).append(lid)

        roots = [lid for lid in loops_dict.keys() if parents.get(lid, -1) == -1]
        inclusive = {lid: rec.inclusive_cycles for lid, rec in loops_dict.items()}
        exclusive: Dict[int, int] = {}

        def walk(lid: int) -> int:
            child_sum = 0
            for ch in children.get(lid, []):
                child_sum += walk(ch)
            excl = inclusive[lid] - child_sum
            if excl < 0:
                excl = 0
            exclusive[lid] = excl
            return inclusive[lid]

        for r in roots:
            walk(r)

        for lid, rec in loops_dict.items():
            rec.exclusive_cycles = exclusive.get(lid, rec.inclusive_cycles)
            loops_final[(cid, lid)] = rec

    return FileLoops(loops=loops_final, total_cycles=total_cycles, comp_names=comp_to_name)


# ---------------- Main logic ----------------
def main():
    if not os.path.isfile(PROGRAM_OVERHEAD_CSV):
        raise SystemExit(f"Cannot find program overhead CSV: {PROGRAM_OVERHEAD_CSV}")

    loop_medians = load_loop_medians(LOOP_MEDIANS_CSV)

    prog_slowdown_map = load_program_overheads(PROGRAM_OVERHEAD_CSV)
    prog_slowdown_pct = prog_slowdown_map.get(BENCHMARK, 0.0)

    bm_dir = os.path.join(BUBO_BASE_DIR, BENCHMARK)
    base_path = os.path.join(bm_dir, f"{BENCHMARK}_baseline_withBubo.out")
    slow_path = os.path.join(bm_dir, f"{BENCHMARK}_slowdown_withBubo.out")

    if not os.path.isfile(base_path) or not os.path.isfile(slow_path):
        raise SystemExit(f"Missing Bubo files:\n  {base_path}\n  {slow_path}")

    base_loops = parse_bubo_file(base_path)
    slow_loops = parse_bubo_file(slow_path)

    if slow_loops.total_cycles is None:
        raise SystemExit("No total cycles found in slowdown file")

    total_cycles_slow = slow_loops.total_cycles

    rows = []
    plot_entries = []

    all_keys = set(base_loops.loops.keys()) | set(slow_loops.loops.keys())

    for cid, lid in sorted(all_keys):
        key = (cid, lid)
        b = base_loops.loops.get(key)
        s = slow_loops.loops.get(key)
        if b is None or s is None:
            continue

        base_excl = b.exclusive_cycles
        slow_excl = s.exclusive_cycles
        if base_excl <= 0:
            continue

        slowdown_pct = (slow_excl - base_excl) / base_excl * 100.0
        runtime_share_pct = (slow_excl / total_cycles_slow) * 100.0
        comp_name = s.comp_name or b.comp_name or "<unknown>"

        method_dot = comp_name_to_method_dot(comp_name)
        loop_median = loop_medians.get((method_dot, lid)) if method_dot else None

        row = {
            "benchmark": BENCHMARK,
            "comp_id": cid,
            "comp_name": comp_name,
            "method_dot": method_dot or "",
            "loop_id": lid,
            "loop_call_count": s.loop_call_count,
            "baseline_exclusive_cycles": base_excl,
            "slowdown_exclusive_cycles": slow_excl,
            "slowdown_pct": slowdown_pct,
            "loop_median_pct": loop_median if loop_median is not None else "",
            "runtime_share_pct": runtime_share_pct,
            "total_cycles_slowdown": total_cycles_slow,
            "prog_slowdown_pct": prog_slowdown_pct,
        }
        rows.append(row)

        if runtime_share_pct >= RUNTIME_SHARE_THRESHOLD:
            plot_entries.append(row)

    # Write CSV (all rows)
    with open(OUT_CSV, "w", newline="") as f:
        fieldnames = [
            "benchmark", "comp_id", "comp_name", "method_dot", "loop_id", "loop_call_count",
            "baseline_exclusive_cycles", "slowdown_exclusive_cycles",
            "slowdown_pct", "loop_median_pct",
            "runtime_share_pct",
            "total_cycles_slowdown", "prog_slowdown_pct",
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    if not plot_entries:
        print(f"No loops >= {RUNTIME_SHARE_THRESHOLD}% runtime share; wrote CSV only: {OUT_CSV}")
        return

    # Plot: sort by runtime share desc
    plot_entries.sort(key=lambda r: r["runtime_share_pct"], reverse=True)

    labels = [
        f"C{r['comp_id']} - L{r['loop_id']}\n{r['runtime_share_pct']:.1f}%\n{r['method_dot'] or r['comp_name']}"
        for r in plot_entries
    ]

    per_loop_vals = [r["slowdown_pct"] for r in plot_entries]

    loop_median_vals: List[float] = []
    loop_median_missing = 0
    for r in plot_entries:
        v = r["loop_median_pct"]
        if v == "" or v is None:
            loop_median_vals.append(0.0)
            loop_median_missing += 1
        else:
            loop_median_vals.append(float(v))

    per_loop_colors = ["tab:blue" if r["loop_call_count"] == 0 else "tab:orange" for r in plot_entries]

    x = list(range(len(plot_entries)))
    width = 0.40
    x1 = [i - width / 2 for i in x]
    x2 = [i + width / 2 for i in x]

    plt.figure(figsize=FIGSIZE)
    ax = plt.gca()

    bars1 = ax.bar(x1, per_loop_vals, width=width, color=per_loop_colors)
    bars2 = ax.bar(x2, loop_median_vals, width=width, color="tab:gray")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.set_ylabel("Slowdown (% change)")
    ax.set_title(f"{BENCHMARK}: per-loop slowdown vs per-loop VTune median")

    # Program slowdown line
    ax.axhline(prog_slowdown_pct, linestyle="--", linewidth=1)

    def label_bars(bars, values):
        for rect, v in zip(bars, values):
            y = rect.get_height()
            if y >= 0:
                ax.text(rect.get_x() + rect.get_width() / 2, y, f"{v:.1f}%", ha="center", va="bottom", fontsize=8)
            else:
                ax.text(rect.get_x() + rect.get_width() / 2, y, f"{v:.1f}%", ha="center", va="top", fontsize=8)

    label_bars(bars1, per_loop_vals)
    label_bars(bars2, loop_median_vals)

    ax.legend(handles=legend_handles, fontsize=9, loc="upper left")
    plt.tight_layout()
    plt.savefig(OUT_PNG, dpi=200)
    plt.close()

    if loop_median_missing > 0:
        print(f"[WARN] {loop_median_missing} plotted loops had no matching loop median (bar shown as 0).")
        print("       This is expected if VTune only produced medians for a subset of loops.")

    print(f"Wrote: {OUT_PNG}")
    print(f"Wrote: {OUT_CSV}")


if __name__ == "__main__":
    main()
