#!/usr/bin/env python3
import csv
import os
from typing import Dict, Tuple, List, Optional

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines


# ---------------- Legend ----------------
legend_handles = [
    mpatches.Patch(color="tab:green", label="Async per-method slowdown (samples)"),
    mpatches.Patch(color="tab:red", label="JFR per-method slowdown (samples)"),
    mpatches.Patch(color="tab:gray", label="Method median (baseline / no tools)"),
    mlines.Line2D([], [], color="black", linestyle="--", label="Program slowdown"),
]

# ---------------- Configuration ----------------
PROGRAM_OVERHEAD_CSV = "cycles_overhead.csv"

# Method medians (VTune-derived)
MEDIANS_CSV_NoTools = "Data/VTuneData/medians_95pct_weighted_noTool.csv"

# Your two Async/JFR datasets (KEEP THESE CHANGES)
ALLDEBUG_DIR = "LoopBenchmarks_AsyncJfrSlowdownRuns_AllDebug"
STRICTDEBUG_DIR = "LoopBenchmarks_AsyncJfrSlowdownRuns_StrictDebug"

# Benchmark folder
BENCHMARK = "LoopBenchmarks"  # change if needed

# Output names (keep)
OUT_PNG_NAME = "LoopBenchmarks_bubo_loops.png"
OUT_CSV_NAME = "LoopBenchmarks_bubo_loops.csv"

FIGSIZE = (22, 14.5)

# How many methods to plot (since we no longer have Bubo runtime_share)
TOP_N_METHODS_TO_PLOT = 20


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


# ---------------- Load method medians ----------------
def load_method_medians(medians_csv: str) -> Dict[str, float]:
    if not os.path.isfile(medians_csv):
        raise SystemExit(f"Cannot find medians CSV: {medians_csv}")

    with open(medians_csv, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit(f"Medians CSV has no header: {medians_csv}")

        method_col = None
        median_col = None
        for cand in ("Method", "method"):
            if cand in reader.fieldnames:
                method_col = cand
                break
        for cand in ("MedianPct_95pctNormalBlocks", "MedianPct_95pct", "median_95pct"):
            if cand in reader.fieldnames:
                median_col = cand
                break

        if method_col is None or median_col is None:
            raise SystemExit(
                f"Medians CSV header mismatch.\nFound: {reader.fieldnames}\n"
                f"Need columns like: Method and MedianPct_95pctNormalBlocks"
            )

        out: Dict[str, float] = {}
        for row in reader:
            m = (row.get(method_col) or "").strip()
            v = (row.get(median_col) or "").strip()
            if not m or not v:
                continue
            try:
                out[m] = float(v)
            except ValueError:
                continue

    print(f"[INFO] Loaded {len(out)} method medians from {medians_csv}")
    return out


# ---------------- Load Async/JFR slowdown CSVs (method-based) ----------------
def load_tool_slowdown_csv(path: str) -> Dict[str, float]:
    """
    Reads <benchmark>_<tool>_slowdown.csv produced by the method-based parser.
    Returns:
        { method_key -> slowdown_pct }

    We accept any of these method column names:
        method_key, method, method_name, top_method

    slowdown_pct can be blank; we treat blank as 0.0.
    """
    if not os.path.isfile(path):
        return {}

    out: Dict[str, float] = {}

    with open(path, newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            return out

        method_col = None
        for cand in ("method_key", "method", "method_name", "top_method"):
            if cand in r.fieldnames:
                method_col = cand
                break
        if method_col is None:
            raise SystemExit(
                f"CSV header mismatch in {path}. Missing a method column. "
                f"Expected one of: method_key/method/method_name/top_method. "
                f"Found: {r.fieldnames}"
            )

        if "slowdown_pct" not in r.fieldnames:
            raise SystemExit(
                f"CSV header mismatch in {path}. Missing column: slowdown_pct. "
                f"Found: {r.fieldnames}"
            )

        for row in r:
            mk = (row.get(method_col) or "").strip()
            if not mk:
                continue

            s = (row.get("slowdown_pct") or "").strip()
            if not s:
                val = 0.0
            else:
                try:
                    val = float(s)
                except ValueError:
                    val = 0.0

            out[mk] = val

    return out


def slowdown_csv_paths(dataset_dir: str, benchmark: str) -> Tuple[str, str]:
    """
    Returns:
      <dataset_dir>/async_slowdown/<benchmark>_async_slowdown.csv
      <dataset_dir>/jfr_slowdown/<benchmark>_jfr_slowdown.csv
    """
    async_path = os.path.join(dataset_dir, "async_slowdown", f"{benchmark}_async_slowdown.csv")
    jfr_path = os.path.join(dataset_dir, "jfr_slowdown", f"{benchmark}_jfr_slowdown.csv")
    return async_path, jfr_path


# ---------------- Core pipeline per dataset ----------------
def run_for_dataset(dataset_name: str, dataset_dir: str) -> None:
    print(f"\n==================== {dataset_name} ====================")

    out_dir = os.path.join(".", f"Plots_{dataset_name}")
    os.makedirs(out_dir, exist_ok=True)
    out_png = os.path.join(out_dir, OUT_PNG_NAME)
    out_csv = os.path.join(out_dir, OUT_CSV_NAME)

    # Medians (no-tools only)
    method_medians_notools = load_method_medians(MEDIANS_CSV_NoTools)

    # Program slowdown line
    prog_slowdown_map = load_program_overheads(PROGRAM_OVERHEAD_CSV)
    prog_slowdown_pct = prog_slowdown_map.get(BENCHMARK, 0.0)

    # Async/JFR (method-based, from CSVs)
    async_csv, jfr_csv = slowdown_csv_paths(dataset_dir, BENCHMARK)
    async_slowdown_by_method = load_tool_slowdown_csv(async_csv)
    jfr_slowdown_by_method = load_tool_slowdown_csv(jfr_csv)

    if not async_slowdown_by_method:
        print(f"[WARN] No async slowdown CSV data found at: {async_csv}")
    else:
        print(f"[INFO] Loaded {len(async_slowdown_by_method)} async method rows from {async_csv}")

    if not jfr_slowdown_by_method:
        print(f"[WARN] No jfr slowdown CSV data found at: {jfr_csv}")
    else:
        print(f"[INFO] Loaded {len(jfr_slowdown_by_method)} jfr method rows from {jfr_csv}")

    # Build rows from method union
    rows = []
    all_methods = sorted(set(async_slowdown_by_method) | set(jfr_slowdown_by_method))
    for mk in all_methods:
        async_pct = async_slowdown_by_method.get(mk, 0.0)
        jfr_pct = jfr_slowdown_by_method.get(mk, 0.0)
        med_notools = method_medians_notools.get(mk, "")

        rows.append(
            {
                "benchmark": BENCHMARK,
                "method_key": mk,
                "async_slowdown_pct": async_pct,
                "jfr_slowdown_pct": jfr_pct,
                "method_median_notools_95pct": med_notools,
                "prog_slowdown_pct": prog_slowdown_pct,
            }
        )

    if not rows:
        raise SystemExit("No Async/JFR method rows found to write rows/plot.")

    # Write CSV (all methods)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    # =========================
    # NEW: median diffs vs VTune
    # =========================
    async_diffs = []
    jfr_diffs = []
    missing_vtune = 0

    for r in rows:
        vt = r["method_median_notools_95pct"]
        if vt == "" or vt is None:
            missing_vtune += 1
            continue
        try:
            vt = float(vt)
        except ValueError:
            missing_vtune += 1
            continue

        async_diffs.append(float(r["async_slowdown_pct"]) - vt)  # percentage points
        jfr_diffs.append(float(r["jfr_slowdown_pct"]) - vt)      # percentage points

    print("\n[STATS] Tool vs VTune median (percentage-point diffs)")
    print(f"        diff_pp = tool_slowdown_pct - vtune_median_notools_95pct")
    print(f"        methods total: {len(rows)}")
    print(f"        methods missing vtune median: {missing_vtune}")

    if async_diffs:
        print(f"        Async: compared={len(async_diffs)}  median_diff={median(async_diffs):+.3f} pp  "
              f"min={min(async_diffs):+.3f}  max={max(async_diffs):+.3f}")
    else:
        print("        Async: no comparable methods (no vtune medians)")

    if jfr_diffs:
        print(f"        JFR:   compared={len(jfr_diffs)}  median_diff={median(jfr_diffs):+.3f} pp  "
              f"min={min(jfr_diffs):+.3f}  max={max(jfr_diffs):+.3f}")
    else:
        print("        JFR:   no comparable methods (no vtune medians)")

    # Choose what to plot: top-N by max(async, jfr)
    plot_entries = sorted(
        rows,
        key=lambda r: max(r["async_slowdown_pct"], r["jfr_slowdown_pct"]),
        reverse=True,
    )[:TOP_N_METHODS_TO_PLOT]

    def short_method(m: str, max_len: int = 40) -> str:
        if not m:
            return "<unknown>"
        return m if len(m) <= max_len else m[: max_len - 1] + "…"

    labels = [short_method(r["method_key"]) for r in plot_entries]

    x = list(range(len(plot_entries)))
    width = 0.22  # 3 bars

    plt.figure(figsize=FIGSIZE)
    ax = plt.gca()

    ax.bar([i - width for i in x],
           [r["async_slowdown_pct"] for r in plot_entries],
           width,
           color="tab:green")

    ax.bar([i + 0.0 for i in x],
           [r["jfr_slowdown_pct"] for r in plot_entries],
           width,
           color="tab:red")

    ax.bar([i + width for i in x],
           [float(r["method_median_notools_95pct"] or 0.0) for r in plot_entries],
           width,
           color="tab:gray")

    ax.axhline(prog_slowdown_pct, linestyle="--", color="black")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.set_ylabel("Slowdown (%)")
    ax.set_title(f"{BENCHMARK} — Async vs JFR [{dataset_name}]")
    ax.legend(handles=legend_handles)

    plt.tight_layout()
    plt.savefig(out_png, dpi=200)
    plt.close()

    print(f"[OK] Wrote: {out_png}")
    print(f"[OK] Wrote: {out_csv}")

def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return xs[n // 2]
    return (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def main():
    run_for_dataset("AllDebug", ALLDEBUG_DIR)
    run_for_dataset("StrictDebug", STRICTDEBUG_DIR)


if __name__ == "__main__":
    main()
