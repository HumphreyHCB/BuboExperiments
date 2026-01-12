#!/usr/bin/env python3
import argparse
import csv
import os
import sys
from collections import defaultdict
from typing import Dict, Set, Tuple, List, Optional


def die(msg: str, code: int = 1) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def normalise_method(s: str) -> str:
    # Make method names comparable between method_dot and method
    # (your pipeline already uses :: <-> . normalisation elsewhere)
    return (s or "").strip().replace("::", ".")


def read_plot_master(plot_csv: str) -> Dict[Tuple[str, int], int]:
    """
    Read master mapping from plots CSV:
      key = (method_norm, loop_id) -> comp_id  (MASTER truth)
    """
    if not os.path.isfile(plot_csv):
        die(f"Plots CSV not found: {plot_csv}")

    out: Dict[Tuple[str, int], int] = {}

    with open(plot_csv, "r", encoding="utf-8", errors="replace", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            die(f"Plots CSV has no header row: {plot_csv}")

        fields = {h.strip() for h in r.fieldnames if h}
        needed = {"comp_id", "method_dot", "loop_id"}
        if not needed.issubset(fields):
            die(f"Plots CSV must contain columns {sorted(needed)}; found {sorted(fields)}")

        bad = 0
        for line_no, row in enumerate(r, start=2):
            comp_s = (row.get("comp_id") or "").strip()
            meth_s = (row.get("method_dot") or "").strip()
            loop_s = (row.get("loop_id") or "").strip()

            if not comp_s or not meth_s or not loop_s:
                bad += 1
                continue

            try:
                comp_id = int(comp_s)
                loop_id = int(loop_s)
            except ValueError:
                die(f"Plots CSV: non-integer comp_id/loop_id at line {line_no}: "
                    f"comp_id='{comp_s}', loop_id='{loop_s}'")

            key = (normalise_method(meth_s), loop_id)

            # If the plots CSV ever has conflicting comp_ids for same (method, loop),
            # that indicates a broken plot file.
            prev = out.get(key)
            if prev is not None and prev != comp_id:
                die(f"Plots CSV conflict: (method={key[0]}, loop_id={loop_id}) has comp_id {prev} and {comp_id}")

            out[key] = comp_id

        if bad:
            die(f"Plots CSV has {bad} rows with missing comp_id/method_dot/loop_id (plots should be fully filled).")

    if not out:
        die(f"Plots CSV contained 0 usable (method, loop_id) rows: {plot_csv}")

    return out


def read_vtune_comp_sets(vtune_csv: str) -> Dict[Tuple[str, int], Set[int]]:
    """
    Read VTune mapping:
      key = (method_norm, loop_id) -> {comp_id,...}
    """
    if not os.path.isfile(vtune_csv):
        die(f"VTune CSV not found: {vtune_csv}")

    out: Dict[Tuple[str, int], Set[int]] = defaultdict(set)

    with open(vtune_csv, "r", encoding="utf-8", errors="replace", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            die(f"VTune CSV has no header row: {vtune_csv}")

        fields = {h.strip() for h in r.fieldnames if h}
        needed = {"comp_id", "method", "loop_id"}
        if not needed.issubset(fields):
            die(f"VTune CSV must contain columns {sorted(needed)}; found {sorted(fields)}")

        for line_no, row in enumerate(r, start=2):
            comp_s = (row.get("comp_id") or "").strip()
            meth_s = (row.get("method") or "").strip()
            loop_s = (row.get("loop_id") or "").strip()

            if not comp_s or not meth_s or not loop_s:
                continue

            try:
                comp_id = int(comp_s)
                loop_id = int(loop_s)
            except ValueError:
                die(f"VTune CSV: non-integer comp_id/loop_id at line {line_no}: "
                    f"comp_id='{comp_s}', loop_id='{loop_s}'")

            key = (normalise_method(meth_s), loop_id)
            out[key].add(comp_id)

    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Check that VTune rows used the SAME comp_id as the plots CSV (master truth) "
                    "for each (method, loop_id)."
    )

    # Explicit paths
    ap.add_argument("--plots-csv", default=None, help="Master plots CSV (contains comp_id, method_dot, loop_id).")
    ap.add_argument("--vtune-csv", default=None, help="VTune block_times_per_loop.csv (contains comp_id, method, loop_id).")

    # Optional dynamic derivation (if you want it)
    ap.add_argument("--benchmark", default=None, help="Benchmark name (e.g. LoopBenchmarks).")
    ap.add_argument("--plots-dir", default="plots", help="Plots dir (default: plots).")
    ap.add_argument("--processed-dir", default=None,
                    help="Processed dir for this run (e.g. processed/AWFY/LoopBenchmarks/WithProbe).")

    ap.add_argument("--max-errors", type=int, default=50, help="Max mismatches to print (default: 50).")

    args = ap.parse_args()

    plots_csv = args.plots_csv
    vtune_csv = args.vtune_csv

    if plots_csv is None:
        if not args.benchmark:
            die("Provide --plots-csv or --benchmark/--plots-dir.")
        plots_csv = os.path.join(args.plots_dir, f"{args.benchmark}_bubo_loops.csv")

    if vtune_csv is None:
        if not args.processed_dir:
            die("Provide --vtune-csv or --processed-dir.")
        vtune_csv = os.path.join(args.processed_dir, "vtune", "block_times_per_loop.csv")

    master = read_plot_master(plots_csv)
    vtune = read_vtune_comp_sets(vtune_csv)

    missing_in_vtune: List[Tuple[str, int, int]] = []
    wrong_comp: List[Tuple[str, int, int, List[int]]] = []

    for (method_norm, loop_id), master_comp in sorted(master.items()):
        vtune_comps = vtune.get((method_norm, loop_id))
        if not vtune_comps:
            missing_in_vtune.append((method_norm, loop_id, master_comp))
            continue
        if master_comp not in vtune_comps:
            wrong_comp.append((method_norm, loop_id, master_comp, sorted(vtune_comps)))

    if missing_in_vtune or wrong_comp:
        print("[FAIL] Detected VTune/plots comp_id mismatch.", file=sys.stderr)
        print(f"[INFO] plots rows checked: {len(master)}", file=sys.stderr)
        print(f"[INFO] vtune keys found:   {len(vtune)}", file=sys.stderr)

        if missing_in_vtune:
            print(f"[ERROR] Missing in VTune (method, loop_id) count: {len(missing_in_vtune)}", file=sys.stderr)

        if wrong_comp:
            print(f"[ERROR] Wrong comp_id used by VTune count: {len(wrong_comp)}", file=sys.stderr)

        limit = max(0, args.max_errors)

        if missing_in_vtune:
            print("\n--- Missing in VTune (present in plots) ---", file=sys.stderr)
            for m, lid, mc in missing_in_vtune[:limit]:
                print(f"[MISSING] method={m} loop_id={lid} plots_comp_id={mc}", file=sys.stderr)
            if limit and len(missing_in_vtune) > limit:
                print(f"[INFO] ... {len(missing_in_vtune)-limit} more missing not shown.", file=sys.stderr)

        if wrong_comp:
            print("\n--- Wrong comp_id (VTune differs from plots master) ---", file=sys.stderr)
            for m, lid, mc, vt in wrong_comp[:limit]:
                print(f"[MISMATCH] method={m} loop_id={lid} plots_comp_id={mc} vtune_comp_ids={vt}", file=sys.stderr)
            if limit and len(wrong_comp) > limit:
                print(f"[INFO] ... {len(wrong_comp)-limit} more mismatches not shown.", file=sys.stderr)

        sys.exit(1)

    print("[OK] VTune used the same comp_id as plots (master) for every (method, loop_id) in the plot CSV.")
    print(f"[OK] checked {len(master)} (method, loop_id) keys from plots against VTune.")
    sys.exit(0)


if __name__ == "__main__":
    main()
