#!/usr/bin/env python3
import os
import csv
import subprocess
from typing import Dict, Optional, Tuple, List

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines

# --------------------------------------------------------------------
# Config / paths
# --------------------------------------------------------------------

RUNTIME_CSV = "runtime_overheads.csv"

BUBO_CSV_DIR   = "BuboLoopCSVs"
ASYNC_CSV_DIR  = "AsyncLoopCSVs"
JFR_CSV_DIR    = "JfrLoopCSVs"

BUBO_SCRIPT   = "bubo_loop_slowdowns.py"
ASYNC_SCRIPT  = "async_loop_slowdowns.py"
JFR_SCRIPT    = "JFR_loop_slowdowns.py"

COMBINED_PLOT_DIR = "CombinedToolPlots"

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------


def load_runtime_overheads(path: str) -> Dict[str, float]:
    """
    Load runtime_overheads.csv and return:
        { benchmark -> slowdown_noBubo_pct }
    """
    slowdown_map: Dict[str, float] = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bm = row["benchmark"]
            slowdown_map[bm] = float(row["slowdown_noBubo_pct"])
    return slowdown_map


def get_benchmarks_from_runtime_csv(path: str) -> List[str]:
    """Return the list of benchmarks in runtime_overheads.csv."""
    benchmarks: List[str] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            benchmarks.append(row["benchmark"])
    return benchmarks


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def need_to_run_tool(csv_dir: str) -> bool:
    """
    Decide if we need to (re-)run a tool script.

    New, simpler logic:
      - If the directory does not exist -> run.
      - If the directory exists but has *no* .csv files -> run.
      - Otherwise -> assume it’s already been run.
    """
    if not os.path.isdir(csv_dir):
        return True

    for name in os.listdir(csv_dir):
        if name.endswith(".csv"):
            return False  # at least one csv present

    return True  # dir exists but empty / no csvs


def run_subprocess_script(script: str) -> None:
    """Run a Python script in this directory."""
    print(f"Running {script} ...")
    subprocess.run(["python3", script], check=True)
    print(f"... {script} finished\n")


def load_top_loop_from_csv(path: str, tool_name: str,
                           has_loop_call_count: bool) -> Optional[Dict]:
    """
    Load a per-tool CSV and return the row for the loop with the largest
    runtime_share_pct.

    Returns a dict with at least:
      - tool
      - comp_id
      - loop_id
      - slowdown_pct
      - runtime_share_pct
      - loop_call_count (0 if not present / tool doesn't use it)
    or None if CSV is empty.
    """
    if not os.path.isfile(path):
        return None

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return None

    # pick the row with max runtime_share_pct
    rows.sort(key=lambda r: float(r["runtime_share_pct"]), reverse=True)
    top = rows[0]

    comp_id = int(top["comp_id"])
    loop_id = int(top["loop_id"])
    slowdown_pct = float(top["slowdown_pct"])
    runtime_share_pct = float(top["runtime_share_pct"])

    if has_loop_call_count:
        loop_call_count = int(top["loop_call_count"])
    else:
        loop_call_count = 0

    return {
        "tool": tool_name,
        "comp_id": comp_id,
        "loop_id": loop_id,
        "slowdown_pct": slowdown_pct,
        "runtime_share_pct": runtime_share_pct,
        "loop_call_count": loop_call_count,
    }


# --------------------------------------------------------------------
# Combined plotting
# --------------------------------------------------------------------


def make_combined_plot(
    benchmark: str,
    prog_slowdown_pct: float,
    bubo_rows: List[Dict],
    async_rows: List[Dict],
    jfr_rows: List[Dict],
    top_n: int = 5,
) -> None:
    """
    Plot top-N loops per tool, arranged into N columns:
       Column 1 = each tool's #1 loop (highest runtime share)
       Column 2 = each tool's #2 loop
       ...
    Each column shows up to three bars: Bubo, Async, JFR.
    """
    ensure_dir(COMBINED_PLOT_DIR)

    # Pad each tool list to length top_n with None
    def pad(lst):
        lst = lst[:top_n]
        while len(lst) < top_n:
            lst.append(None)
        return lst

    bubo_rows  = pad(bubo_rows)
    async_rows = pad(async_rows)
    jfr_rows   = pad(jfr_rows)

    # Flatten into plotting arrays
    x_positions = []
    heights     = []
    colors      = []
    labels      = []

    x = 0
    for i in range(top_n):
        # column i: Bubo, Async, JFR
        for tool_name, row in [
            ("Bubo",  bubo_rows[i]),
            ("Async", async_rows[i]),
            ("JFR",   jfr_rows[i]),
        ]:
            if row is not None:
                heights.append(row["slowdown_pct"])
                share = row["runtime_share_pct"]
                cid = row["comp_id"]
                lid = row["loop_id"]

                # colouring
                if tool_name == "Bubo":
                    if row["loop_call_count"] == 0:
                        colors.append("tab:blue")
                    else:
                        colors.append("tab:orange")
                elif tool_name == "Async":
                    colors.append("tab:green")
                else:
                    colors.append("tab:red")

                labels.append(f"{tool_name}\nC{cid}-L{lid}\n{share:.1f}%")
            else:
                heights.append(0)
                colors.append("tab:gray")
                labels.append(f"{tool_name}\n---\n---")

            x_positions.append(x)
            x += 1

        # Insert a spacing gap between columns
        x += 1.0

    plt.figure(figsize=(max(10, top_n * 3.5), 5))
    ax = plt.gca()
    ax.bar(x_positions, heights, color=colors)

    ax.axhline(
        prog_slowdown_pct,
        linestyle="--",
        color="black",
        label=f"Program slowdown: {prog_slowdown_pct:.1f}%"
    )

    ax.set_xticks(x_positions)
    ax.set_xticklabels(labels, rotation=45, ha="right")

    ax.set_ylabel("Loop slowdown (% change vs baseline)")
    ax.set_title(f"{benchmark}: Top {top_n} loops per tool")

    legend_handles = [
        mpatches.Patch(color="tab:blue", label="Bubo pure"),
        mpatches.Patch(color="tab:orange", label="Bubo non-pure"),
        mpatches.Patch(color="tab:green", label="Async"),
        mpatches.Patch(color="tab:red", label="JFR"),
        mlines.Line2D([], [], linestyle="--", color="black",
                      label="Program slowdown"),
    ]
    ax.legend(handles=legend_handles, fontsize=8, loc="best")

    plt.tight_layout()
    out_path = os.path.join(COMBINED_PLOT_DIR, f"{benchmark}_combined_top{top_n}.png")
    plt.savefig(out_path, dpi=200)
    plt.close()

    print(f"[{benchmark}] Combined plot written → {out_path}")



# --------------------------------------------------------------------
# Main
# --------------------------------------------------------------------


def main():
    # 1) basic checks + benchmark list
    if not os.path.isfile(RUNTIME_CSV):
        raise SystemExit(f"Cannot find {RUNTIME_CSV} in current directory.")

    benchmarks = get_benchmarks_from_runtime_csv(RUNTIME_CSV)
    print("Benchmarks (from runtime_overheads.csv):", ", ".join(benchmarks))

    slowdown_map = load_runtime_overheads(RUNTIME_CSV)

    # 2) ensure per-tool CSVs exist; run scripts if necessary
    if need_to_run_tool(BUBO_CSV_DIR):
        run_subprocess_script(BUBO_SCRIPT)

    if need_to_run_tool(ASYNC_CSV_DIR):
        run_subprocess_script(ASYNC_SCRIPT)

    if need_to_run_tool(JFR_CSV_DIR):
        run_subprocess_script(JFR_SCRIPT)

    # 3) For each benchmark, load top loop per tool and make combined plot
    for bm in benchmarks:
        prog_slowdown_pct = slowdown_map.get(bm, 0.0)

        bubo_csv  = os.path.join(BUBO_CSV_DIR,  f"bubo_loops_{bm}.csv")
        async_csv = os.path.join(ASYNC_CSV_DIR, f"AsyncLoops_{bm}.csv")
        jfr_csv   = os.path.join(JFR_CSV_DIR,   f"JfrLoops_{bm}.csv")

        bubo_row  = load_top_loop_from_csv(bubo_csv,  "Bubo",  has_loop_call_count=True)
        async_row = load_top_loop_from_csv(async_csv, "Async", has_loop_call_count=False)
        jfr_row   = load_top_loop_from_csv(jfr_csv,   "JFR",   has_loop_call_count=False)

        make_combined_plot(bm,prog_slowdown_pct,bubo_rows = load_top_n(bubo_csv, "Bubo", True, n=5),async_rows = load_top_n(async_csv, "Async", False, n=5),jfr_rows = load_top_n(jfr_csv, "JFR", False, n=5),)


def load_top_n(path, tool_name, has_loop_call_count, n=5):
    if not os.path.isfile(path):
        return []

    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))

    rows.sort(key=lambda r: float(r["runtime_share_pct"]), reverse=True)
    rows = rows[:n]

    out = []
    for r in rows:
        out.append({
            "tool": tool_name,
            "comp_id": int(r["comp_id"]),
            "loop_id": int(r["loop_id"]),
            "slowdown_pct": float(r["slowdown_pct"]),
            "runtime_share_pct": float(r["runtime_share_pct"]),
            "loop_call_count": int(r["loop_call_count"]) if has_loop_call_count else 0,
        })
    return out


if __name__ == "__main__":
    main()
