#!/usr/bin/env python3
import csv
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines


# ============================================================
# CONFIGURATION (NO COMMAND-LINE ARGUMENTS)
# ============================================================

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BENCHMARK = "LoopBenchmarks"

# Input CSVs (multiple runs)
INPUT_CSVS = [
    os.path.join(ROOT_DIR, "plots", "LoopBenchmarks_bubo_loops_DevineWithOutProbe_PlusPayload.csv"),
]

# Outputs
PLOTS_DIR = os.path.join(ROOT_DIR, "plots")
OUT_PNG = os.path.join(PLOTS_DIR, "LoopBenchmarks_bubo_loops_median_errorbars2.png")
OUT_CSV = os.path.join(PLOTS_DIR, "LoopBenchmarks_bubo_loops_median_errorbars2.csv")

# Plot settings
RUNTIME_SHARE_THRESHOLD = 2.0  # percent
FIGSIZE = (20, 14.5)

MIN_RUNTIME_SHARE_TO_INCLUDE = 1.0
# --- NEW: console stats knobs ---
PRINT_ALL_DIFFS = True          # if False, print only loops passing runtime threshold
SORT_DIFFS_BY = "runtime"       # "runtime" or "absdiff" or "comp"


# ============================================================
# LEGEND
# ============================================================

legend_handles = [
    mpatches.Patch(color="tab:blue", label="Per-loop slowdown (pure loops, LCC=0)"),
    mpatches.Patch(color="tab:orange", label="Per-loop slowdown (non-pure, LCC>0)"),
    mpatches.Patch(color="tab:gray", label="Per-loop median (VTune)"),
    mlines.Line2D([], [], color="black", linestyle="--", label="Program slowdown"),
]


# ============================================================
# HELPERS
# ============================================================

def median(xs: List[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return xs[n // 2]
    return (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def parse_float(x: str) -> Optional[float]:
    if x is None:
        return None
    x = str(x).strip()
    if not x:
        return None
    try:
        return float(x)
    except ValueError:
        return None


@dataclass(frozen=True)
class Key:
    comp_id: int
    loop_id: int
    method: str


@dataclass
class Agg:
    comp_id: int
    loop_id: int
    method: str
    comp_name: str
    loop_call_count: int

    slowdown_vals: List[float]
    runtime_share_vals: List[float]
    vtune_vals: List[float]
    prog_slowdown_vals: List[float]

    def __init__(self, comp_id, loop_id, method, comp_name, lcc):
        self.comp_id = comp_id
        self.loop_id = loop_id
        self.method = method
        self.comp_name = comp_name
        self.loop_call_count = lcc
        self.slowdown_vals = []
        self.runtime_share_vals = []
        self.vtune_vals = []
        self.prog_slowdown_vals = []


# ============================================================
# LOAD + AGGREGATE
# ============================================================

def load_and_aggregate(csv_paths: List[str]) -> Dict[Key, Agg]:
    agg: Dict[Key, Agg] = {}

    for path in csv_paths:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                comp_id = int(row["comp_id"])
                loop_id = int(row["loop_id"])
                method = row["method_dot"] or row["comp_name"]
                comp_name = row["comp_name"]
                lcc = int(row["loop_call_count"])

                key = Key(comp_id, loop_id, method)
                if key not in agg:
                    agg[key] = Agg(comp_id, loop_id, method, comp_name, lcc)

                a = agg[key]

                if (v := parse_float(row.get("slowdown_pct", ""))) is not None:
                    a.slowdown_vals.append(v)
                if (v := parse_float(row.get("runtime_share_pct", ""))) is not None:
                    a.runtime_share_vals.append(v)
                if (v := parse_float(row.get("loop_median_pct", ""))) is not None:
                    a.vtune_vals.append(v)
                if (v := parse_float(row.get("prog_slowdown_pct", ""))) is not None:
                    a.prog_slowdown_vals.append(v)

    return agg


# ============================================================
# MAIN
# ============================================================

def main():
    os.makedirs(PLOTS_DIR, exist_ok=True)

    agg = load_and_aggregate(INPUT_CSVS)

    rows = []
    for a in agg.values():
        if not a.slowdown_vals:
            continue

        vt = median(a.vtune_vals) if a.vtune_vals else ""
        rows.append({
            "comp_id": a.comp_id,
            "loop_id": a.loop_id,
            "method": a.method,
            "comp_name": a.comp_name,
            "loop_call_count": a.loop_call_count,
            "slow_median": median(a.slowdown_vals),
            "slow_min": min(a.slowdown_vals),
            "slow_max": max(a.slowdown_vals),
            "runtime_share": median(a.runtime_share_vals) if a.runtime_share_vals else 0.0,
            "vtune_median": vt,
            "prog_slowdown": median(a.prog_slowdown_vals) if a.prog_slowdown_vals else 0.0,
            "num_runs": len(a.slowdown_vals),
        })

    if not rows:
        print("[WARN] No rows aggregated from input CSVs")
        return

    # Write aggregated CSV
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    # --- NEW: console diff stats (Bubo median vs VTune median) ---
    diff_rows = []
    for r in rows:
        if r["vtune_median"] == "" or r["vtune_median"] is None:
            continue
        vt = float(r["vtune_median"])
        diff = float(r["slow_median"]) - vt  # percentage points
        diff_rows.append({
            "comp_id": r["comp_id"],
            "loop_id": r["loop_id"],
            "method": r["method"],
            "runtime_share": float(r["runtime_share"]),
            "slow_median": float(r["slow_median"]),
            "vtune_median": vt,
            "diff_pp": diff,
            "abs_diff_pp": abs(diff),
        })

    # Always drop loops below the minimum share (noise floor)
    diff_rows = [d for d in diff_rows if d["runtime_share"] >= MIN_RUNTIME_SHARE_TO_INCLUDE]

    # If you additionally want to restrict console printing to the plot threshold:
    if not PRINT_ALL_DIFFS:
        diff_rows = [d for d in diff_rows if d["runtime_share"] >= RUNTIME_SHARE_THRESHOLD]

    if SORT_DIFFS_BY == "absdiff":
        diff_rows.sort(key=lambda d: d["abs_diff_pp"], reverse=True)
    elif SORT_DIFFS_BY == "comp":
        diff_rows.sort(key=lambda d: (d["comp_id"], d["loop_id"]))
    else:  # "runtime"
        diff_rows.sort(key=lambda d: d["runtime_share"], reverse=True)

    print()
    print("============================================================")
    print(f"{BENCHMARK}: Bubo median vs VTune median (percentage-point diffs)")
    print("diff_pp = slow_median - vtune_median")
    if not PRINT_ALL_DIFFS:
        print(f"(filtered to runtime_share >= {RUNTIME_SHARE_THRESHOLD}%)")
    print("------------------------------------------------------------")

    if not diff_rows:
        print("[WARN] No loops had both Bubo + VTune medians to compare.")
    else:
        diffs = [d["diff_pp"] for d in diff_rows]
        for d in diff_rows:
            print(
                f"C{d['comp_id']} L{d['loop_id']}  "
                f"share={d['runtime_share']:.3f}%  "
                f"bubo={d['slow_median']:.3f}%  vtune={d['vtune_median']:.3f}%  "
                f"diff={d['diff_pp']:+.3f} pp  "
                f"{d['method']}"
            )

        print("------------------------------------------------------------")
        print(f"Compared loops: {len(diffs)}")
        print(f"Median diff:   {median(diffs):+.3f} pp")
        print(f"Min diff:      {min(diffs):+.3f} pp")
        print(f"Max diff:      {max(diffs):+.3f} pp")
    print("============================================================")
    print()

    # Plot selection
    plot_rows = [r for r in rows if float(r["runtime_share"]) >= RUNTIME_SHARE_THRESHOLD]
    if not plot_rows:
        print("[WARN] No loops pass runtime-share threshold")
        print(f"[OK] Wrote: {OUT_CSV}")
        return

    plot_rows.sort(key=lambda r: float(r["runtime_share"]), reverse=True)
    prog_slowdown = float(plot_rows[0]["prog_slowdown"])

    labels = [
        f"C{r['comp_id']} - L{r['loop_id']}\n{float(r['runtime_share']):.1f}%\n{r['method']}"
        for r in plot_rows
    ]

    med_vals = [float(r["slow_median"]) for r in plot_rows]
    lo_vals = [float(r["slow_min"]) for r in plot_rows]
    hi_vals = [float(r["slow_max"]) for r in plot_rows]
    yerr = [[m - l for m, l in zip(med_vals, lo_vals)], [h - m for m, h in zip(med_vals, hi_vals)]]

    vtune_vals = [float(r["vtune_median"]) if r["vtune_median"] != "" else 0.0 for r in plot_rows]
    colors = ["tab:blue" if int(r["loop_call_count"]) == 0 else "tab:orange" for r in plot_rows]

    x = list(range(len(plot_rows)))
    w = 0.40

    plt.figure(figsize=FIGSIZE)
    ax = plt.gca()

    ax.bar([i - w/2 for i in x], med_vals, width=w, color=colors)
    ax.errorbar([i - w/2 for i in x], med_vals, yerr=yerr, fmt="none", capsize=3)
    ax.bar([i + w/2 for i in x], vtune_vals, width=w, color="tab:gray")

    ax.axhline(prog_slowdown, linestyle="--", linewidth=1)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.set_ylabel("Slowdown (% change)")
    ax.set_title(f"{BENCHMARK}: per-loop slowdown (median across runs)")
    ax.legend(handles=legend_handles, fontsize=9, loc="upper left")

    plt.tight_layout()
    plt.savefig(OUT_PNG, dpi=200)
    plt.close()

    print(f"[OK] Wrote: {OUT_PNG}")
    print(f"[OK] Wrote: {OUT_CSV}")


if __name__ == "__main__":
    main()
