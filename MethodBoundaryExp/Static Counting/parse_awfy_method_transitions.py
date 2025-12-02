#!/usr/bin/env python3
"""
parse_awfy_method_transitions_last.py

Hard-coded paths. No CLI args.
- Keeps the LAST transitions value per (Benchmark, Compilation Unit)
- Sums per-benchmark totals
- Emits 2 CSVs and a bar chart PNG of benchmark totals.
"""

import csv
import re
from pathlib import Path
from collections import defaultdict, OrderedDict
import matplotlib.pyplot as plt

# --------- HARD-CODED PATHS (edit if you move things) ----------
BASE_DIR = Path(__file__).resolve().parent
UNITS_CSV = BASE_DIR / "all_compilation_units.csv"
LOGS_DIR  = BASE_DIR / "logs_awfy_2025-10-21_20-26-31"

OUT_UNITS_CSV = BASE_DIR / "awfy_unit_last_transitions.csv"
OUT_BENCH_CSV = BASE_DIR / "awfy_benchmark_totals.csv"
OUT_PNG       = BASE_DIR / "awfy_benchmark_totals.png"
# ---------------------------------------------------------------

# Pairing regex: comp ... next transitions (works across newlines)
PAIR_RE = re.compile(
    r"HumphreysTestPhase:\s*comp\s*=\s*(.*?)\s*HumphreysTestPhase:\s*method transitions\s*=\s*(\d+)",
    re.DOTALL
)

def normalize_comp_name(comp: str) -> str:
    """
    Normalize a comp string to compare with the CSV 'Compilation Unit'.
    - Trim whitespace.
    - Drop trailing argument list: Foo.bar(int,java.lang.String) -> Foo.bar
    """
    comp = comp.strip()
    m = re.match(r"^([^(\s]+)", comp)
    return m.group(1) if m else comp

def load_allowlist(units_csv_path: Path):
    """
    Load CSV mapping Benchmark -> set of allowed Compilation Units.
    The sample header shows 'enchmark,Compilation Unit', so we just take first two cols.
    """
    allow = defaultdict(set)
    with units_csv_path.open(newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader, None)  # ignore header
        for row in reader:
            if not row or len(row) < 2:
                continue
            benchmark = row[0].strip()
            unit = row[1].strip()
            if benchmark and unit:
                allow[benchmark].add(unit)
    return allow

def parse_log_file(path: Path):
    """
    Return a list of (comp_raw, transitions:int) in textual order.
    """
    text = path.read_text(encoding='utf-8', errors='replace')
    pairs = []
    for comp_raw, transitions in PAIR_RE.findall(text):
        pairs.append((comp_raw.strip(), int(transitions)))
    return pairs

def main():
    if not UNITS_CSV.exists():
        raise FileNotFoundError(f"Missing {UNITS_CSV}")
    if not LOGS_DIR.exists():
        raise FileNotFoundError(f"Missing {LOGS_DIR}")

    allowlist = load_allowlist(UNITS_CSV)

    # Keep LAST transitions per (benchmark, unit)
    # Initialize zeros for all allowed units so they appear even if unseen.
    unit_last = defaultdict(lambda: defaultdict(int))  # bench -> unit -> last transitions

    # Touch defaults
    for bench, units in allowlist.items():
        for u in units:
            _ = unit_last[bench][u]

    # Walk logs (14 files)
    for log_path in sorted(LOGS_DIR.glob("*.log")):
        bench = log_path.stem  # Bounce, CD, ...
        allowed_units = allowlist.get(bench, set())
        if not allowed_units:
            # No allow-list entries for this benchmark; skip.
            continue

        pairs = parse_log_file(log_path)
        # IMPORTANT: use the LAST one seen for each unit
        for comp_raw, transitions in pairs:
            comp_norm = normalize_comp_name(comp_raw)
            if comp_norm in allowed_units:
                unit_last[bench][comp_norm] = transitions  # overwrite with latest

    # Build per-benchmark totals (sum of last values across its units)
    bench_totals = OrderedDict()
    for bench in sorted(unit_last.keys()):
        total = sum(unit_last[bench].values())
        bench_totals[bench] = total

    # --- Write per-unit CSV (last transitions) ---
    OUT_UNITS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_UNITS_CSV.open("w", newline='', encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Benchmark", "Compilation Unit", "Last Transitions"])
        for bench in sorted(unit_last.keys()):
            for unit in sorted(unit_last[bench].keys()):
                w.writerow([bench, unit, unit_last[bench][unit]])

    # --- Write per-benchmark totals CSV ---
    with OUT_BENCH_CSV.open("w", newline='', encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Benchmark", "Total Transitions"])
        for bench, total in bench_totals.items():
            w.writerow([bench, total])

    # --- Bar chart of totals ---
    benches = list(bench_totals.keys())
    totals  = list(bench_totals.values())

    # Sort by total descending for a nicer chart
    benches, totals = zip(*sorted(zip(benches, totals), key=lambda x: x[1], reverse=True)) if benches else ([], [])

    plt.figure(figsize=(12, 6))
    plt.bar(benches, totals)
    plt.xticks(rotation=45, ha="right")
    plt.ylabel("Total Transitions")
    plt.title("AWFY: Total Method Transitions per Benchmark")
    plt.tight_layout()
    plt.savefig(OUT_PNG, dpi=200)

    print(f"Wrote per-unit CSV: {OUT_UNITS_CSV}")
    print(f"Wrote per-benchmark totals CSV: {OUT_BENCH_CSV}")
    print(f"Wrote bar chart: {OUT_PNG}")

if __name__ == "__main__":
    main()
