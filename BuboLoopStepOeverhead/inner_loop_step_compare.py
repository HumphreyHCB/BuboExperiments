#!/usr/bin/env python3
"""
inner_loop_step_compare.py (BuboLoopStepOeverhead/Data)

Goal:
  For each bench method InnerLoopStepBenchmark.benchN():

    - In NoNested: pick the loop with the largest cycles in the chosen comp.
      This defines the "reference loop id" for that bench method.

    - In Nested: compare the SAME loop id (must exist), using the chosen comp.

  Then:
    - collect per-run cycles for that same loop id
    - take medians across 3 runs per mode
    - plot % change relative to Nested baseline:
          (NoNested − Nested) / Nested * 100

Data layout:
  /home/hb478/repos/BuboExperiments/BuboLoopStepOeverhead/Data/
    run1.txt            (Nested)
    run2.txt
    run3.txt
    run1_NoNested.txt   (NoNested)
    run2_NoNested.txt
    run3_NoNested.txt

Outputs (under <ROOT>/parsed_inner_step/):
  - per_run_selected_loop_cycles.csv
  - per_method_medians.csv
  - plots/LoopStep_selected_loop_pct_change_per_bench_method.pdf
"""

from __future__ import annotations

import csv
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt


# =========================
# Configuration (EDIT ME)
# =========================

ROOT = Path("/home/hb478/repos/BuboExperiments/BuboLoopStepOeverhead").resolve()
DATA_DIR = (ROOT / "Data").resolve()

NESTED_FILES = [
    DATA_DIR / "run1.txt",
    DATA_DIR / "run2.txt",
    DATA_DIR / "run3.txt",
    DATA_DIR / "run4.txt",
    DATA_DIR / "run5.txt",
    DATA_DIR / "run6.txt",
    DATA_DIR / "run7.txt",
    DATA_DIR / "run8.txt",
    DATA_DIR / "run9.txt",
    DATA_DIR / "run10.txt",
]
NONESTED_FILES = [
    DATA_DIR / "run1_NoNested.txt",
    DATA_DIR / "run2_NoNested.txt",
    DATA_DIR / "run3_NoNested.txt",
    DATA_DIR / "run4_NoNested.txt",
    DATA_DIR / "run5_NoNested.txt",
    DATA_DIR / "run6_NoNested.txt",
    DATA_DIR / "run7_NoNested.txt",
    DATA_DIR / "run8_NoNested.txt",
    DATA_DIR / "run9_NoNested.txt",
    DATA_DIR / "run10_NoNested.txt",
]

BENCHMARK_NAME = "LoopStep"
BENCH_CLASS = "InnerLoopStepBenchmark"

OUT_DIR = (ROOT / "parsed_inner_step").resolve()
PLOTS_DIR = OUT_DIR / "plots"


# =========================
# Regex
# =========================

TOTAL_RE = re.compile(r"^Bubo\.RDTSC\.Harness\.main Total RDTSC cycles:\s*(\d+)\s*$")
COMP_RE = re.compile(r"^Comp\s+(?P<comp_id>\d+)\s+\((?P<comp_name>.+?)\)\s+loops:\s*$")
ENC_RE = re.compile(r"^Found Encoding\s*:\s*(?P<enc>.*)\s*$")
LOOP_LINE_RE = re.compile(
    r"^\s*loop\s+(?P<loop_id>\d+)\s+Cycles:\s*(?P<cycles>\d+)\b(?P<rest>.*)$"
)
METHOD_RE = re.compile(rf"\b{re.escape(BENCH_CLASS)}\.bench(?P<n>\d+)\s*\(")


# =========================
# Data models
# =========================

@dataclass
class LoopInfo:
    loop_id: int
    cycles: int
    source: str


@dataclass
class CompBlock:
    comp_id: int
    comp_name: str
    encoding: str
    loops: List[LoopInfo]


@dataclass
class SelectedRunRow:
    benchmark: str
    mode: str               # "NoNested" or "Nested"
    run_name: str           # run1/run2/run3
    file_path: str
    total_cycles: Optional[int]
    bench_method: str       # bench1..benchN

    # chosen comp
    comp_id: int
    comp_name: str
    encoding: str

    # selected loop (same id across modes, defined by NoNested)
    selected_loop_id: int
    selected_loop_cycles: int


# =========================
# Helpers
# =========================

def median_int(xs: List[int]) -> int:
    return int(statistics.median(xs))


def write_csv(path: Path, fieldnames: List[str], rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def safe_pct_change(baseline: int, new: int) -> float:
    if baseline <= 0:
        return float("nan")
    return (new - baseline) / baseline * 100.0


def extract_source_from_rest(rest: str) -> str:
    idx = rest.find("Source:")
    if idx == -1:
        return ""
    return rest[idx + len("Source:"):].strip()


def method_from_comp_name(comp_name: str) -> Optional[str]:
    m = METHOD_RE.search(comp_name)
    if not m:
        return None
    return f"bench{int(m.group('n'))}"


def run_name_from_file(path: Path) -> str:
    name = path.name
    if name.endswith(".txt"):
        name = name[:-4]
    name = name.replace("_NoNested", "")
    return name


def parse_file_to_comp_blocks(path: Path) -> Tuple[Optional[int], List[CompBlock]]:
    total_cycles: Optional[int] = None
    blocks: List[CompBlock] = []
    current: Optional[CompBlock] = None

    with path.open("r", encoding="utf-8", errors="replace") as f:
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
                if current is not None:
                    blocks.append(current)
                current = CompBlock(
                    comp_id=int(m.group("comp_id")),
                    comp_name=m.group("comp_name").strip(),
                    encoding="",
                    loops=[],
                )
                continue

            m = ENC_RE.match(line)
            if m and current is not None:
                current.encoding = m.group("enc").strip()
                continue

            m = LOOP_LINE_RE.match(line)
            if m and current is not None:
                loop_id = int(m.group("loop_id"))
                cycles = int(m.group("cycles"))
                rest = m.group("rest") or ""
                source = extract_source_from_rest(rest)
                current.loops.append(LoopInfo(loop_id=loop_id, cycles=cycles, source=source))
                continue

    if current is not None:
        blocks.append(current)

    return total_cycles, blocks


def all_bench_methods_present(blocks: List[CompBlock]) -> List[str]:
    methods = set()
    for b in blocks:
        m = method_from_comp_name(b.comp_name)
        if m is not None:
            methods.add(m)
    return sorted(methods, key=lambda s: int(s.replace("bench", "")))


def choose_best_comp_for_method(blocks: List[CompBlock], bench_method: str) -> Optional[CompBlock]:
    """
    Choose comp variant for this method inside this file.

    We pick the comp whose MAX loop cycles is largest.
    This is usually the hot recomp that contains the dominant loop.
    """
    candidates: List[Tuple[int, CompBlock]] = []
    for b in blocks:
        m = method_from_comp_name(b.comp_name)
        if m == bench_method and b.loops:
            max_loop = max(li.cycles for li in b.loops)
            candidates.append((max_loop, b))

    if not candidates:
        return None

    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def pick_largest_loop_id(block: CompBlock) -> Optional[int]:
    if not block.loops:
        return None
    return max(block.loops, key=lambda li: li.cycles).loop_id


def loop_cycles_by_id(block: CompBlock) -> Dict[int, int]:
    return {li.loop_id: li.cycles for li in block.loops}


# =========================
# Core logic
# =========================

def determine_reference_loop_ids_from_nonested() -> Dict[str, int]:
    """
    For each bench method:
      - look across all NoNested runs
      - choose the loop id that corresponds to the largest observed loop cycles
        (in that run's chosen comp)
    Returns: bench_method -> selected_loop_id
    """
    best: Dict[str, Tuple[int, int]] = {}  # bench_method -> (best_cycles_seen, loop_id)

    for fp in NONESTED_FILES:
        if not fp.is_file():
            raise FileNotFoundError(f"Missing expected file: {fp}")

        _, blocks = parse_file_to_comp_blocks(fp)
        methods = all_bench_methods_present(blocks)

        for bm in methods:
            comp = choose_best_comp_for_method(blocks, bm)
            if comp is None or not comp.loops:
                continue

            # In NoNested, define reference as the largest loop in this comp
            li = max(comp.loops, key=lambda x: x.cycles)
            prev = best.get(bm)
            if prev is None or li.cycles > prev[0]:
                best[bm] = (li.cycles, li.loop_id)

    return {bm: loop_id for bm, (_, loop_id) in best.items()}


def extract_selected_loop_cycles(mode: str, files: List[Path], ref_loop_ids: Dict[str, int]) -> List[SelectedRunRow]:
    """
    For each file, for each bench method with a reference loop id:
      - choose best comp
      - extract cycles for that reference loop id
      - if that loop id not present in this comp, skip (not comparable)
    """
    rows: List[SelectedRunRow] = []

    for fp in files:
        if not fp.is_file():
            raise FileNotFoundError(f"Missing expected file: {fp}")

        total_cycles, blocks = parse_file_to_comp_blocks(fp)

        for bm, ref_loop_id in ref_loop_ids.items():
            comp = choose_best_comp_for_method(blocks, bm)
            if comp is None or not comp.loops:
                continue

            cycles_map = loop_cycles_by_id(comp)
            if ref_loop_id not in cycles_map:
                # This run doesn't have the reference loop id => not comparable
                continue

            rows.append(
                SelectedRunRow(
                    benchmark=BENCHMARK_NAME,
                    mode=mode,
                    run_name=run_name_from_file(fp),
                    file_path=str(fp),
                    total_cycles=total_cycles,
                    bench_method=bm,
                    comp_id=comp.comp_id,
                    comp_name=comp.comp_name,
                    encoding=comp.encoding,
                    selected_loop_id=ref_loop_id,
                    selected_loop_cycles=cycles_map[ref_loop_id],
                )
            )

    return rows


def compute_medians_per_mode(rows: List[SelectedRunRow]) -> Dict[Tuple[str, str], int]:
    """
    median cycles for each (mode, bench_method)
    """
    groups: Dict[Tuple[str, str], List[int]] = {}
    for r in rows:
        groups.setdefault((r.mode, r.bench_method), []).append(r.selected_loop_cycles)

    med: Dict[Tuple[str, str], int] = {}
    for k, vals in groups.items():
        if vals:
            med[k] = median_int(vals)
    return med


def plot_pct_bar(per_method_rows: List[dict], out_path: Path) -> None:
    """
    Bar chart of ABSOLUTE percentage change (|%Δ|) for each bench method.

    X-axis labels use the InnerLoopStepBenchmark inner-loop sizes:
      bench1..bench15 -> 100, 150, 220, 330, 500, 750, 1100, 1600, 2400,
                         3600, 5400, 8100, 12000, 20000, 50000
    """
    # Inner loop sizes in order (bench1 maps to index 0, etc.)
    inner_sizes = [100, 150, 220, 330, 500, 750, 1100, 1600, 2400, 3600, 5400, 8100, 12000, 20000, 50000]

    labels: List[str] = []
    abs_pcts: List[float] = []

    for r in per_method_rows:
        bm = str(r.get("bench_method", ""))
        pct = float(r.get("pct_change_nonested_vs_nested", float("nan")))

        # benchN -> N
        n = None
        if bm.startswith("bench"):
            try:
                n = int(bm.replace("bench", ""))
            except ValueError:
                n = None

        if n is not None and 1 <= n <= len(inner_sizes):
            labels.append(str(inner_sizes[n - 1]))
        else:
            labels.append(bm)  # fallback

        abs_pcts.append(abs(pct) if pct == pct else float("nan"))

    fig, ax = plt.subplots(figsize=(7, 5), layout="constrained")
    ax.bar(range(len(labels)), abs_pcts)
    ax.axhline(0.0)

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right")

    ax.set_ylabel("Percentage change in loop cycles (%)")
    ax.set_xlabel("Inner loop size")
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200, pad_inches=0.25)
    plt.close(fig)
    print(f"[OK] Wrote plot: {out_path}")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: choose loop id per method from NoNested (largest loop => loop id)
    ref_loop_ids = determine_reference_loop_ids_from_nonested()
    if not ref_loop_ids:
        raise RuntimeError("No reference loop ids found from NoNested runs.")

    # Step 2: extract that SAME loop id from each run in each mode
    rows_nested = extract_selected_loop_cycles("Nested", NESTED_FILES, ref_loop_ids)
    rows_nonested = extract_selected_loop_cycles("NoNested", NONESTED_FILES, ref_loop_ids)
    all_rows = rows_nested + rows_nonested

    # Write per-run CSV
    per_run_csv = OUT_DIR / "per_run_selected_loop_cycles.csv"
    write_csv(
        per_run_csv,
        fieldnames=[
            "benchmark",
            "mode",
            "run_name",
            "file_path",
            "total_cycles",
            "bench_method",
            "comp_id",
            "comp_name",
            "encoding",
            "selected_loop_id",
            "selected_loop_cycles",
        ],
        rows=[
            {
                "benchmark": r.benchmark,
                "mode": r.mode,
                "run_name": r.run_name,
                "file_path": r.file_path,
                "total_cycles": "" if r.total_cycles is None else r.total_cycles,
                "bench_method": r.bench_method,
                "comp_id": r.comp_id,
                "comp_name": r.comp_name,
                "encoding": r.encoding,
                "selected_loop_id": r.selected_loop_id,
                "selected_loop_cycles": r.selected_loop_cycles,
            }
            for r in all_rows
        ],
    )
    print(f"[OK] Wrote {per_run_csv}")

    # Step 3: compute medians, intersection only
    med = compute_medians_per_mode(all_rows)

    methods_no = {m for (mode, m) in med.keys() if mode == "NoNested"}
    methods_ne = {m for (mode, m) in med.keys() if mode == "Nested"}
    common_methods = sorted(methods_no & methods_ne, key=lambda s: int(s.replace("bench", "")))

    per_method: List[dict] = []
    for bm in common_methods:
        m_ne = med[("Nested", bm)]
        m_no = med[("NoNested", bm)]
        pct = safe_pct_change(m_ne, m_no)

        per_method.append(
            {
                "benchmark": BENCHMARK_NAME,
                "bench_method": bm,
                "selected_loop_id": ref_loop_ids.get(bm, ""),
                "median_selected_cycles_nested": m_ne,
                "median_selected_cycles_nonested": m_no,
                "pct_change_nonested_vs_nested": pct,
            }
        )

    # CSV of medians
    per_method_csv = OUT_DIR / "per_method_medians.csv"
    write_csv(
        per_method_csv,
        fieldnames=[
            "benchmark",
            "bench_method",
            "selected_loop_id",
            "median_selected_cycles_nested",
            "median_selected_cycles_nonested",
            "pct_change_nonested_vs_nested",
        ],
        rows=per_method,
    )
    print(f"[OK] Wrote {per_method_csv}")

    # Plot
    plot_path = PLOTS_DIR / f"{BENCHMARK_NAME}_selected_loop_pct_change_per_bench_method.pdf"
    plot_pct_bar(per_method, plot_path)

    # Summary with min/median/max per mode
    print()
    print("===== Summary =====")
    print(f"Benchmark:       {BENCHMARK_NAME}")
    print(f"Class filter:    {BENCH_CLASS}")
    print(f"Nested files:    {len(NESTED_FILES)}")
    print(f"NoNested files:  {len(NONESTED_FILES)}")
    print(f"Comparable methods (present in both modes): {len(common_methods)}")
    print()

    # Build per-mode value lists per bench method (for the selected loop id)
    vals_by_mode: Dict[Tuple[str, str], List[int]] = {}
    for rr in all_rows:
        vals_by_mode.setdefault((rr.mode, rr.bench_method), []).append(rr.selected_loop_cycles)

    for r in per_method:
        bm = r["bench_method"]
        loop_id = r["selected_loop_id"]

        nested_vals = vals_by_mode.get(("Nested", bm), [])
        nonested_vals = vals_by_mode.get(("NoNested", bm), [])

        if not nested_vals or not nonested_vals:
            # Should not happen if it's in common_methods, but keep it safe.
            continue

        n_min, n_med, n_max = min_med_max(nested_vals)
        nn_min, nn_med, nn_max = min_med_max(nonested_vals)

        pct = r["pct_change_nonested_vs_nested"]
        pct_str = "nan" if pct != pct else f"{pct:.2f}%"

        print(f"  {bm} (loop {loop_id}): %Δ={pct_str}")
        print(f"    Nested:   min={n_min}  med={n_med}  max={n_max}")
        print(f"    NoNested: min={nn_min}  med={nn_med}  max={nn_max}")

    print()

    return 0


def min_med_max(vals: List[int]) -> Tuple[int, int, int]:
    vmin = min(vals)
    vmax = max(vals)
    vmed = median_int(vals)
    return vmin, vmed, vmax


if __name__ == "__main__":
    raise SystemExit(main())
