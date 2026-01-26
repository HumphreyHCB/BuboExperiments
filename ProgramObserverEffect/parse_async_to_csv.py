#!/usr/bin/env python3
"""
parse_async_to_csv.py

Scans:
  /home/hb478/repos/BuboExperiments/ProgramObserverEffect
for:
  bubo_runs*_BuboWithDebug/async/*.txt

For each Async-Profiler text file:
  - Extracts total samples from the "--- Execution profile ---" section
  - Extracts the final summary table rows:
        ns  percent  samples  top
  - Writes one CSV per input file, next to it:
        <input>.txt.csv

Additionally, builds per-benchmark aggregate CSVs:
  - Aggregates "samples" per (benchmark, method, mode) across all runs
  - Computes median samples for BuboOff and BuboOn
  - Writes:
        <root>/async_aggregate/<benchmark>_aggregate.csv

Plots:
  1) Per benchmark bar plot of pct_change_vs_off per method
     - Excludes java.lang.String.hashCode always
     - Starts with methods whose BuboOff median share is >= 2%
     - If coverage is below 85%, keeps adding methods by descending BuboOff share
       until coverage reaches 85% or there are no more eligible methods
     - Each x label includes the method's BuboOff share in brackets

  2) Final box plot across benchmarks
     - One box per benchmark
     - Box data points are "weighted absolute percent changes" per method in that benchmark:
           w_i = p_i * abs(pct_change_i)
       where p_i is the BuboOff median share: p_i = off_med / total_off_med

Important change:
  - The per benchmark summary statistic printed is the mean of w_i values, not a sum.
    This matches your request to use a mean rather than a sum.

Outputs:
  - Per file CSVs next to each input .txt
  - Per benchmark aggregate CSVs in:
        <root>/async_aggregate/
  - Per benchmark plots in:
        <root>/async_aggregate/plots/
  - Final box plot:
        <root>/async_aggregate/plots/ALL_benchmarks_weighted_abs_pct_change_boxplot.pdf
"""

from __future__ import annotations

import csv
import re
import sys
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt


RUN_DIR_RE = re.compile(r"^bubo_runs\d+_BuboWithDebug$")

FILE_RE = re.compile(r"^(?P<bench>.+?)_Bubo(?P<mode>On|Off)\.txt$")

TOTAL_SAMPLES_RE = re.compile(r"^\s*Total\s+samples\s*:\s*(?P<n>\d+)\s*$")
TABLE_HEADER_RE = re.compile(r"^\s*ns\s+percent\s+samples\s+top\s*$", re.IGNORECASE)
TABLE_ROW_RE = re.compile(
    r"^\s*(?P<ns>[\d,]+)\s+(?P<pct>\d+(?:\.\d+)?)%\s+(?P<samples>[\d,]+)\s+(?P<method>.+?)\s*$"
)

EXCLUDE_METHOD = "java.lang.String.hashCode"

MIN_CONTRIB_FRAC = 0.02   # 2% initial filter
TARGET_COVERAGE = 0.80    # 85% desired minimum coverage


@dataclass
class Row:
    ns: int
    percent: float
    samples: int
    method: str


def median_int(values: List[int]) -> Optional[int]:
    if not values:
        return None
    return int(statistics.median(values))


def parse_total_samples(text_lines: List[str]) -> Optional[int]:
    for line in text_lines:
        m = TOTAL_SAMPLES_RE.match(line)
        if m:
            return int(m.group("n"))
    return None


def parse_final_table_rows(text_lines: List[str]) -> List[Row]:
    header_idxs = [i for i, ln in enumerate(text_lines) if TABLE_HEADER_RE.match(ln.strip())]
    if not header_idxs:
        return []

    start = header_idxs[-1] + 1

    rows: List[Row] = []
    for ln in text_lines[start:]:
        m = TABLE_ROW_RE.match(ln)
        if not m:
            if rows:
                break
            continue

        ns = int(m.group("ns").replace(",", ""))
        pct = float(m.group("pct"))
        samples = int(m.group("samples").replace(",", ""))
        method = m.group("method").strip()

        rows.append(Row(ns=ns, percent=pct, samples=samples, method=method))

    return rows


def write_per_file_csv(
    out_path: Path,
    rows: List[Row],
    total_samples: Optional[int],
    benchmark: str,
    mode: str,
    run_dir: str,
    input_file: str,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "benchmark",
            "mode",
            "run_dir",
            "input_file",
            "total_samples",
            "method",
            "samples",
            "percent",
            "ns",
        ])
        for r in rows:
            w.writerow([
                benchmark,
                mode,
                run_dir,
                input_file,
                "" if total_samples is None else total_samples,
                r.method,
                r.samples,
                r.percent,
                r.ns,
            ])


def write_benchmark_aggregate_csv(
    out_dir: Path,
    benchmark: str,
    samples_by_method: Dict[str, Dict[str, List[int]]],
    totals_by_mode: Dict[str, List[int]],
) -> Tuple[Path, Optional[int], Optional[int]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{benchmark}_aggregate.csv"

    total_off_med = median_int(totals_by_mode.get("BuboOff", []))
    total_on_med = median_int(totals_by_mode.get("BuboOn", []))

    rows_out: List[Tuple] = []
    for method, by_mode in samples_by_method.items():
        off_vals = by_mode.get("BuboOff", [])
        on_vals = by_mode.get("BuboOn", [])

        off_med = median_int(off_vals)
        on_med = median_int(on_vals)

        diff = None
        pct_change = None
        if off_med is not None and on_med is not None:
            diff = on_med - off_med
            if off_med != 0:
                pct_change = (diff / off_med) * 100.0

        rows_out.append((
            method,
            len(off_vals),
            "" if off_med is None else off_med,
            len(on_vals),
            "" if on_med is None else on_med,
            "" if diff is None else diff,
            "" if pct_change is None else pct_change,
        ))

    def sort_key(t: Tuple) -> Tuple:
        diff_val = t[5]
        if diff_val == "":
            return (1, 0, t[0])
        return (0, -abs(int(diff_val)), t[0])

    rows_out.sort(key=sort_key)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "benchmark",
            "median_total_samples_bubo_off",
            "median_total_samples_bubo_on",
        ])
        w.writerow([
            benchmark,
            "" if total_off_med is None else total_off_med,
            "" if total_on_med is None else total_on_med,
        ])
        w.writerow([])

        w.writerow([
            "method",
            "n_runs_off",
            "median_samples_bubo_off",
            "n_runs_on",
            "median_samples_bubo_on",
            "diff_on_minus_off",
            "pct_change_vs_off",
        ])
        for r in rows_out:
            w.writerow(list(r))

    return out_path, total_off_med, total_on_med


def process_one_file(
    txt_path: Path,
    aggregate_samples: Dict[str, Dict[str, Dict[str, List[int]]]],
    aggregate_totals: Dict[str, Dict[str, List[int]]],
) -> Tuple[bool, str]:
    m = FILE_RE.match(txt_path.name)
    if not m:
        return False, f"Skipped (name did not match *_BuboOn/Off.txt): {txt_path}"

    benchmark = m.group("bench")
    mode = f"Bubo{m.group('mode')}"

    run_dir = ""
    try:
        run_dir = txt_path.parents[1].name
    except Exception:
        run_dir = ""

    lines = txt_path.read_text(encoding="utf-8", errors="replace").splitlines()

    total_samples = parse_total_samples(lines)
    rows = parse_final_table_rows(lines)

    out_csv = txt_path.with_suffix(txt_path.suffix + ".csv")
    write_per_file_csv(
        out_path=out_csv,
        rows=rows,
        total_samples=total_samples,
        benchmark=benchmark,
        mode=mode,
        run_dir=run_dir,
        input_file=str(txt_path),
    )

    bench_map = aggregate_samples.setdefault(benchmark, {})
    totals_map = aggregate_totals.setdefault(benchmark, {})
    if total_samples is not None:
        totals_map.setdefault(mode, []).append(total_samples)

    for r in rows:
        meth_map = bench_map.setdefault(r.method, {})
        meth_map.setdefault(mode, []).append(r.samples)

    msg_bits = [f"Wrote {out_csv}"]
    if total_samples is None:
        msg_bits.append("warning: total_samples not found")
    if not rows:
        msg_bits.append("warning: table rows not found")
    return True, " | ".join(msg_bits)


def _select_methods_to_reach_coverage(
    total_off_med: int,
    method_items: List[Tuple[str, int, int]],
) -> List[Tuple[str, int, int]]:
    """
    method_items: list of (method, off_med, on_med), already filtered for validity
    Returns a selected list that starts with methods >= 2% share, then adds more by share
    until coverage reaches 85% or we run out.
    """
    items_sorted = sorted(method_items, key=lambda t: t[1], reverse=True)

    selected: List[Tuple[str, int, int]] = []
    selected_set = set()

    coverage = 0.0

    # First pass: include methods meeting the 2% threshold
    for method, off_med, on_med in items_sorted:
        share = off_med / total_off_med
        if share >= MIN_CONTRIB_FRAC:
            selected.append((method, off_med, on_med))
            selected_set.add(method)
            coverage += share

    # If still below target, add more methods by descending share
    if coverage < TARGET_COVERAGE:
        for method, off_med, on_med in items_sorted:
            if method in selected_set:
                continue
            selected.append((method, off_med, on_med))
            selected_set.add(method)
            coverage += off_med / total_off_med
            if coverage >= TARGET_COVERAGE:
                break

    return selected


def plot_benchmark_pct_change(
    plots_dir: Path,
    benchmark: str,
    total_off_med: Optional[int],
    samples_by_method: Dict[str, Dict[str, List[int]]],
) -> Tuple[Optional[Path], Optional[float], Optional[float], int, List[float]]:
    """
    Returns:
      (plot_path, mean_weighted_abs_pct_change, coverage_frac, n_plotted, w_points)

    w_points are per method:
      w_i = p_i * abs(pct_change_i)
    where p_i = off_med / total_off_med

    mean_weighted_abs_pct_change is:
      mean(w_points)
    """
    if total_off_med is None or total_off_med <= 0:
        return (None, None, None, 0, [])

    eligible: List[Tuple[str, int, int]] = []
    for method, by_mode in samples_by_method.items():
        if method == EXCLUDE_METHOD:
            continue

        off_med = median_int(by_mode.get("BuboOff", []))
        on_med = median_int(by_mode.get("BuboOn", []))

        if off_med is None or on_med is None:
            continue
        if off_med <= 0:
            continue

        eligible.append((method, off_med, on_med))

    if not eligible:
        return (None, None, None, 0, [])

    selected = _select_methods_to_reach_coverage(total_off_med, eligible)
    if not selected:
        return (None, None, None, 0, [])

    # Compute per method values
    methods: List[str] = []
    pct_changes: List[float] = []
    contrib_pcts: List[float] = []
    off_meds: List[int] = []
    w_points: List[float] = []

    for method, off_med, on_med in selected:
        p_i = off_med / total_off_med
        pct_change = ((on_med - off_med) / off_med) * 100.0
        w_i = p_i * abs(pct_change)

        methods.append(method)
        pct_changes.append(pct_change)
        contrib_pcts.append(p_i * 100.0)
        off_meds.append(off_med)
        w_points.append(w_i)

    coverage_frac = sum(off_meds) / total_off_med

    # You asked for mean, not sum
    mean_weighted_abs_pct = float(statistics.mean(w_points)) if w_points else None

    # Sort bars by absolute percent change for visibility
    order = sorted(range(len(methods)), key=lambda i: abs(pct_changes[i]), reverse=True)
    methods_sorted = [methods[i] for i in order]
    pct_sorted = [pct_changes[i] for i in order]
    contrib_sorted = [contrib_pcts[i] for i in order]

    labels = [f"{m} ({c:.2f}%)" for m, c in zip(methods_sorted, contrib_sorted)]

    plots_dir.mkdir(parents=True, exist_ok=True)
    out_path = plots_dir / f"{benchmark}_pct_change_vs_off.pdf"

    plt.figure(figsize=(max(10, 0.35 * len(labels)), 4.9))
    plt.bar(range(len(labels)), pct_sorted)
    plt.axhline(0.0, linewidth=1.0)

    plt.title(f"{benchmark}: pct_change_vs_off per method")
    plt.ylabel("pct_change_vs_off (%)")
    plt.xlabel("method")

    plt.xticks(range(len(labels)), labels, rotation=75, ha="right")

    coverage_pct = coverage_frac * 100.0
    plt.gcf().text(
        0.01,
        0.01,
        f"Coverage {coverage_pct:.2f}% of median total samples (BuboOff). "
        f"Excluded {EXCLUDE_METHOD}. Start threshold {MIN_CONTRIB_FRAC*100:.0f}%, target {TARGET_COVERAGE*100:.0f}%. "
        f"Mean of weighted abs changes {mean_weighted_abs_pct:.3f}%.",
        fontsize=8,
        va="bottom",
    )

    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()

    return (out_path, mean_weighted_abs_pct, coverage_frac, len(labels), w_points)


def plot_final_boxplot(
    out_path: Path,
    bench_to_wpoints: Dict[str, List[float]],
) -> Optional[Path]:
    """
    Final box plot across benchmarks.

    Keeps all previous behaviour, plus:
      - Square PDF canvas (good for LaTeX centring)
      - Constrained layout for label padding
      - Square axes box, centred in the figure
      - Avoids bbox_inches="tight" so the saved PDF canvas stays square
    """
    benches_raw = sorted([b for b in bench_to_wpoints.keys() if bench_to_wpoints[b]])
    if not benches_raw:
        return None

    label_map = {
        "LoopBenchmarks": "LoopsBench",
    }
    benches = [label_map.get(b, b) for b in benches_raw]
    data = [bench_to_wpoints[b] for b in benches_raw]

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Square canvas, and let Matplotlib manage padding for labels
    fig, ax = plt.subplots(figsize=(6, 6), layout="constrained")

    bp = ax.boxplot(
        data,
        labels=benches,
        vert=False,               # horizontal, names on the left
        patch_artist=True,        # allow filled boxes
        showmeans=False,          # remove mean marker (no green triangle)
        medianprops={
            "linestyle": "-",
            "linewidth": 1.0,
            "color": "orange",
        },
        whiskerprops={
            "linestyle": "-",
            "linewidth": 1.0,
        },
        capprops={
            "linestyle": "-",
            "linewidth": 1.0,
        },
    )

    # Give each box a different light colour
    cmap = plt.get_cmap("tab10")
    for i, box in enumerate(bp["boxes"]):
        box.set_facecolor(cmap(i % 10))
        box.set_alpha(0.65)
        box.set_linewidth(1.0)

    ax.set_xlabel("Weighted absolute percentage change (%)")

    # Make the *axes box* square, and keep it centred in the figure
    ax.set_box_aspect(1)
    ax.set_anchor("C")

    # Manually control padding:
    # more space on the left for benchmark names,
    # symmetric top/bottom, tight right edge
    fig.subplots_adjust(
        left=0.35,   # space for labels
        right=0.95,
        top=0.95,
        bottom=0.15,
    )
    

    # IMPORTANT: keep square PDF canvas
    fig.savefig(out_path, pad_inches=0.25)
    plt.close(fig)

    return out_path




def main() -> int:
    root = Path("/home/hb478/repos/BuboExperiments/ProgramObserverEffect").expanduser().resolve()
    if not root.is_dir():
        print(f"Error: not a directory: {root}", file=sys.stderr)
        return 2

    run_dirs = [p for p in root.iterdir() if p.is_dir() and RUN_DIR_RE.match(p.name)]
    run_dirs.sort()

    if not run_dirs:
        print(f"Warning: no run directories found under {root}", file=sys.stderr)
        return 1

    aggregate_samples: Dict[str, Dict[str, Dict[str, List[int]]]] = {}
    aggregate_totals: Dict[str, Dict[str, List[int]]] = {}

    total_files = 0
    written = 0
    skipped = 0

    for rd in run_dirs:
        async_dir = rd / "async"
        if not async_dir.is_dir():
            continue

        for tf in sorted(async_dir.glob("*.txt")):
            total_files += 1
            ok, msg = process_one_file(tf, aggregate_samples, aggregate_totals)
            print(msg)
            if ok:
                written += 1
            else:
                skipped += 1

    out_agg_dir = root / "async_aggregate"
    plots_dir = out_agg_dir / "plots"

    agg_written = 0
    plot_written = 0

    bench_summaries: List[Tuple[str, float, float, int]] = []
    all_means: List[float] = []

    bench_to_wpoints: Dict[str, List[float]] = {}

    for bench in sorted(aggregate_samples.keys()):
        out_path, total_off_med, _total_on_med = write_benchmark_aggregate_csv(
            out_dir=out_agg_dir,
            benchmark=bench,
            samples_by_method=aggregate_samples[bench],
            totals_by_mode=aggregate_totals.get(bench, {}),
        )
        print(f"Wrote aggregate {out_path}")
        agg_written += 1

        plot_path, mean_wabs, coverage_frac, n_plotted, w_points = plot_benchmark_pct_change(
            plots_dir=plots_dir,
            benchmark=bench,
            total_off_med=total_off_med,
            samples_by_method=aggregate_samples[bench],
        )

        bench_to_wpoints[bench] = w_points

        if plot_path is not None:
            print(f"Wrote plot {plot_path}")
            plot_written += 1

            if mean_wabs is not None and coverage_frac is not None:
                bench_summaries.append((bench, mean_wabs, coverage_frac, n_plotted))
                all_means.append(mean_wabs)

    # Final box plot across benchmarks
    final_box_path = plots_dir / "Whole_Program_Observer_Effect.pdf"
    made_box = plot_final_boxplot(final_box_path, bench_to_wpoints)
    if made_box is not None:
        print(f"Wrote final box plot {made_box}")

    print()
    if bench_summaries:
        print("Summary (per benchmark): mean of per method weighted absolute percent changes")
        print("Each method point is p_i * abs(pct_change_i), where p_i is BuboOff median share.")
        for bench, mean_wabs, cov, n in sorted(bench_summaries, key=lambda x: x[0]):
            print(f"  {bench}: mean_weighted_abs_pct_change={mean_wabs:.3f}% , coverage={cov*100:.2f}% , n_methods={n}")

        overall = float(statistics.median(all_means)) if all_means else float("nan")
        print()
        print(f"Overall median of per benchmark mean_weighted_abs_pct_change = {overall:.3f}%")
    else:
        print("Summary: no plots produced, no methods passed filters.")

    print()
    print(
        f"Done. Found {total_files} .txt files, wrote {written} per file CSVs, "
        f"skipped {skipped}, wrote {agg_written} aggregate CSVs, wrote {plot_written} plots."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
