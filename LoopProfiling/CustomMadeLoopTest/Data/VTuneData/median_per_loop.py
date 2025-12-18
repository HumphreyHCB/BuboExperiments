#!/usr/bin/env python3
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

# --------------------------------------------------------------------
# EDIT THESE WHENEVER YOU WANT
# --------------------------------------------------------------------

SLOWDOWN_INPUT_FILE = "2025_12_16_17_17_40_LoopBenchmarks_SlowdownTest_NewRDTSCWhileDevine onlyEditDistance .txt"
NODE_LOOPID_CSV = "loops3.csv"

# Existing output (per-loop totals)
OUTPUT_LOOP_TOTALS_CSV = "total_pct_slowdown_per_loop.csv"

# NEW: per-block mapping output (what you asked for)
OUTPUT_BLOCK_MAP_CSV = "block_times_per_loop.csv"

SLOWDOWN_BLOCK_ID_IS_VTUNE = True
BRIDGE_JSON = "/home/hb478/repos/GTSlowdownSchedular/FinalBuboTests/LoopBenchmarks/Final_LoopBenchmarks.json"
ENABLE_METHOD_FALLBACK_MATCH = True

# Keep rows with normal_time > MIN_NORMAL_TIME_PER_BLOCK
MIN_NORMAL_TIME_PER_BLOCK = 0.0

# --------------------------------------------------------------------

LINE_RE = re.compile(
    r"^Method:\s*(?P<method>.*?),\s*"
    r"Block ID:\s*(?P<block>\d+),\s*"
    r"Normal Time:\s*(?P<normal>-?\d+(?:\.\d+)?),\s*"
    r"Slowdown Time:\s*(?P<slow>-?\d+(?:\.\d+)?),\s*"
    r"Percentage Increase:\s*(?P<pct>-?\d+(?:\.\d+)?)(?:%)?\s*$"
)

NODE_RE = re.compile(r"^b(?P<num>\d+)$")
BRIDGE_KEY_RE = re.compile(r"^\s*(?P<graal>\d+)\s*\(Vtune Block\s*(?P<vtune>\d+)\)\s*$")


@dataclass(frozen=True)
class BlockRow:
    method_raw: str
    method_norm: str
    block_id: int          # as read from slowdown file (VTune id if SLOWDOWN_BLOCK_ID_IS_VTUNE)
    normal_time: float
    slowdown_time: float


def normalise_method_name(s: str) -> str:
    return s.strip().replace("::", ".")


def read_slowdown_blocks(path: str) -> Tuple[List[BlockRow], int, int]:
    rows: List[BlockRow] = []
    matched = 0
    total = 0

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            total += 1
            line = raw.strip()
            m = LINE_RE.match(line)
            if not m:
                continue

            method_raw = m.group("method").strip()
            rows.append(BlockRow(
                method_raw=method_raw,
                method_norm=normalise_method_name(method_raw),
                block_id=int(m.group("block")),
                normal_time=float(m.group("normal")),
                slowdown_time=float(m.group("slow")),
            ))
            matched += 1

    return rows, matched, total


def read_bridge_vtune_to_graal(path: str) -> Dict[str, Dict[int, int]]:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))

    out: Dict[str, Dict[int, int]] = {}

    for method, mapping in data.items():
        method_norm = normalise_method_name(method)
        vtune_to_graal: Dict[int, int] = {}

        if not isinstance(mapping, dict):
            continue

        for k in mapping.keys():
            m = BRIDGE_KEY_RE.match(str(k))
            if not m:
                continue
            graal_id = int(m.group("graal"))
            vtune_id = int(m.group("vtune"))
            vtune_to_graal[vtune_id] = graal_id

        out[method_norm] = vtune_to_graal

    return out


def read_dot_node_map(path: str) -> Tuple[
    Dict[str, List[int]],
    Dict[Tuple[int, str, int], int]
]:
    method_to_comps_set: Dict[str, set] = defaultdict(set)
    node_map: Dict[Tuple[int, str, int], int] = {}

    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        r = csv.DictReader(f)
        needed = {"comp_id", "method", "node", "loop_id"}
        if not needed.issubset(set(r.fieldnames or [])):
            raise ValueError(f"{path} must have columns: {sorted(needed)}")

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

            comp_id = int(comp_s)
            method_norm = normalise_method_name(method)
            block_id = int(nm.group("num"))
            loop_id = int(loop_s)

            node_map[(comp_id, method_norm, block_id)] = loop_id
            method_to_comps_set[method_norm].add(comp_id)

    method_to_comps_sorted: Dict[str, List[int]] = {m: sorted(cs) for m, cs in method_to_comps_set.items()}
    return method_to_comps_sorted, node_map


def find_comp_for_method_block(
    method_norm: str,
    block_id: int,
    method_to_comps: Dict[str, List[int]],
    node_map: Dict[Tuple[int, str, int], int],
) -> Optional[int]:
    comps = method_to_comps.get(method_norm, [])
    for cid in reversed(comps):  # newest first
        if (cid, method_norm, block_id) in node_map:
            return cid

    if not ENABLE_METHOD_FALLBACK_MATCH:
        return None

    alt = method_norm.replace(".", "::") if "." in method_norm else method_norm.replace("::", ".")
    alt_norm = normalise_method_name(alt)
    comps = method_to_comps.get(alt_norm, [])
    for cid in reversed(comps):
        if (cid, alt_norm, block_id) in node_map:
            return cid
    return None


@dataclass
class LoopAgg:
    num_blocks: int = 0
    sum_normal: float = 0.0
    sum_slow: float = 0.0


def safe_pct_increase(normal: float, slow: float) -> float:
    if normal <= 0.0:
        return 0.0
    return ((slow - normal) / normal) * 100.0


def main() -> None:
    blocks, matched, total = read_slowdown_blocks(SLOWDOWN_INPUT_FILE)
    method_to_comps, node_map = read_dot_node_map(NODE_LOOPID_CSV)

    vtune_to_graal_by_method: Dict[str, Dict[int, int]] = {}
    if SLOWDOWN_BLOCK_ID_IS_VTUNE:
        vtune_to_graal_by_method = read_bridge_vtune_to_graal(BRIDGE_JSON)

    grouped: Dict[Tuple[int, str, int], LoopAgg] = defaultdict(LoopAgg)

    # NEW: store per-block rows for the debug CSV
    block_rows: List[Tuple] = []

    missing_methodblock = 0
    missing_block = 0
    used = 0
    missing_bridge = 0
    skipped_normal = 0

    for br in blocks:
        if br.normal_time <= MIN_NORMAL_TIME_PER_BLOCK:
            skipped_normal += 1
            continue

        method_norm = br.method_norm

        vtune_block_id: Optional[int] = None
        graal_block_id: Optional[int] = None

        # Interpret br.block_id as VTune id if enabled
        if SLOWDOWN_BLOCK_ID_IS_VTUNE:
            vtune_block_id = br.block_id
            vtune_to_graal = vtune_to_graal_by_method.get(method_norm)
            if not vtune_to_graal:
                missing_bridge += 1
                continue
            graal_block = vtune_to_graal.get(vtune_block_id)
            if graal_block is None:
                missing_bridge += 1
                continue
            graal_block_id = graal_block
            block_id_for_node_map = graal_block_id
        else:
            # slowdown file already uses graal block ids
            graal_block_id = br.block_id
            block_id_for_node_map = graal_block_id

        comp_id = find_comp_for_method_block(method_norm, block_id_for_node_map, method_to_comps, node_map)
        if comp_id is None:
            missing_methodblock += 1
            continue

        loop_id = node_map.get((comp_id, method_norm, block_id_for_node_map))
        if loop_id is None:
            missing_block += 1
            continue

        # Aggregate per-loop totals
        g = grouped[(comp_id, method_norm, loop_id)]
        g.num_blocks += 1
        g.sum_normal += br.normal_time
        g.sum_slow += br.slowdown_time
        used += 1

        # Record per-block mapping row (we’ll enrich with totals later)
        block_rows.append((
            comp_id,
            method_norm,
            loop_id,
            vtune_block_id if vtune_block_id is not None else "",
            graal_block_id if graal_block_id is not None else "",
            br.normal_time,
            br.slowdown_time,
            safe_pct_increase(br.normal_time, br.slowdown_time),
        ))

    # Build per-loop totals output
    out_rows = []
    for (comp_id, method_norm, loop_id), g in grouped.items():
        pct = safe_pct_increase(g.sum_normal, g.sum_slow)
        out_rows.append((comp_id, method_norm, loop_id, g.num_blocks, g.sum_normal, g.sum_slow, pct))
    out_rows.sort(key=lambda r: (r[0], r[1], r[2]))

    with open(OUTPUT_LOOP_TOTALS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "comp_id", "method", "loop_id",
            "num_blocks_matched",
            "total_normal_time", "total_slowdown_time",
            "median_pct_slowdown"
        ])
        w.writerows(out_rows)

    # NEW: per-block CSV, enriched with loop totals + shares
    # map loop key -> totals
    loop_totals: Dict[Tuple[int, str, int], Tuple[float, float, float]] = {}
    for (comp_id, method_norm, loop_id), g in grouped.items():
        loop_totals[(comp_id, method_norm, loop_id)] = (g.sum_normal, g.sum_slow, safe_pct_increase(g.sum_normal, g.sum_slow))

    enriched_block_rows = []
    for (comp_id, method_norm, loop_id, vtune_id, graal_id, n, s, pct_block) in block_rows:
        tn, ts, pct_loop = loop_totals.get((comp_id, method_norm, loop_id), (0.0, 0.0, 0.0))
        share_normal = (n / tn) if tn > 0.0 else 0.0
        share_slow = (s / ts) if ts > 0.0 else 0.0
        enriched_block_rows.append((
            comp_id, method_norm, loop_id,
            vtune_id, graal_id,
            n, s, pct_block,
            tn, ts, pct_loop,
            share_normal, share_slow
        ))

    enriched_block_rows.sort(key=lambda r: (r[0], r[1], r[2], int(r[4]) if str(r[4]).isdigit() else 10**9))

    with open(OUTPUT_BLOCK_MAP_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "comp_id", "method", "loop_id",
            "vtune_block_id", "graal_block_id",
            "normal_time", "slowdown_time", "pct_increase_block",
            "loop_total_normal_time", "loop_total_slowdown_time", "pct_increase_loop_total",
            "block_share_of_loop_normal", "block_share_of_loop_slowdown"
        ])
        w.writerows(enriched_block_rows)

    print(f"Read {total} lines, matched {matched} per-block lines.")
    print(f"Used {used} block->loop matches.")
    print(f"Missing method+block (no comp contained that block for the method): {missing_methodblock}")
    print(f"Missing blocks (comp chosen, but block not in node_map): {missing_block}")
    if SLOWDOWN_BLOCK_ID_IS_VTUNE:
        print(f"Bridge misses (no VTune->Graal mapping for row): {missing_bridge}")
    if MIN_NORMAL_TIME_PER_BLOCK > 0.0:
        print(f"Skipped rows with normal_time <= {MIN_NORMAL_TIME_PER_BLOCK}: {skipped_normal}")
    else:
        print(f"Skipped rows with normal_time <= 0.0: {skipped_normal}")
    print(f"Wrote CSV (loop totals): {OUTPUT_LOOP_TOTALS_CSV}")
    print(f"Wrote CSV (block map):  {OUTPUT_BLOCK_MAP_CSV}")


if __name__ == "__main__":
    main()
