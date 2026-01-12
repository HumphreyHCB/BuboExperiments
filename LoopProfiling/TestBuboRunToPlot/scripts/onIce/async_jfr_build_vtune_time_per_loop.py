#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Tuple, Optional, List

NODE_RE = re.compile(r"^b(?P<num>\d+)$")

# ------------------------------------------------------------
# Normalisation
# ------------------------------------------------------------

def normalise_method_name(s: str) -> str:
    # Your pipeline generally uses "." not "::"
    return (s or "").strip().replace("::", ".")

def method_alternatives(method_norm: str) -> List[str]:
    # allow matching VTune CSV that might use "::" or "."
    if "." in method_norm:
        return [method_norm, method_norm.replace(".", "::")]
    if "::" in method_norm:
        return [method_norm, method_norm.replace("::", ".")]
    return [method_norm]

# ------------------------------------------------------------
# Read loops.csv (CFG output)
#   columns: comp_id, method, node, loop_id
# node is like "b123" where 123 is graal block id
# ------------------------------------------------------------

def read_loops_csv(path: str) -> List[Tuple[int, str, int, int]]:
    """
    Returns rows: (comp_id, method_norm, loop_id, graal_block_id)
    """
    out = []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        r = csv.DictReader(f)
        needed = {"comp_id", "method", "node", "loop_id"}
        if not needed.issubset(set(r.fieldnames or [])):
            raise SystemExit(f"{path} must have columns: {sorted(needed)} (found: {r.fieldnames})")

        for row in r:
            comp_s = (row.get("comp_id") or "").strip()
            method = (row.get("method") or "").strip()
            node = (row.get("node") or "").strip()
            loop_s = (row.get("loop_id") or "").strip()
            if not comp_s or not method or not node or not loop_s:
                continue

            nm = NODE_RE.match(node)
            if not nm:
                continue

            try:
                comp_id = int(comp_s)
                loop_id = int(loop_s)
                graal_bid = int(nm.group("num"))
            except ValueError:
                continue

            method_norm = normalise_method_name(method)
            out.append((comp_id, method_norm, loop_id, graal_bid))

    return out

# ------------------------------------------------------------
# Read MarkerPhaseInfo.json
# format: { "method": [ {"GraalID":..., "VtuneBlock":...}, ... ], ... }
# ------------------------------------------------------------

def read_markerphase_graal_to_vtune(markerphase_json: str) -> Dict[str, Dict[int, int]]:
    data = json.loads(Path(markerphase_json).read_text(encoding="utf-8"))
    out: Dict[str, Dict[int, int]] = {}

    if not isinstance(data, dict):
        return out

    for method, arr in data.items():
        method_norm = normalise_method_name(method)
        g2v: Dict[int, int] = {}

        if not isinstance(arr, list):
            continue

        for entry in arr:
            if not isinstance(entry, dict):
                continue
            g = entry.get("GraalID")
            v = entry.get("VtuneBlock")
            if g is None or v is None:
                continue
            try:
                graal_id = int(str(g).strip())
                vtune_id = int(str(v).strip())
            except ValueError:
                continue
            g2v[graal_id] = vtune_id

        out[method_norm] = g2v

    return out

# ------------------------------------------------------------
# Read VTune per-block times CSV
# You tell this script which file; it will detect columns.
#
# Supported header patterns:
# - method + vtune_block_id + time
# - method + block_id + time
# - method + VtuneBlock + time
#
# "time" column candidates (first match wins):
#   total_time, cpu_time, time, Self Time, self_time, inclusive_time, samples, cycles
# ------------------------------------------------------------

def detect_vtune_columns(fieldnames: List[str]) -> Tuple[str, str, str]:
    if not fieldnames:
        raise SystemExit("VTune CSV has no header")

    # method column
    method_col = None
    for c in ("method", "Method", "function", "Function", "symbol", "Symbol"):
        if c in fieldnames:
            method_col = c
            break
    if method_col is None:
        raise SystemExit(f"Could not find a method/function column in VTune CSV. Found: {fieldnames}")

    # block id column (VTune block id)
    block_col = None
    for c in ("vtune_block_id", "VtuneBlock", "block_id", "BlockID", "block", "Block"):
        if c in fieldnames:
            block_col = c
            break
    if block_col is None:
        raise SystemExit(f"Could not find a block id column in VTune CSV. Found: {fieldnames}")

    # time column
    time_col = None
    for c in (
        "total_time", "cpu_time", "time",
        "Self Time", "self_time", "inclusive_time",
        "samples", "cycles",
        "Total Time", "CPU Time"
    ):
        if c in fieldnames:
            time_col = c
            break
    if time_col is None:
        raise SystemExit(
            "Could not find a time column in VTune CSV.\n"
            f"Found headers: {fieldnames}\n"
            "Add one of: total_time/cpu_time/time/Self Time/self_time/inclusive_time/samples/cycles"
        )

    return method_col, block_col, time_col

def read_vtune_block_times(vtune_csv: str) -> Dict[Tuple[str, int], float]:
    """
    Returns: {(method_norm, vtune_block_id) -> time_value}
    If the CSV contains multiple rows for same key, times are summed.
    """
    out: Dict[Tuple[str, int], float] = defaultdict(float)

    with open(vtune_csv, "r", encoding="utf-8", errors="replace", newline="") as f:
        r = csv.DictReader(f)
        method_col, block_col, time_col = detect_vtune_columns(r.fieldnames or [])

        bad = 0
        for row in r:
            m_raw = (row.get(method_col) or "").strip()
            b_raw = (row.get(block_col) or "").strip()
            t_raw = (row.get(time_col) or "").strip()
            if not m_raw or not b_raw or not t_raw:
                continue

            # method normalisation
            m_norm = normalise_method_name(m_raw)

            try:
                bid = int(float(b_raw))  # tolerate "12.0"
                tv = float(t_raw)
            except ValueError:
                bad += 1
                continue

            out[(m_norm, bid)] += tv

    return dict(out)

# ------------------------------------------------------------
# Main compute: loops.csv + markerphase + vtune times => per-loop totals
# ------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Compute VTune time per loop using CFG loops.csv (Graal blocks per loop) + MarkerPhaseInfo.json (Graal->VTune) + VTune per-block times CSV."
    )
    ap.add_argument("--loops-csv", required=True, help="processed/.../cfg/loops.csv")
    ap.add_argument("--markerphase-json", required=True, help="MarkerPhaseInfo.json (GraalID->VtuneBlock per method)")
    ap.add_argument("--vtune-block-times-csv", required=True, help="CSV with per-method per-vtune-block times")
    ap.add_argument("--out-loop-totals-csv", required=True, help="Output: per-loop summed VTune time")
    ap.add_argument("--out-block-map-csv", required=True, help="Output: expanded mapping rows for debugging")
    args = ap.parse_args()

    loops_rows = read_loops_csv(args.loops_csv)
    g2v_by_method = read_markerphase_graal_to_vtune(args.markerphase_json)
    vtune_times = read_vtune_block_times(args.vtune_block_times_csv)

    # Build mapping + totals
    loop_totals: Dict[Tuple[int, str, int], float] = defaultdict(float)
    loop_block_counts: Dict[Tuple[int, str, int], int] = defaultdict(int)

    # for debugging output
    expanded_rows = []

    missing_marker_method = 0
    missing_marker_block = 0
    missing_vtune_time = 0
    used = 0

    for comp_id, method_norm, loop_id, graal_bid in loops_rows:
        # find method mapping (try alternatives)
        g2v = None
        chosen_method = None
        for m_alt in method_alternatives(method_norm):
            m_alt_norm = normalise_method_name(m_alt)
            if m_alt_norm in g2v_by_method:
                g2v = g2v_by_method[m_alt_norm]
                chosen_method = m_alt_norm
                break
        if g2v is None:
            missing_marker_method += 1
            expanded_rows.append({
                "comp_id": comp_id,
                "method": method_norm,
                "loop_id": loop_id,
                "graal_block_id": graal_bid,
                "vtune_block_id": "",
                "vtune_time": "",
                "status": "missing_markerphase_method",
            })
            continue

        vtune_bid = g2v.get(graal_bid)
        if vtune_bid is None:
            missing_marker_block += 1
            expanded_rows.append({
                "comp_id": comp_id,
                "method": chosen_method,
                "loop_id": loop_id,
                "graal_block_id": graal_bid,
                "vtune_block_id": "",
                "vtune_time": "",
                "status": "missing_markerphase_block",
            })
            continue

        # get VTune time for (method, vtune block id) with method fallback
        tv = None
        for m_alt in method_alternatives(chosen_method):
            m_alt_norm = normalise_method_name(m_alt)
            key = (m_alt_norm, vtune_bid)
            if key in vtune_times:
                tv = vtune_times[key]
                break

        if tv is None:
            missing_vtune_time += 1
            expanded_rows.append({
                "comp_id": comp_id,
                "method": chosen_method,
                "loop_id": loop_id,
                "graal_block_id": graal_bid,
                "vtune_block_id": vtune_bid,
                "vtune_time": "",
                "status": "missing_vtune_time",
            })
            continue

        loop_key = (comp_id, chosen_method, loop_id)
        loop_totals[loop_key] += tv
        loop_block_counts[loop_key] += 1
        used += 1

        expanded_rows.append({
            "comp_id": comp_id,
            "method": chosen_method,
            "loop_id": loop_id,
            "graal_block_id": graal_bid,
            "vtune_block_id": vtune_bid,
            "vtune_time": tv,
            "status": "ok",
        })

    # Write expanded block map
    os.makedirs(os.path.dirname(args.out_block_map_csv) or ".", exist_ok=True)
    with open(args.out_block_map_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "comp_id", "method", "loop_id",
                "graal_block_id", "vtune_block_id",
                "vtune_time",
                "status",
            ],
        )
        w.writeheader()
        w.writerows(expanded_rows)

    # Write per-loop totals
    os.makedirs(os.path.dirname(args.out_loop_totals_csv) or ".", exist_ok=True)
    totals_rows = []
    for (comp_id, method_norm, loop_id), total_time in loop_totals.items():
        totals_rows.append({
            "comp_id": comp_id,
            "method": method_norm,
            "loop_id": loop_id,
            "num_blocks_with_time": loop_block_counts[(comp_id, method_norm, loop_id)],
            "total_vtune_time": total_time,
        })
    totals_rows.sort(key=lambda r: (int(r["comp_id"]), r["method"], int(r["loop_id"])))

    with open(args.out_loop_totals_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["comp_id", "method", "loop_id", "num_blocks_with_time", "total_vtune_time"],
        )
        w.writeheader()
        w.writerows(totals_rows)

    print("[INFO] loops.csv rows:", len(loops_rows))
    print("[INFO] used blocks:", used)
    print("[INFO] missing markerphase method:", missing_marker_method)
    print("[INFO] missing markerphase block:", missing_marker_block)
    print("[INFO] missing vtune time:", missing_vtune_time)
    print("[OK] wrote:", args.out_loop_totals_csv)
    print("[OK] wrote:", args.out_block_map_csv)


if __name__ == "__main__":
    main()
