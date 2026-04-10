#!/usr/bin/env python3
"""
collect_and_plot_overall_accuracy.py

Build ONE plot with THREE box plots:
  - BuboL
  - Async
  - JFR

Uses final condensed CSVs:

  condensed_loops_runtimeShareGT2_callCount0_withMedian_Bubo.csv
  condensed_loops_runtimeShareGT2_callCount0_withMedian_async_jfr.csv

Metrics:
  BuboL: abs(slowdown_pct - loop_median_pct)
  Async: abs(async_pct - vtune_pct)
  JFR:   matched by (benchmark, mode, loop_id) + closest share
         then abs(jfr_pct - vtune_pct)
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

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

# =========================================================
# HARD-CODED PATHS (like your previous scripts)
# =========================================================
BASE_DIR = Path("/home/hb478/repos/BuboExperiments/LoopProfiling/Thesis")

BUBO_CSV = BASE_DIR / "condensed_loops_runtimeShareGT2_callCount0_withMedian_Bubo.csv"
ASYNC_JFR_CSV = BASE_DIR / "condensed_loops_runtimeShareGT2_callCount0_withMedian_async_jfr.csv"

OUT_PDF = BASE_DIR / "BuboL_JFR_Async_Accuracy_summary.pdf"

# Axis limit
XLIM = 70.0

# Optional visual cap to avoid insane outliers flattening plot (does NOT affect stats)
PLOT_CAP = 300.0


# =========================================================
# Helpers
# =========================================================
def safe_int(s: str) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def safe_float(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_csv_lenient(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []

    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)

        header = None
        for raw in reader:
            if raw and any((c or "").strip() for c in raw):
                header = [c.strip() for c in raw]
                break

        if header is None:
            return rows

        dict_reader = csv.DictReader(f, fieldnames=header)

        first_col = header[0].strip().lower()

        for r in dict_reader:
            if r is None:
                continue
            if all(((v or "").strip() == "") for v in r.values()):
                continue
            if (r.get(header[0]) or "").strip().lower() == first_col:
                continue

            rows.append({k: (r.get(k, "") or "") for k in header})

    return rows


# =========================================================
# Bubo
# =========================================================
def compute_bubo_absdiffs(rows: List[Dict[str, str]]) -> List[float]:
    out: List[float] = []
    for r in rows:
        s = safe_float(r.get("slowdown_pct", ""))
        m = safe_float(r.get("loop_median_pct", ""))
        if s is None or m is None:
            continue
        out.append(abs(s - m))
    return out


# =========================================================
# Async / JFR
# =========================================================
@dataclass(frozen=True)
class Key:
    benchmark: str
    mode: str
    loop_id: int


@dataclass
class AJRow:
    benchmark: str
    mode: str
    loop_id: Optional[int]
    share: Optional[float]
    vtune: Optional[float]
    async_: Optional[float]
    jfr: Optional[float]


def parse_async_jfr_rows(rows: List[Dict[str, str]]) -> List[AJRow]:
    parsed: List[AJRow] = []
    for r in rows:
        parsed.append(
            AJRow(
                benchmark=(r.get("benchmark") or "").strip(),
                mode=(r.get("mode") or "").strip(),
                loop_id=safe_int(r.get("loop_id", "")),
                share=safe_float(r.get("slow_run_share_pct", "")),
                vtune=safe_float(r.get("vtune_pct", "")),
                async_=safe_float(r.get("async_pct", "")),
                jfr=safe_float(r.get("jfr_pct", "")),
            )
        )
    return parsed


def compute_async_absdiffs(parsed: List[AJRow]) -> List[float]:
    out: List[float] = []
    for r in parsed:
        if r.vtune is None or r.async_ is None:
            continue
        out.append(abs(r.async_ - r.vtune))
    return out


def compute_jfr_absdiffs(parsed: List[AJRow]) -> List[float]:
    # Collect JFR candidates
    jfr_candidates: Dict[Key, List[AJRow]] = {}

    for r in parsed:
        if r.jfr is None:
            continue
        if r.loop_id is None:
            continue
        if not r.benchmark or not r.mode:
            continue

        k = Key(r.benchmark, r.mode, r.loop_id)
        jfr_candidates.setdefault(k, []).append(r)

    out: List[float] = []

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

        best = None
        best_delta = None

        for cand in cands:
            if cand.share is None:
                continue
            d = abs(cand.share - base.share)
            if best_delta is None or d < best_delta:
                best_delta = d
                best = cand

        if best is None or best.jfr is None:
            continue

        out.append(abs(best.jfr - base.vtune))

    return out


# =========================================================
# Plot
# =========================================================
def plot_three(bubo_vals, async_vals, jfr_vals):
    labels = ["BuboL", "Async", "JFR"]
    raw_data = [bubo_vals, async_vals, jfr_vals]

    # Cap extreme values for plotting only
    data = [
        [min(v, PLOT_CAP) for v in vals] if vals else [float("nan")]
        for vals in raw_data
    ]

    fig, ax = plt.subplots(figsize=(6.2, 2.6))

    bp = ax.boxplot(
        data,
        labels=labels,
        patch_artist=True,
        showfliers=True,
        vert=False,
    )

    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]

    for i, box in enumerate(bp["boxes"]):
        box.set_facecolor(colors[i % len(colors)])
        box.set_alpha(0.6)

    ax.set_xlim(0, XLIM)
    ax.set_xlabel("Difference from baseline (%)")
    ax.set_yticklabels(labels)

    # Median labels
    y_offset = 0.25
    for y, vals in enumerate(raw_data, start=1):
        if not vals:
            continue
        md = statistics.median(vals)
        ax.text(
            min(md+1, XLIM+1),
            y + y_offset,
            f"{md:.1f}%",
            ha="center",
            va="bottom",
        )

    fig.tight_layout()
    fig.savefig(OUT_PDF)
    plt.close(fig)

    print(f"[OK] Wrote plot: {OUT_PDF}")


# =========================================================
# Main
# =========================================================
def main():

    if not BUBO_CSV.is_file():
        raise SystemExit(f"[ERROR] Missing Bubo CSV: {BUBO_CSV}")
    if not ASYNC_JFR_CSV.is_file():
        raise SystemExit(f"[ERROR] Missing Async/JFR CSV: {ASYNC_JFR_CSV}")

    bubo_rows = read_csv_lenient(BUBO_CSV)
    aj_rows = read_csv_lenient(ASYNC_JFR_CSV)

    bubo_vals = compute_bubo_absdiffs(bubo_rows)

    parsed = parse_async_jfr_rows(aj_rows)
    async_vals = compute_async_absdiffs(parsed)
    jfr_vals = compute_jfr_absdiffs(parsed)

    print("\n=== Overall ABS(tool − VTune) accuracy ===")
    for name, vals in [
        ("BuboL", bubo_vals),
        ("Async", async_vals),
        ("JFR", jfr_vals),
    ]:
        if not vals:
            print(f"{name}: no data")
            continue
        print(
            f"{name}: n={len(vals)} "
            f"min={min(vals):.3f}% "
            f"median={statistics.median(vals):.3f}% "
            f"max={max(vals):.3f}%"
        )

    plot_three(bubo_vals, async_vals, jfr_vals)


if __name__ == "__main__":
    main()