#!/usr/bin/env python3

"""
Master comparison script: Bubo (FirstTest) vs Async (SecondTest_Async).

Run this from LoopProfiling/:

    python3 master_bubo_vs_async.py

It will:

  1. Ensure the two per-experiment scripts have been run:
       - FirstTest/loop_slowdown_analysis.py
       - SecondTest_Async/marker_sample_extractor.py

  2. For every benchmark that exists in BOTH:
       FirstTest/<bench>/loop_cycle_percent_change_exclusive.csv  (preferred)
         or FirstTest/<bench>/loop_cycle_percent_change.csv       (fallback)
       SecondTest_Async/<bench>/loop_sample_percent_change.csv

     it produces in the CURRENT DIRECTORY (LoopProfiling/):

       <bench>_Bubo_vs_Async.csv
       <bench>_Bubo_vs_Async.png

Combined plot:

  - Uses Bubo **exclusive** data when available (so it matches Async's
    per-loop exclusive samples better).

  - X-axis: loop *rank* by baseline share (1 = most important),
    but tick labels show, per rank:
        Line 1: "B xx.x% / A yy.y%"   (share of total baseline time/samples)
        Line 2: "B Cc-Ll / A Cc-Ll"   (CompId/LoopId in Bubo vs Async)

  - Bars:
        * Bubo (FirstTest) = dark blue
        * Async (SecondTest) = green

  - Two horizontal lines:
        * Bubo total % change (runtime)
        * Async total % change (samples)

  - Title also reports how much of the total baseline we cover:
        "(Bubo cover XX.X%, Async cover YY.Y%)"
"""

from pathlib import Path
import csv
import math
import re
import subprocess
import sys

import matplotlib.pyplot as plt


# --------------------------------------------------------------------------------------
# Common util
# --------------------------------------------------------------------------------------

def percent_change(base, new):
    if base == 0:
        return float("nan")
    return (new - base) / float(base) * 100.0


# --------------------------------------------------------------------------------------
# Ensuring FirstTest / SecondTest_Async scripts have run
# --------------------------------------------------------------------------------------

def ensure_firsttest_results(root: Path):
    """
    Ensure FirstTest/loop_slowdown_analysis.py has been run.
    We check for loop_cycle_percent_change.csv AND
    loop_cycle_percent_change_exclusive.csv in each benchmark dir.
    If any is missing, we run the script.
    """
    first_root = root / "FirstTest"
    script = first_root / "loop_slowdown_analysis.py"

    if not first_root.is_dir():
        print("[WARN] FirstTest/ directory not found, skipping Bubo side.")
        return

    if not script.exists():
        print("[WARN] FirstTest/loop_slowdown_analysis.py not found, skipping Bubo side.")
        return

    need_run = False
    for bench_dir in sorted(first_root.iterdir()):
        if bench_dir.is_dir():
            incl_csv = bench_dir / "loop_cycle_percent_change.csv"
            excl_csv = bench_dir / "loop_cycle_percent_change_exclusive.csv"
            if not incl_csv.exists() or not excl_csv.exists():
                need_run = True
                break

    if need_run:
        print("[INFO] Running FirstTest/loop_slowdown_analysis.py ...")
        try:
            subprocess.run(
                [sys.executable, script.name],
                cwd=str(first_root),
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Failed running loop_slowdown_analysis.py: {e}")
    else:
        print("[INFO] FirstTest results already present, not re-running.")


def ensure_secondtest_results(root: Path):
    """
    Ensure SecondTest_Async/marker_sample_extractor.py has been run.
    We check for loop_sample_percent_change.csv in each benchmark dir.
    If any is missing, we run the script.
    """
    second_root = root / "SecondTest_Async"
    script = second_root / "marker_sample_extractor.py"

    if not second_root.is_dir():
        print("[WARN] SecondTest_Async/ directory not found, skipping Async side.")
        return

    if not script.exists():
        print("[WARN] SecondTest_Async/marker_sample_extractor.py not found, skipping Async side.")
        return

    need_run = False
    for bench_dir in sorted(second_root.iterdir()):
        if bench_dir.is_dir() and not bench_dir.name.startswith("."):
            csv_path = bench_dir / "loop_sample_percent_change.csv"
            if not csv_path.exists():
                need_run = True
                break

    if need_run:
        print("[INFO] Running SecondTest_Async/marker_sample_extractor.py ...")
        try:
            subprocess.run(
                [sys.executable, script.name],
                cwd=str(second_root),
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Failed running marker_sample_extractor.py: {e}")
    else:
        print("[INFO] SecondTest_Async results already present, not re-running.")


# --------------------------------------------------------------------------------------
# Loading CSVs for each benchmark
# --------------------------------------------------------------------------------------

def load_bubo_csv(first_root: Path, bench: str):
    """
    Load Bubo data for a benchmark.

    Prefer the EXCLUSIVE CSV:
        loop_cycle_percent_change_exclusive.csv

    Fall back to the original inclusive CSV:
        loop_cycle_percent_change.csv
    """
    bench_dir = first_root / bench
    excl_path = bench_dir / "loop_cycle_percent_change_exclusive.csv"
    incl_path = bench_dir / "loop_cycle_percent_change.csv"

    path = None
    if excl_path.exists():
        path = excl_path
    elif incl_path.exists():
        path = incl_path
    else:
        return None

    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    # sort by baseline share descending
    def share(r):
        for key in ("BaselineSharePercentExclusive", "BaselineSharePercent"):
            try:
                if key in r and r[key] != "":
                    return float(r[key])
            except ValueError:
                continue
        return 0.0

    rows.sort(key=share, reverse=True)
    return rows


def load_async_csv(second_root: Path, bench: str):
    path = second_root / bench / "loop_sample_percent_change.csv"
    if not path.exists():
        return None

    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    # sort by baseline share descending
    def share(r):
        try:
            return float(r["BaselineSharePercent"])
        except (KeyError, ValueError):
            return 0.0

    rows.sort(key=share, reverse=True)
    return rows


# --------------------------------------------------------------------------------------
# Total % change for Bubo (FirstTest) - runtime
# --------------------------------------------------------------------------------------

TOTAL_RUNTIME_RE = re.compile(r"Total Runtime:\s+(\d+)us")

def _parse_total_runtime(path: Path):
    if not path.exists():
        return None
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = TOTAL_RUNTIME_RE.search(line)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    return None
    return None


def compute_bubo_total_pct(first_root: Path, bench: str):
    bench_dir = first_root / bench

    # Try NoSlowdown/Slowdown first
    ns = bench_dir / "NoSlowdown.txt"
    sl = bench_dir / "Slowdown.txt"

    # Fallback to *LIR_false.out / *LIR_true.out if needed
    if not ns.exists() or not sl.exists():
        false_files = sorted(bench_dir.glob("*LIR_false.out"))
        true_files = sorted(bench_dir.glob("*LIR_true.out"))
        if false_files and true_files:
            ns = false_files[0]
            sl = true_files[0]
        else:
            return float("nan")

    base = _parse_total_runtime(ns)
    slow = _parse_total_runtime(sl)
    if base is None or slow is None:
        return float("nan")
    return percent_change(base, slow)


# --------------------------------------------------------------------------------------
# Total % change for Async (SecondTest_Async) - samples
# --------------------------------------------------------------------------------------

TOTAL_SAMPLES_RE = re.compile(r"^Total samples\s*:\s*(\d+)")

def _parse_total_samples(path: Path):
    if not path.exists():
        return None
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = TOTAL_SAMPLES_RE.match(line.strip())
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    return None
    return None


def compute_async_total_pct(second_root: Path, bench: str):
    bench_dir = second_root / bench

    base_file = None
    slow_file = None

    # Match marker_sample_extractor.py assumptions
    for f in bench_dir.iterdir():
        if f.suffix == ".txt" and "GTAssignDebug_true" in f.name:
            if "LIR_false" in f.name:
                base_file = f
            elif "LIR_true" in f.name:
                slow_file = f

    if not base_file or not slow_file:
        return float("nan")

    base = _parse_total_samples(base_file)
    slow = _parse_total_samples(slow_file)
    if base is None or slow is None:
        return float("nan")

    return percent_change(base, slow)


# --------------------------------------------------------------------------------------
# Combined CSV + plot
# --------------------------------------------------------------------------------------

def write_combined_csv(out_path: Path, bubo_rows, async_rows):
    """
    Write a combined CSV that aligns loops by rank:
    rank 1 = top Bubo loop + top Async loop by baseline share, etc.

    Uses Bubo exclusive columns if present, otherwise inclusive.
    """
    k = min(len(bubo_rows), len(async_rows))
    if k == 0:
        return

    fieldnames = [
        "Rank",
        "BuboCompId", "BuboLoopId",
        "BuboPercentChangeCycles", "BuboBaselineSharePercent",
        "AsyncCompId", "AsyncLoopId",
        "AsyncPercentChangeSamples", "AsyncBaselineSharePercent",
    ]

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        for i in range(k):
            br = bubo_rows[i]
            ar = async_rows[i]

            # Prefer exclusive columns, fall back to inclusive
            b_pct = br.get("PercentChangeCyclesExclusive",
                           br.get("PercentChangeCycles", ""))
            b_share = br.get("BaselineSharePercentExclusive",
                             br.get("BaselineSharePercent", ""))

            row = {
                "Rank": i + 1,
                "BuboCompId": br.get("CompId", ""),
                "BuboLoopId": br.get("LoopId", ""),
                "BuboPercentChangeCycles": b_pct,
                "BuboBaselineSharePercent": b_share,
                "AsyncCompId": ar.get("CompId", ""),
                "AsyncLoopId": ar.get("LoopId", ""),
                "AsyncPercentChangeSamples": ar.get("PercentChangeSamples", ""),
                "AsyncBaselineSharePercent": ar.get("BaselineSharePercent", ""),
            }
            w.writerow(row)

    print(f"  -> wrote combined CSV: {out_path.name}")


def create_combined_plot(
    bench: str,
    bubo_rows,
    async_rows,
    bubo_total_pct,
    async_total_pct,
    out_path: Path,
):
    k = min(len(bubo_rows), len(async_rows))
    if k == 0:
        print(f"  [WARN] No overlapping ranks to plot for {bench}")
        return

    # Extract Bubo values (prefer exclusive)
    bubo_pct = []
    bubo_shares = []
    bubo_ids = []
    for i in range(k):
        br = bubo_rows[i]
        # percent change
        b_p = None
        for key in ("PercentChangeCyclesExclusive", "PercentChangeCycles"):
            try:
                if key in br and br[key] != "":
                    b_p = float(br[key])
                    break
            except ValueError:
                continue
        if b_p is None:
            b_p = float("nan")
        bubo_pct.append(b_p)

        # share
        b_s = 0.0
        found_share = False
        for key in ("BaselineSharePercentExclusive", "BaselineSharePercent"):
            try:
                if key in br and br[key] != "":
                    b_s = float(br[key])
                    found_share = True
                    break
            except ValueError:
                continue
        if not found_share:
            b_s = 0.0
        bubo_shares.append(b_s)

        b_comp = br.get("CompId", "")
        b_loop = br.get("LoopId", "")
        bubo_ids.append((b_comp, b_loop))

    # Extract Async values
    async_pct = []
    async_shares = []
    async_ids = []
    for i in range(k):
        ar = async_rows[i]
        try:
            async_pct.append(float(ar["PercentChangeSamples"]))
        except (KeyError, ValueError):
            async_pct.append(float("nan"))
        try:
            async_shares.append(float(ar["BaselineSharePercent"]))
        except (KeyError, ValueError):
            async_shares.append(0.0)

        a_comp = ar.get("CompId", "")
        a_loop = ar.get("LoopId", "")
        async_ids.append((a_comp, a_loop))

    # Total coverage of baseline shares in the plotted set
    bubo_covered = sum(s for s in bubo_shares if not math.isnan(s))
    async_covered = sum(s for s in async_shares if not math.isnan(s))

    x = list(range(k))
    width = 0.4

    fig_width = max(10, k * 0.6)
    fig, ax = plt.subplots(figsize=(fig_width, 5))

    # Colors: fixed and consistent
    bubo_color = "tab:blue"   # dark-ish blue
    async_color = "tab:green"

    # Bubo bars (left)
    ax.bar(
        [i - width / 2.0 for i in x],
        bubo_pct,
        width=width,
        label="Bubo (FirstTest, exclusive)",
        color=bubo_color,
    )

    # Async bars (right)
    ax.bar(
        [i + width / 2.0 for i in x],
        async_pct,
        width=width,
        label="Async (SecondTest)",
        color=async_color,
        alpha=0.7,
    )

    # Horizontal lines for totals
    if not math.isnan(bubo_total_pct):
        ax.axhline(
            bubo_total_pct,
            linestyle="--",
            linewidth=1.5,
            label=f"Bubo total: {bubo_total_pct:.1f}%",
        )

    if not math.isnan(async_total_pct):
        ax.axhline(
            async_total_pct,
            linestyle=":",
            linewidth=1.5,
            label=f"Async total: {async_total_pct:.1f}%",
        )

    # X tick labels:
    # Line 1: "B xx.x% / A yy.y%"
    # Line 2: "B Cb-Lb / A Ca-La"
    xtick_labels = []
    for i in range(k):
        b_share = bubo_shares[i]
        a_share = async_shares[i]
        b_comp, b_loop = bubo_ids[i]
        a_comp, a_loop = async_ids[i]

        if math.isnan(b_share):
            b_share_str = "?"
        else:
            b_share_str = f"{b_share:.1f}%"
        if math.isnan(a_share):
            a_share_str = "?"
        else:
            a_share_str = f"{a_share:.1f}%"

        line1 = f"B {b_share_str} / A {a_share_str}"
        line2 = f"B C{b_comp}-L{b_loop} / A C{a_comp}-L{a_loop}"
        xtick_labels.append(line1 + "\n" + line2)

    ax.set_xticks(x)
    ax.set_xticklabels(xtick_labels)
    # shrink font a bit to make the multi-line labels readable
    for label in ax.get_xticklabels():
        label.set_fontsize(8)

    ax.set_xlabel("Loop rank by baseline share (left: Bubo, right: Async)")
    ax.set_ylabel("% change (slowdown vs baseline)")

    title = (
        f"{bench} – Bubo (FirstTest, exclusive) vs Async (SecondTest)\n"
        f"(Bubo cover {bubo_covered:.1f}% of baseline, "
        f"Async cover {async_covered:.1f}% of baseline)"
    )
    ax.set_title(title)

    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)

    print(f"  -> wrote combined plot: {out_path.name}")


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

def main():
    root = Path(".").resolve()
    print("[INFO] LoopProfiling root:", root)

    ensure_firsttest_results(root)
    ensure_secondtest_results(root)

    first_root = root / "FirstTest"
    second_root = root / "SecondTest_Async"

    if not first_root.is_dir() or not second_root.is_dir():
        print("[ERROR] Missing FirstTest/ or SecondTest_Async/, nothing to compare.")
        return

    first_benches = {
        d.name for d in first_root.iterdir() if d.is_dir() and not d.name.startswith(".")
    }
    second_benches = {
        d.name for d in second_root.iterdir() if d.is_dir() and not d.name.startswith(".")
    }

    common = sorted(first_benches & second_benches)
    if not common:
        print("[WARN] No common benchmarks between FirstTest and SecondTest_Async.")
        return

    print("[INFO] Common benchmarks:", ", ".join(common))

    for bench in common:
        print(f"\n[INFO] Processing benchmark: {bench}")

        bubo_rows = load_bubo_csv(first_root, bench)
        async_rows = load_async_csv(second_root, bench)

        if not bubo_rows:
            print("  [WARN] Missing or empty Bubo CSV, skipping.")
            continue
        if not async_rows:
            print("  [WARN] Missing or empty Async CSV, skipping.")
            continue

        bubo_total_pct = compute_bubo_total_pct(first_root, bench)
        async_total_pct = compute_async_total_pct(second_root, bench)

        # Combined CSV + plot in the root (LoopProfiling/)
        out_csv = root / f"{bench}_Bubo_vs_Async.csv"
        out_png = root / f"{bench}_Bubo_vs_Async.png"

        write_combined_csv(out_csv, bubo_rows, async_rows)
        create_combined_plot(
            bench,
            bubo_rows,
            async_rows,
            bubo_total_pct,
            async_total_pct,
            out_png,
        )


if __name__ == "__main__":
    main()
