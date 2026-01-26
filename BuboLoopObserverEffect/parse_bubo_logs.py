#!/usr/bin/env python3
"""
bubo_parse_and_compare.py

What this script does

A) Parse all Bubo logs under:
   /home/hb478/repos/BuboExperiments/BuboLoopObserverEffect/bubo_runs*/logs/*.log

B) Write raw CSVs:
   <root>/parsed/bubo_logs.csv
   <root>/parsed/bubo_loops.csv

C) Aggregate across runs (medians) for each unique loop:
     (benchmark, nested_mode, comp_id, loop_id)

D) Compare NoNested vs Nested (Nested is baseline), only for loops present in BOTH.

E) Filter out loops whose *baseline* share of total cycles is < 1%
   baseline share = median(loop_cycles_nested) / median(total_cycles_nested_for_benchmark)

F) Per benchmark, plot per-loop percentage change:
     pct_change = (median_no_nested - median_nested) / median_nested * 100
   Ordered by baseline share (descending).

G) Final final plot (requested):
   A box plot per benchmark, where each benchmark's distribution is the per-loop absolute
   percentage changes (|pct_change|), weighted by baseline share (contribution).
   This gives one box per benchmark.

   Prints summary stats:
   - weighted median absolute change (%) per benchmark
   - number of loops measured (after filters and intersection)
   - coverage: sum of baseline shares of included loops (fraction of total cycles)

Outputs:
  <root>/parsed/agg_loop_medians.csv
  <root>/parsed/compare_nested_vs_nonested.csv
  <root>/parsed/plots/<benchmark>_nested_vs_nonested_pct_change.pdf
  <root>/parsed/plots/boxplot_weighted_abs_change_per_benchmark.pdf
"""

from __future__ import annotations

import csv
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import matplotlib.pyplot as plt


# =========================
# Hard-coded paths
# =========================

ROOT = Path("/home/hb478/repos/BuboExperiments/BuboLoopObserverEffect").resolve()
OUT_DIR = (ROOT / "parsed").resolve()
PLOTS_DIR = OUT_DIR / "plots"

# Filter threshold
MIN_BASELINE_SHARE = 0.01  # 1%


# =========================
# Regex patterns
# =========================

TOTAL_RE = re.compile(r"^Bubo\.RDTSC\.Harness\.main Total RDTSC cycles:\s*(\d+)\s*$")
COMP_RE = re.compile(r"^Comp\s+(?P<comp_id>\d+)\s+\((?P<comp_name>.+?)\)\s+loops:\s*$")
ENC_RE = re.compile(r"^Found Encoding\s*:\s*(?P<enc>.*)\s*$")
LOOP_RE = re.compile(r"^\s*loop\s+(?P<loop_id>\d+)\s+Cycles:\s*(?P<cycles>\d+)\b")


# =========================
# Data containers
# =========================

@dataclass
class LogSummary:
    run_dir: str
    nested_mode: str
    benchmark: str
    log_path: str
    total_cycles: Optional[int]
    comp_count: int
    loop_count: int


@dataclass
class LoopRow:
    run_dir: str
    nested_mode: str
    benchmark: str
    log_path: str
    total_cycles: Optional[int]
    comp_id: int
    comp_name: str
    encoding: Optional[str]
    loop_id: int
    loop_cycles: int


@dataclass
class AggLoop:
    benchmark: str
    nested_mode: str
    comp_id: int
    loop_id: int
    comp_name: str
    encoding: str
    n_runs: int
    median_loop_cycles: int


# =========================
# Helpers
# =========================

def median_int(values: List[int]) -> int:
    return int(statistics.median(values))


def safe_pct_change(baseline: int, new: int) -> float:
    if baseline <= 0:
        return 0.0
    return ((new - baseline) / baseline) * 100.0


def infer_nested_mode(run_dir_name: str) -> str:
    name = run_dir_name.lower()
    if "bubononested" in name or "nonested" in name:
        return "NoNested"
    if "bubonested" in name or "nested" in name:
        return "Nested"
    return "Unknown"


def infer_benchmark_from_filename(path: Path) -> str:
    stem = path.name
    if stem.endswith(".log"):
        stem = stem[:-4]
    if stem.endswith("_BuboOn"):
        return stem[:-7]
    if stem.endswith("_BuboOff"):
        return stem[:-8]
    return stem


def iter_run_dirs(root: Path) -> Iterable[Path]:
    for p in sorted(root.iterdir()):
        if p.is_dir() and p.name.startswith("bubo_runs"):
            yield p


def iter_log_files(run_dir: Path) -> Iterable[Path]:
    logs = run_dir / "logs"
    if not logs.is_dir():
        return
    for p in sorted(logs.glob("*.log")):
        if p.is_file():
            yield p


def write_csv(path: Path, fieldnames: List[str], dict_rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(dict_rows)


def short_comp_name(name: str, max_len: int = 60) -> str:
    s = (name or "").strip().replace("\n", " ")
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def weighted_median(values: List[float], weights: List[float]) -> float:
    """
    Weighted median: smallest x where cumulative weight >= 0.5 total weight.
    Assumes weights are non-negative.
    """
    if not values or not weights or len(values) != len(weights):
        return float("nan")
    pairs = sorted(zip(values, weights), key=lambda t: t[0])
    total_w = sum(w for _, w in pairs if w > 0.0)
    if total_w <= 0.0:
        return float("nan")
    cum = 0.0
    for v, w in pairs:
        if w <= 0.0:
            continue
        cum += w
        if cum >= 0.5 * total_w:
            return float(v)
    return float(pairs[-1][0])


def weighted_percentile(values: List[float], weights: List[float], q: float) -> float:
    """
    Weighted percentile (0..1). Uses the same cumulative-weight threshold idea.
    """
    if not values or not weights or len(values) != len(weights):
        return float("nan")
    if q <= 0.0:
        return float(min(values))
    if q >= 1.0:
        return float(max(values))
    pairs = sorted(zip(values, weights), key=lambda t: t[0])
    total_w = sum(w for _, w in pairs if w > 0.0)
    if total_w <= 0.0:
        return float("nan")
    target = q * total_w
    cum = 0.0
    for v, w in pairs:
        if w <= 0.0:
            continue
        cum += w
        if cum >= target:
            return float(v)
    return float(pairs[-1][0])


# =========================
# Parsing
# =========================

def parse_log_file(
    log_path: Path,
    run_dir_name: str,
    nested_mode: str,
    benchmark: str,
) -> Tuple[LogSummary, List[LoopRow]]:
    total_cycles: Optional[int] = None
    current_comp_id: Optional[int] = None
    current_comp_name: Optional[str] = None
    current_encoding: Optional[str] = None

    seen_comps: Dict[int, str] = {}
    rows: List[LoopRow] = []

    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")

            if total_cycles is None:
                m = TOTAL_RE.match(line)
                if m:
                    try:
                        total_cycles = int(m.group(1))
                    except ValueError:
                        total_cycles = None
                    continue

            m = COMP_RE.match(line)
            if m:
                current_comp_id = int(m.group("comp_id"))
                current_comp_name = m.group("comp_name").strip()
                current_encoding = None
                seen_comps[current_comp_id] = current_comp_name
                continue

            m = ENC_RE.match(line)
            if m and current_comp_id is not None:
                current_encoding = m.group("enc").strip()
                continue

            m = LOOP_RE.match(line)
            if m and current_comp_id is not None and current_comp_name is not None:
                loop_id = int(m.group("loop_id"))
                loop_cycles = int(m.group("cycles"))
                rows.append(
                    LoopRow(
                        run_dir=run_dir_name,
                        nested_mode=nested_mode,
                        benchmark=benchmark,
                        log_path=str(log_path),
                        total_cycles=total_cycles,
                        comp_id=current_comp_id,
                        comp_name=current_comp_name,
                        encoding=current_encoding,
                        loop_id=loop_id,
                        loop_cycles=loop_cycles,
                    )
                )

    summary = LogSummary(
        run_dir=run_dir_name,
        nested_mode=nested_mode,
        benchmark=benchmark,
        log_path=str(log_path),
        total_cycles=total_cycles,
        comp_count=len(seen_comps),
        loop_count=len(rows),
    )
    return summary, rows


def parse_all_logs(root: Path) -> Tuple[List[LogSummary], List[LoopRow]]:
    all_summaries: List[LogSummary] = []
    all_loop_rows: List[LoopRow] = []

    for run_dir in iter_run_dirs(root):
        nested_mode = infer_nested_mode(run_dir.name)
        for log_file in iter_log_files(run_dir):
            benchmark = infer_benchmark_from_filename(log_file)
            summary, rows = parse_log_file(
                log_path=log_file,
                run_dir_name=run_dir.name,
                nested_mode=nested_mode,
                benchmark=benchmark,
            )
            all_summaries.append(summary)
            all_loop_rows.extend(rows)

    return all_summaries, all_loop_rows


# =========================
# Aggregation + comparison
# =========================

KeyLoop = Tuple[str, str, int, int]      # (benchmark, nested_mode, comp_id, loop_id)
KeyBenchMode = Tuple[str, str]          # (benchmark, nested_mode)
KeyBenchCompLoop = Tuple[str, int, int] # (benchmark, comp_id, loop_id)


def aggregate_loop_medians(loop_rows: List[LoopRow]) -> List[AggLoop]:
    cycles_by_key: Dict[KeyLoop, List[int]] = {}
    name_by_key: Dict[KeyLoop, str] = {}
    enc_by_key: Dict[KeyLoop, str] = {}

    for r in loop_rows:
        if r.nested_mode == "Unknown":
            continue
        key: KeyLoop = (r.benchmark, r.nested_mode, r.comp_id, r.loop_id)
        cycles_by_key.setdefault(key, []).append(int(r.loop_cycles))
        name_by_key[key] = r.comp_name
        enc_by_key[key] = "" if r.encoding is None else r.encoding

    out: List[AggLoop] = []
    for key, vals in cycles_by_key.items():
        bench, mode, comp_id, loop_id = key
        out.append(
            AggLoop(
                benchmark=bench,
                nested_mode=mode,
                comp_id=comp_id,
                loop_id=loop_id,
                comp_name=name_by_key.get(key, ""),
                encoding=enc_by_key.get(key, ""),
                n_runs=len(vals),
                median_loop_cycles=median_int(vals),
            )
        )

    out.sort(key=lambda x: (x.benchmark, x.nested_mode, x.comp_id, x.loop_id))
    return out


def benchmark_total_cycles_median(summaries: List[LogSummary]) -> Dict[KeyBenchMode, int]:
    totals: Dict[KeyBenchMode, List[int]] = {}
    for s in summaries:
        if s.nested_mode == "Unknown":
            continue
        if s.total_cycles is None:
            continue
        totals.setdefault((s.benchmark, s.nested_mode), []).append(int(s.total_cycles))

    med: Dict[KeyBenchMode, int] = {}
    for k, vals in totals.items():
        if vals:
            med[k] = median_int(vals)
    return med


def build_compare_rows(
    agg: List[AggLoop],
    total_meds: Dict[KeyBenchMode, int],
    min_share: float,
) -> List[dict]:
    nested_map: Dict[KeyBenchCompLoop, AggLoop] = {}
    nonested_map: Dict[KeyBenchCompLoop, AggLoop] = {}

    for a in agg:
        k: KeyBenchCompLoop = (a.benchmark, a.comp_id, a.loop_id)
        if a.nested_mode == "Nested":
            nested_map[k] = a
        elif a.nested_mode == "NoNested":
            nonested_map[k] = a

    out_rows: List[dict] = []

    for k, base in nested_map.items():
        if k not in nonested_map:
            continue
        new = nonested_map[k]

        bench, comp_id, loop_id = k
        bench_total_key = (bench, "Nested")
        if bench_total_key not in total_meds:
            continue
        total_cycles_nested = total_meds[bench_total_key]
        if total_cycles_nested <= 0:
            continue

        share = base.median_loop_cycles / total_cycles_nested
        if share < min_share:
            continue

        pct = safe_pct_change(base.median_loop_cycles, new.median_loop_cycles)
        abs_pct = abs(pct)

        out_rows.append(
            {
                "benchmark": bench,
                "comp_id": comp_id,
                "loop_id": loop_id,
                "comp_name": base.comp_name,
                "encoding_nested": base.encoding,
                "encoding_nonested": new.encoding,
                "runs_nested": base.n_runs,
                "runs_nonested": new.n_runs,
                "median_total_cycles_nested": total_cycles_nested,
                "median_loop_cycles_nested": base.median_loop_cycles,
                "median_loop_cycles_nonested": new.median_loop_cycles,
                "baseline_share_nested": share,
                "pct_change_nonested_vs_nested": pct,
                "abs_pct_change_nonested_vs_nested": abs_pct,
            }
        )

    out_rows.sort(key=lambda r: (r["benchmark"], -r["baseline_share_nested"], r["comp_id"], r["loop_id"]))
    return out_rows


# =========================
# Plotting
# =========================

def plot_per_benchmark_bars(compare_rows: List[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    by_bench: Dict[str, List[dict]] = {}
    for r in compare_rows:
        by_bench.setdefault(r["benchmark"], []).append(r)

    for bench, rows in sorted(by_bench.items()):
        labels: List[str] = []
        values: List[float] = []

        for r in rows:
            comp_id = r["comp_id"]
            loop_id = r["loop_id"]
            cname = short_comp_name(r["comp_name"])
            labels.append(f"C{comp_id} L{loop_id} | {cname}")
            values.append(float(r["pct_change_nonested_vs_nested"]))

        if not values:
            continue

        n = len(values)
        width = 14
        height = min(18, max(6, 0.35 * n))

        plt.figure(figsize=(width, height))
        plt.bar(range(n), values)
        plt.axhline(0.0)
        plt.title(f"{bench}: NoNested vs Nested (percentage change, baseline = Nested)")
        plt.ylabel("Percentage change in loop cycles (%)")
        plt.xticks(range(n), labels, rotation=90, ha="center")
        plt.tight_layout()

        out_path = out_dir / f"{bench}_nested_vs_nonested_pct_change.pdf"
        plt.savefig(out_path)
        plt.close()

        print(f"[OK] Wrote plot: {out_path}")


def plot_boxplot_weighted_abs_change(compare_rows: List[dict], out_dir: Path) -> Path:
    """
    Box plot per benchmark of |pct_change| values.
    Weighted by baseline share by expanding the data (replication) into integer counts.

    Styling changes requested:
      - Horizontal boxes (benchmarks on the left, rotated orientation)
      - No title
      - Coloured boxes (different colour per benchmark)
      - Y-axis (now x-axis, because horizontal) fixed to [0, 50]
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "BuboL_Loop_ObserverEffect_boxplot.pdf"

    by_bench: Dict[str, List[dict]] = {}
    for r in compare_rows:
        by_bench.setdefault(r["benchmark"], []).append(r)

    benchmarks = sorted(by_bench.keys())
    if not benchmarks:
        print("[WARN] No data for boxplot.")
        return out_path

    # Replication scale: bigger = closer to weights, but bigger arrays.
    SCALE = 2000

    data: List[List[float]] = []
    for bench in benchmarks:
        rows = by_bench[bench]
        expanded: List[float] = []
        for r in rows:
            share = float(r["baseline_share_nested"])
            val = float(r["abs_pct_change_nonested_vs_nested"])
            rep = int(round(share * SCALE))
            if rep < 1:
                rep = 1
            expanded.extend([val] * rep)
        data.append(expanded)

    # Square canvas, constrained layout
    fig, ax = plt.subplots(figsize=(6, 6), layout="constrained")

    # Horizontal box plot (benchmarks on the left)
    bp = ax.boxplot(
        data,
        vert=False,
        labels=benchmarks,
        showfliers=False,
        patch_artist=True,  # enables fill colours
    )

    # Give each box a distinct colour (Matplotlib default colour cycle)
    cycle = plt.rcParams["axes.prop_cycle"].by_key().get("color", [])
    if not cycle:
        cycle = ["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]

    for i, box in enumerate(bp["boxes"]):
        box.set_facecolor(cycle[i % len(cycle)])
        box.set_alpha(0.7)

    # Axes labels (no title)
    ax.set_xlabel("Median Percentage Change in Loop Cycles (%)")
    #ax.set_ylabel("Benchmark")

    # Clamp range to 0..50
    ax.set_xlim(0, 50)

    ax.grid(True, axis="x", linestyle="--", alpha=0.3)

    # Keep axes box square and centred
    ax.set_box_aspect(1)
    ax.set_anchor("C")

    fig.savefig(out_path, dpi=200, pad_inches=0.2, bbox_inches="tight")
    plt.close(fig)

    print(f"[OK] Wrote plot: {out_path}")
    return out_path



# =========================
# Summary statistics
# =========================

def print_summary_stats(compare_rows: List[dict]) -> None:
    by_bench: Dict[str, List[dict]] = {}
    for r in compare_rows:
        by_bench.setdefault(r["benchmark"], []).append(r)

    print()
    print("===== Summary (Nested baseline, loops in both, baseline share >= 1%) =====")
    if not by_bench:
        print("No benchmarks had any qualifying loops.")
        print()
        return

    for bench in sorted(by_bench.keys()):
        rows = by_bench[bench]
        abs_changes = [float(r["abs_pct_change_nonested_vs_nested"]) for r in rows]
        shares = [float(r["baseline_share_nested"]) for r in rows]

        wmed = weighted_median(abs_changes, shares)
        wq1 = weighted_percentile(abs_changes, shares, 0.25)
        wq3 = weighted_percentile(abs_changes, shares, 0.75)

        n_loops = len(rows)
        coverage = sum(shares)

        # also show unweighted median for sanity
        unweighted_median = statistics.median(abs_changes) if abs_changes else float("nan")

        print(f"{bench}:")
        print(f"  Loops measured:                 {n_loops}")
        print(f"  Coverage of total cycles:       {coverage * 100.0:.2f}%")
        print(f"  Weighted median |Δ%|:           {wmed:.2f}%")
        print(f"  Weighted IQR |Δ%| (Q1..Q3):     {wq1:.2f}% .. {wq3:.2f}%")
        print(f"  Unweighted median |Δ%|:         {unweighted_median:.2f}%")

    # global weighted median across all benchmarks (optional but useful)
    all_abs = [float(r["abs_pct_change_nonested_vs_nested"]) for r in compare_rows]
    all_w = [float(r["baseline_share_nested"]) for r in compare_rows]
    global_wmed = weighted_median(all_abs, all_w)
    print()
    print(f"Global weighted median |Δ%| across all benchmarks: {global_wmed:.2f}%")
    print()


# =========================
# Main
# =========================

def main() -> int:
    print(f"[INFO] Root: {ROOT}")
    print(f"[INFO] Output: {OUT_DIR}")

    summaries, loop_rows = parse_all_logs(ROOT)

    # Write raw CSVs
    logs_csv = OUT_DIR / "bubo_logs.csv"
    write_csv(
        logs_csv,
        fieldnames=[
            "run_dir",
            "nested_mode",
            "benchmark",
            "log_path",
            "total_cycles",
            "comp_count",
            "loop_count",
        ],
        dict_rows=[
            {
                "run_dir": s.run_dir,
                "nested_mode": s.nested_mode,
                "benchmark": s.benchmark,
                "log_path": s.log_path,
                "total_cycles": "" if s.total_cycles is None else s.total_cycles,
                "comp_count": s.comp_count,
                "loop_count": s.loop_count,
            }
            for s in summaries
        ],
    )

    loops_csv = OUT_DIR / "bubo_loops.csv"
    write_csv(
        loops_csv,
        fieldnames=[
            "run_dir",
            "nested_mode",
            "benchmark",
            "log_path",
            "total_cycles",
            "comp_id",
            "comp_name",
            "encoding",
            "loop_id",
            "loop_cycles",
        ],
        dict_rows=[
            {
                "run_dir": r.run_dir,
                "nested_mode": r.nested_mode,
                "benchmark": r.benchmark,
                "log_path": r.log_path,
                "total_cycles": "" if r.total_cycles is None else r.total_cycles,
                "comp_id": r.comp_id,
                "comp_name": r.comp_name,
                "encoding": "" if r.encoding is None else r.encoding,
                "loop_id": r.loop_id,
                "loop_cycles": r.loop_cycles,
            }
            for r in loop_rows
        ],
    )

    print(f"[OK] Wrote {logs_csv}")
    print(f"[OK] Wrote {loops_csv}")
    print(f"[OK] Parsed {len(summaries)} logs, {len(loop_rows)} loop rows")

    # Aggregate loop medians
    agg = aggregate_loop_medians(loop_rows)

    agg_csv = OUT_DIR / "agg_loop_medians.csv"
    write_csv(
        agg_csv,
        fieldnames=[
            "benchmark",
            "nested_mode",
            "comp_id",
            "loop_id",
            "comp_name",
            "encoding",
            "n_runs",
            "median_loop_cycles",
        ],
        dict_rows=[
            {
                "benchmark": a.benchmark,
                "nested_mode": a.nested_mode,
                "comp_id": a.comp_id,
                "loop_id": a.loop_id,
                "comp_name": a.comp_name,
                "encoding": a.encoding,
                "n_runs": a.n_runs,
                "median_loop_cycles": a.median_loop_cycles,
            }
            for a in agg
        ],
    )
    print(f"[OK] Wrote {agg_csv}")

    # Benchmark total cycle medians (Nested baseline)
    total_meds = benchmark_total_cycles_median(summaries)

    # Compare (Nested baseline), filter share >= 1%, only loops in both
    compare_rows = build_compare_rows(
        agg=agg,
        total_meds=total_meds,
        min_share=MIN_BASELINE_SHARE,
    )

    compare_csv = OUT_DIR / "compare_nested_vs_nonested.csv"
    write_csv(
        compare_csv,
        fieldnames=[
            "benchmark",
            "comp_id",
            "loop_id",
            "comp_name",
            "encoding_nested",
            "encoding_nonested",
            "runs_nested",
            "runs_nonested",
            "median_total_cycles_nested",
            "median_loop_cycles_nested",
            "median_loop_cycles_nonested",
            "baseline_share_nested",
            "pct_change_nonested_vs_nested",
            "abs_pct_change_nonested_vs_nested",
        ],
        dict_rows=compare_rows,
    )
    print(f"[OK] Wrote {compare_csv}")

    # Existing per-benchmark bar plots (ordered by baseline share)
    plot_per_benchmark_bars(compare_rows, PLOTS_DIR)

    # Final final box plot (weighted absolute changes)
    plot_boxplot_weighted_abs_change(compare_rows, PLOTS_DIR)

    # Print your requested summary stats
    print_summary_stats(compare_rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
