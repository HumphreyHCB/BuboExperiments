#!/usr/bin/env python3
"""
collect_and_plot_async_jfr_vs_vtune.py  (UPDATED)

New changes:
- global font size = 8
- plot ABSOLUTE change: abs(tool_pct - vtune_pct)
- median labels moved just to the right of each box (no overlap)
- median labels include a '%' sign

Other behaviour kept:
- coloured boxes
- fixed y-axis range [-200, +200] and indicators if data exceeds
- console summary per benchmark + overall median
- JFR matching by (benchmark, mode, loop_id) + closest slow_run_share_pct
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt


# =========================================================
# GLOBAL FONT SIZE (ALL TEXT = 8)
# =========================================================
plt.rcParams.update(
    {
        "font.size": 8,
        "axes.titlesize": 8,
        "axes.labelsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
    }
)

PLOTS_DIR = Path("/home/hb478/repos/BuboExperiments/LoopProfiling/TestAsyncJFRRunToPlot/plots")

OUT_CSV = PLOTS_DIR / "condensed_vtune_async_jfr_shareGT2.csv"
OUT_ASYNC_PDF = PLOTS_DIR / "Async_Accuracy_Boxplot.pdf"
OUT_JFR_PDF = PLOTS_DIR / "JFR_Accuracy_Boxplot.pdf"

EXPECTED_HEADER = [
    "benchmark",
    "suite",
    "mode",
    "comp_id",
    "loop_id",
    "method",
    "slow_run_share_pct",
    "vtune_pct",
    "async_pct",
    "jfr_pct",
]

MIN_SHARE_PCT = 2.0

# Fixed y-axis window (relative)
YLIM = 150.0


def safe_int(s: str) -> Optional[int]:
    s = (s or "").strip()
    if s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def safe_float(s: str) -> Optional[float]:
    s = (s or "").strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def is_repeated_header_row(row: Dict[str, str]) -> bool:
    return (row.get("benchmark") or "").strip().lower() == "benchmark"


def normalise_row(row: Dict[str, str]) -> Dict[str, str]:
    return {k: (row.get(k, "") or "") for k in EXPECTED_HEADER}


def should_keep(row: Dict[str, str]) -> bool:
    share = safe_float(row.get("slow_run_share_pct", ""))
    if share is None or share <= MIN_SHARE_PCT:
        return False
    return True


def read_one_csv_lenient(path: Path) -> Tuple[int, int, List[Dict[str, str]]]:
    rows_seen = 0
    rows_kept = 0
    kept: List[Dict[str, str]] = []

    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)

        header: Optional[List[str]] = None
        for raw in reader:
            if not raw or all((c or "").strip() == "" for c in raw):
                continue
            header = [c.strip() for c in raw]
            break

        if header is None:
            return (0, 0, [])

        dict_reader = csv.DictReader(f, fieldnames=header)

        for row in dict_reader:
            if row is None:
                continue
            if all(((v or "").strip() == "") for v in row.values()):
                continue
            if is_repeated_header_row(row):
                continue

            rows_seen += 1
            nr = normalise_row(row)
            if should_keep(nr):
                kept.append(nr)
                rows_kept += 1

    return (rows_seen, rows_kept, kept)


def collect_and_condense_csvs(plots_dir: Path, out_csv: Path) -> List[Dict[str, str]]:
    csv_files = sorted(p for p in plots_dir.rglob("*_vtune_async_jfr.csv") if p.is_file())

    total_seen = 0
    total_kept = 0

    seen_keys = set()
    deduped_rows: List[Dict[str, str]] = []

    for p in csv_files:
        if p.resolve() == out_csv.resolve():
            continue

        try:
            seen, kept, rows = read_one_csv_lenient(p)
        except Exception as e:
            print(f"[WARN] Failed to read {p}: {e}")
            continue

        total_seen += seen
        total_kept += kept

        for r in rows:
            key = tuple((r.get(k, "") or "") for k in EXPECTED_HEADER)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped_rows.append(r)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as out:
        w = csv.DictWriter(out, fieldnames=EXPECTED_HEADER, extrasaction="ignore")
        w.writeheader()
        for r in deduped_rows:
            w.writerow(r)

    print(f"[OK] Source CSV files scanned: {len(csv_files)}")
    print(f"[OK] Rows seen (non-empty, non-header): {total_seen}")
    print(f"[OK] Rows kept (share > {MIN_SHARE_PCT}% pre-dedupe): {total_kept}")
    print(f"[OK] Rows written (deduped): {len(deduped_rows)}")
    print(f"[OK] Wrote condensed CSV: {out_csv}")

    return deduped_rows


@dataclass(frozen=True)
class Key:
    benchmark: str
    mode: str
    loop_id: int


@dataclass
class RowParsed:
    benchmark: str
    suite: str
    mode: str
    comp_id: Optional[int]
    loop_id: Optional[int]
    method: str
    share: Optional[float]
    vtune: Optional[float]
    async_: Optional[float]
    jfr: Optional[float]
    raw: Dict[str, str]


def parse_rows(rows: List[Dict[str, str]]) -> List[RowParsed]:
    out: List[RowParsed] = []
    for r in rows:
        out.append(
            RowParsed(
                benchmark=(r.get("benchmark") or "").strip(),
                suite=(r.get("suite") or "").strip(),
                mode=(r.get("mode") or "").strip(),
                comp_id=safe_int(r.get("comp_id", "")),
                loop_id=safe_int(r.get("loop_id", "")),
                method=(r.get("method") or "").strip(),
                share=safe_float(r.get("slow_run_share_pct", "")),
                vtune=safe_float(r.get("vtune_pct", "")),
                async_=safe_float(r.get("async_pct", "")),
                jfr=safe_float(r.get("jfr_pct", "")),
                raw=r,
            )
        )
    return out


def compute_async_absdiffs_by_benchmark(parsed: List[RowParsed]) -> Dict[str, List[float]]:
    """
    Each loop record is a point:
      absdiff_async = abs(async_pct - vtune_pct)
    Uses only rows where both async and vtune are present.
    """
    out: Dict[str, List[float]] = {}
    for r in parsed:
        if not r.benchmark:
            continue
        if r.vtune is None or r.async_ is None:
            continue
        out.setdefault(r.benchmark, []).append(abs(r.async_ - r.vtune))
    return out


def compute_jfr_absdiffs_by_benchmark_with_share_matching(parsed: List[RowParsed]) -> Dict[str, List[float]]:
    """
    For each VTune row (vtune_pct present), match a JFR row by:
      (benchmark, mode, loop_id) + closest slow_run_share_pct
    Then:
      absdiff_jfr = abs(matched_jfr_pct - vtune_pct)
    """
    jfr_candidates: Dict[Key, List[RowParsed]] = {}
    for r in parsed:
        if r.jfr is None:
            continue
        if r.loop_id is None:
            continue
        if not r.benchmark or not r.mode:
            continue
        k = Key(r.benchmark, r.mode, r.loop_id)
        jfr_candidates.setdefault(k, []).append(r)

    out: Dict[str, List[float]] = {}

    for base in parsed:
        if base.vtune is None:
            continue
        if base.loop_id is None:
            continue
        if base.share is None:
            continue
        if not base.benchmark or not base.mode:
            continue

        k = Key(base.benchmark, base.mode, base.loop_id)
        cands = jfr_candidates.get(k)
        if not cands:
            continue

        best: Optional[RowParsed] = None
        best_delta: Optional[float] = None
        for cand in cands:
            if cand.share is None:
                continue
            d = abs(cand.share - base.share)
            if best_delta is None or d < best_delta:
                best_delta = d
                best = cand

        if best is None or best.jfr is None:
            continue

        out.setdefault(base.benchmark, []).append(abs(best.jfr - base.vtune))

    return out


def _print_summary(diffs_by_bench: Dict[str, List[float]], title: str) -> None:
    print()
    print(f"=== {title} ===")
    all_vals: List[float] = []
    for bench in sorted(diffs_by_bench.keys()):
        vals = diffs_by_bench[bench]
        if not vals:
            continue
        all_vals.extend(vals)
        mn = min(vals)
        md = statistics.median(vals)
        mx = max(vals)
        print(f"{bench}: n={len(vals)}  min={mn:.3f}%  median={md:.3f}%  max={mx:.3f}%")
    if all_vals:
        overall = statistics.median(all_vals)
        print(f"OVERALL: n={len(all_vals)}  median={overall:.3f}%")
    else:
        print("OVERALL: no data")


def plot_boxplot_per_benchmark(
    diffs_by_bench: Dict[str, List[float]],
    out_pdf: Path,
    ylabel: str,
    title: str,
    ylim: float = 200.0,
) -> None:
    benches = sorted([b for b, vals in diffs_by_bench.items() if vals])
    if not benches:
        print(f"[WARN] No data to plot for: {out_pdf.name}")
        return

    data = [diffs_by_bench[b] for b in benches]

    medians = [statistics.median(vals) for vals in data]
    has_low = [min(vals) < 0 for vals in data]   # usually false for absdiff
    has_high = [max(vals) > ylim for vals in data]

    fig = plt.figure(figsize=(8, 4))
    ax = fig.add_subplot(111)

    # ---------
    # Horizontal box plot
    # ---------
    bp = ax.boxplot(
        data,
        labels=benches,
        showfliers=True,
        patch_artist=True,
        vert=False,   # <<< rotation
    )

    cycle = plt.rcParams["axes.prop_cycle"].by_key().get("color", [])
    if not cycle:
        cycle = ["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]

    for i, box in enumerate(bp["boxes"]):
        box.set_facecolor(cycle[i % len(cycle)])
        box.set_alpha(0.6)

    # ---------
    # Axes / labels
    # ---------
    ax.set_xlim(0, ylim)
    ax.set_xlabel(ylabel)
    ax.set_title(title)
    ax.set_yticklabels(benches)

    # ---------
    # Median labels ABOVE each box
    # ---------
    y_offset = 0.25  # vertical offset above the box
    for y, md in enumerate(medians, start=1):
        ax.text(
            min(md, ylim),
            y + y_offset,
            f"{md:.1f}%",
            ha="center",
            va="bottom",
        )

    # ---------
    # Out-of-range indicators (right edge)
    # ---------
    for y, high in enumerate(has_high, start=1):
        if high:
            ax.plot(ylim, y, marker=">", markersize=6)
    for y, low in enumerate(has_low, start=1):
        if low:
            ax.plot(0, y, marker="<", markersize=6)

    fig.tight_layout()
    fig.savefig(out_pdf)
    plt.close(fig)

    print(f"[OK] Wrote plot: {out_pdf}")


def main() -> None:
    if not PLOTS_DIR.is_dir():
        raise SystemExit(f"[ERROR] Directory not found: {PLOTS_DIR}")

    rows = collect_and_condense_csvs(PLOTS_DIR, OUT_CSV)
    parsed = parse_rows(rows)

    async_absdiffs = compute_async_absdiffs_by_benchmark(parsed)
    _print_summary(async_absdiffs, "ABS(async − vtune), share > 2%")
    plot_boxplot_per_benchmark(
        async_absdiffs,
        OUT_ASYNC_PDF,
        ylabel="Difference from baseline (%)",
        title="Async",
        ylim=YLIM,
    )

    jfr_absdiffs = compute_jfr_absdiffs_by_benchmark_with_share_matching(parsed)
    _print_summary(jfr_absdiffs, "ABS(jfr − vtune), matched by loop_id + closest share, share > 2%")
    plot_boxplot_per_benchmark(
        jfr_absdiffs,
        OUT_JFR_PDF,
        ylabel="Difference from baseline (%)",
        title="JFR",
        ylim=YLIM,
    )


if __name__ == "__main__":
    main()