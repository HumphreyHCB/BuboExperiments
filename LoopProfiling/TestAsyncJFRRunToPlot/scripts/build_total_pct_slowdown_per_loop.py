#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
from collections import defaultdict

# ============================================================
# Part 1: HumphreysDebugDataPhase output -> per comp DOT files
# ============================================================

@dataclass
class Block:
    bid: int
    successors: List[int] = field(default_factory=list)
    loop: Optional[str] = None
    sources: List[str] = field(default_factory=list)

@dataclass
class Compilation:
    name: str
    blocks: Dict[int, Block]

def parse_debug_output(text: str) -> List[Compilation]:
    lines = text.splitlines()
    comps: List[Compilation] = []

    in_comp = False
    comp_name: Optional[str] = None
    blocks: Dict[int, Block] = {}
    current_block: Optional[Block] = None
    mode: Optional[str] = None

    for raw in lines:
        line = raw.strip()

        if line.startswith("=== HumphreysDebugDataPhase ==="):
            in_comp = True
            comp_name = None
            blocks = {}
            current_block = None
            mode = None
            continue

        if not in_comp:
            continue

        if line.startswith("=== End HumphreysDebugDataPhase ==="):
            if comp_name is None:
                comp_name = "<unknown-compilation>"
            comps.append(Compilation(name=comp_name, blocks=blocks))
            in_comp = False
            current_block = None
            mode = None
            continue

        if line.startswith("Compilation: "):
            comp_name = line[len("Compilation: "):].strip()
            continue

        if line.startswith("Number of loops:"):
            continue

        if line.startswith("Block "):
            m = re.match(r"Block\s+(\d+)", line)
            if not m:
                continue
            bid = int(m.group(1))
            current_block = Block(bid)
            blocks[bid] = current_block
            mode = None
            continue

        if line.startswith("Successors:"):
            mode = "succ"
            continue

        if line.startswith("Predecessors:"):
            mode = "pred"
            continue

        if line.startswith("In loop:"):
            if current_block is not None:
                val = line[len("In loop:"):].strip()
                current_block.loop = None if val == "<none>" else val
            continue

        if line.startswith("Source positions in block:"):
            mode = "src"
            continue

        if current_block is None:
            continue

        if mode == "succ":
            if "->" in line:
                succ_str = line.split("->", 1)[1].strip()
                if succ_str:
                    try:
                        current_block.successors.append(int(succ_str))
                    except ValueError:
                        pass
            continue

        if mode == "src":
            if line and line != "<none>":
                current_block.sources.append(line)
            continue

    return comps

def gv_escape(s: str) -> str:
    s = s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")
    return s

def sanitize_name(name: str) -> str:
    base = re.sub(r"[^0-9A-Za-z._]+", "_", name)
    if len(base) > 80:
        base = base[:80]
    return base or "compilation"

def compilation_to_dot(comp: Compilation, comp_index: int) -> str:
    out: List[str] = []

    graph_name = f"CFG_{comp_index}"
    out.append(f"digraph {graph_name} {{")
    out.append("  rankdir=LR;")
    out.append("  graph [fontsize=20, ranksep=1.5, nodesep=1.0, overlap=false, splines=true];")
    out.append("  node [shape=box, style=filled, fontname=\"Helvetica\", fontsize=10];")
    out.append(f"  label=\"{gv_escape(comp.name)}\";")
    out.append("  labelloc=top;")
    out.append("  labeljust=left;")

    palette = [
        "lightblue", "lightgreen", "lightpink", "gold", "orange",
        "violet", "khaki", "plum", "lightcyan", "lightcoral"
    ]

    loop_ids = sorted({b.loop for b in comp.blocks.values() if b.loop is not None})
    loop_color = {loop: palette[i % len(palette)] for i, loop in enumerate(loop_ids)}

    for bid, block in sorted(comp.blocks.items()):
        node_name = f"b{bid}"

        label_lines = [f"B{bid}"]
        label_lines.append(f"Loop: {block.loop if block.loop is not None else '<none>'}")

        if block.sources:
            label_lines.append("---")
            label_lines.extend(block.sources)

        label = gv_escape("\n".join(label_lines))
        fill = "white" if block.loop is None else loop_color.get(block.loop, "white")

        attrs = {
            "label": f"\"{label}\"",
            "fillcolor": f"\"{fill}\"",
        }

        attr_str = ", ".join(f"{k}={v}" for k, v in attrs.items())
        out.append(f"  {node_name} [{attr_str}];")

    for bid, block in sorted(comp.blocks.items()):
        for succ in block.successors:
            if succ in comp.blocks:
                out.append(f"  b{bid} -> b{succ};")

    out.append("}")
    return "\n".join(out)

def write_dots_from_debug(debug_txt_path: str, dots_outdir: str) -> List[str]:
    os.makedirs(dots_outdir, exist_ok=True)
    text = Path(debug_txt_path).read_text(encoding="utf-8", errors="replace")
    comps = parse_debug_output(text)
    if not comps:
        raise SystemExit(f"No HumphreysDebugDataPhase sections found in: {debug_txt_path}")

    dot_paths: List[str] = []
    for idx, comp in enumerate(comps, start=1):
        dot_str = compilation_to_dot(comp, idx)
        base = sanitize_name(comp.name)
        dot_path = os.path.join(dots_outdir, f"{idx:03d}_{base}.dot")
        Path(dot_path).write_text(dot_str, encoding="utf-8")
        dot_paths.append(dot_path)

    return dot_paths

# ============================================================
# Part 2: DOT folder -> loops.csv (node, comp, method, loop_id)
# ============================================================

GRAPH_LABEL_RE = re.compile(r'^\s*label="([^"]+)";\s*$')
NODE_START_RE = re.compile(r'^\s*(b\d+)\s*\[\s*label="')
EDGE_RE = re.compile(r'^\s*(b\d+)\s*->\s*(b\d+)\s*;')
LOOP_LINE_RE = re.compile(r'Loop:\s*(<none>|L\d+)\b')
NODE_RE = re.compile(r"^b(?P<num>\d+)$")

def extract_label_value(node_chunk: str) -> str:
    key = 'label="'
    start = node_chunk.find(key)
    if start < 0:
        return ""
    p = start + len(key)

    out = []
    escaped = False
    while p < len(node_chunk):
        ch = node_chunk[p]
        if escaped:
            out.append(ch)
            escaped = False
        else:
            if ch == '\\':
                escaped = True
                out.append(ch)
            elif ch == '"':
                break
            else:
                out.append(ch)
        p += 1
    return "".join(out)

def parse_graph_label(label: str) -> Tuple[Optional[int], str]:
    comp_id = None
    method = label
    m = re.match(r'^\s*(\d+)\s*-\s*(.+?)\s*$', label)
    if m:
        comp_id = int(m.group(1))
        rest = m.group(2).strip()
        method = rest.split("(", 1)[0].strip()
    return comp_id, method

def parse_dot(path: str) -> Tuple[Optional[int], str, Dict[str, str]]:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines(True)

    graph_label: Optional[str] = None
    node_looplabel: Dict[str, str] = {}

    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n")

        m = GRAPH_LABEL_RE.match(line.strip())
        if m:
            graph_label = m.group(1)

        nm = NODE_START_RE.match(line)
        if nm:
            node_id = nm.group(1)

            chunk = line
            j = i + 1
            while j < len(lines) and "];" not in chunk:
                chunk += lines[j]
                j += 1

            label_text = extract_label_value(chunk)

            lm = LOOP_LINE_RE.search(label_text)
            if lm:
                v = lm.group(1)
                if v != "<none>":
                    node_looplabel[node_id] = v

            i = j
            continue

        i += 1

    comp_id, method = (None, "")
    if graph_label is not None:
        comp_id, method = parse_graph_label(graph_label)

    return comp_id, method, node_looplabel

def loop_label_to_loop_id(loop_label: str) -> Optional[int]:
    m = re.match(r"^L(\d+)$", loop_label)
    if not m:
        return None
    return int(m.group(1))

def write_loops_csv_from_dots(dots_dir: str, out_csv: str) -> int:
    dot_files: List[str] = []
    for root, _, files in os.walk(dots_dir):
        for fn in files:
            if fn.lower().endswith(".dot"):
                dot_files.append(os.path.join(root, fn))
    dot_files.sort()
    if not dot_files:
        raise SystemExit(f"No .dot files found in: {dots_dir}")

    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)

    rows = []
    for path in dot_files:
        comp_id, method, node_looplabel = parse_dot(path)
        if comp_id is None:
            continue

        method_norm = normalise_method_name(method)

        for node, lx in sorted(node_looplabel.items(), key=lambda kv: int(kv[0][1:])):
            loop_id = loop_label_to_loop_id(lx)
            if loop_id is None:
                continue
            rows.append({
                "comp_id": comp_id,
                "method": method_norm,
                "node": node,
                "loop_id": loop_id,
            })

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["comp_id", "method", "node", "loop_id"])
        w.writeheader()
        w.writerows(rows)

    return len(rows)

# ============================================================
# Part 3: slowdown blocks + loops.csv (+ mapping) -> totals csv
# ============================================================

LINE_RE = re.compile(
    r"^Method:\s*(?P<method>.*?),\s*"
    r"Block ID:\s*(?P<block>\d+),\s*"
    r"Normal Time:\s*(?P<normal>-?\d+(?:\.\d+)?),\s*"
    r"Slowdown Time:\s*(?P<slow>-?\d+(?:\.\d+)?),\s*"
    r"Percentage Increase:\s*(?P<pct>-?\d+(?:\.\d+)?)(?:%)?\s*$"
)

BRIDGE_KEY_RE = re.compile(r"^\s*(?P<graal>\d+)\s*\(Vtune Block\s*(?P<vtune>\d+)\)\s*$")

@dataclass(frozen=True)
class BlockRow:
    method_raw: str
    method_norm: str
    block_id: int
    normal_time: float
    slowdown_time: float

def normalise_method_name(s: str) -> str:
    return s.strip().replace("::", ".")

def read_slowdown_rows(path: str) -> Tuple[List[BlockRow], int, int]:
    block_rows: List[BlockRow] = []
    matched_blocks = 0
    total = 0

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            total += 1
            line = raw.strip()
            if not line:
                continue

            m = LINE_RE.match(line)
            if not m:
                continue

            method_raw = m.group("method").strip()
            method_norm = normalise_method_name(method_raw)
            block_rows.append(BlockRow(
                method_raw=method_raw,
                method_norm=method_norm,
                block_id=int(m.group("block")),
                normal_time=float(m.group("normal")),
                slowdown_time=float(m.group("slow")),
            ))
            matched_blocks += 1

    return block_rows, matched_blocks, total

def read_bridge_vtune_to_graal(path: str) -> Dict[str, Dict[int, int]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
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

def read_markerphase_graal_to_vtune(path: str) -> Dict[str, Dict[int, int]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: Dict[str, Dict[int, int]] = {}

    if not isinstance(data, dict):
        return out

    for method, arr in data.items():
        method_norm = normalise_method_name(method)
        graal_to_vtune: Dict[int, int] = {}

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
            graal_to_vtune[graal_id] = vtune_id

        out[method_norm] = graal_to_vtune

    return out

def invert_graal_to_vtune_to_vtune_to_graal(
    graal_to_vtune_by_method: Dict[str, Dict[int, int]]
) -> Dict[str, Dict[int, int]]:
    """
    Invert per method graal_id to vtune_id into vtune_id to graal_id.
    If multiple graal_ids map to the same vtune_id, keep the smallest graal_id.
    """
    out: Dict[str, Dict[int, int]] = {}
    for method_norm, g2v in graal_to_vtune_by_method.items():
        v2g: Dict[int, int] = {}
        for g, v in g2v.items():
            if v not in v2g or g < v2g[v]:
                v2g[v] = g
        out[method_norm] = v2g
    return out

def read_dot_node_map(path: str) -> Tuple[Dict[str, List[int]], Dict[Tuple[int, str, int], int]]:
    method_to_comps_set: Dict[str, set] = defaultdict(set)
    node_map: Dict[Tuple[int, str, int], int] = {}

    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        r = csv.DictReader(f)
        needed = {"comp_id", "method", "node", "loop_id"}
        if not needed.issubset(set(r.fieldnames or [])):
            raise ValueError(f"{path} must have columns: {sorted(needed)}")

        for row in r:
            comp_s = (row.get("comp_id") or "").strip()
            method_norm = (row.get("method") or "").strip()
            node = (row.get("node") or "").strip()
            loop_s = (row.get("loop_id") or "").strip()

            if not comp_s or not method_norm or not node or not loop_s:
                continue

            nm = NODE_RE.match(node)
            if not nm:
                continue

            comp_id = int(comp_s)
            block_id = int(nm.group("num"))
            loop_id = int(loop_s)

            node_map[(comp_id, method_norm, block_id)] = loop_id
            method_to_comps_set[method_norm].add(comp_id)

    method_to_comps_sorted: Dict[str, List[int]] = {m: sorted(cs) for m, cs in method_to_comps_set.items()}
    return method_to_comps_sorted, node_map

def safe_pct_increase(normal: float, slow: float) -> float:
    if normal <= 0.0:
        return 0.0
    return ((slow - normal) / normal) * 100.0

def choose_best_comp_per_method(
    blocks: List[BlockRow],
    method_to_comps: Dict[str, List[int]],
    node_map: Dict[Tuple[int, str, int], int],
    slowdown_block_id_is_vtune: bool,
    vtune_to_graal_by_method: Dict[str, Dict[int, int]],
    vtune_to_graal_fallback_by_method: Dict[str, Dict[int, int]],
    enable_method_fallback_match: bool,
) -> Dict[str, int]:
    """
    Pick one compilation id per method that best matches the slowdown rows.

    For each candidate comp id, score is the number of slowdown rows whose mapped graal block id exists in node_map.
    Tie break, prefer the largest comp id among tied best scores.
    """

    def method_alternatives(m: str) -> List[str]:
        if not enable_method_fallback_match:
            return [m]
        alt = m.replace(".", "::") if "." in m else m.replace("::", ".")
        alt_norm = normalise_method_name(alt)
        if alt_norm != m:
            return [m, alt_norm]
        return [m]

    method_to_graal_blocks: Dict[str, List[int]] = defaultdict(list)

    for br in blocks:
        method_norm = br.method_norm

        if slowdown_block_id_is_vtune:
            vtune_bid = br.block_id

            vtune_to_graal = vtune_to_graal_by_method.get(method_norm, {})
            graal_bid = vtune_to_graal.get(vtune_bid)

            if graal_bid is None:
                fallback = vtune_to_graal_fallback_by_method.get(method_norm, {})
                graal_bid = fallback.get(vtune_bid)

            if graal_bid is None:
                continue
        else:
            graal_bid = br.block_id

        method_to_graal_blocks[method_norm].append(graal_bid)

    best_comp_for_method: Dict[str, int] = {}

    for method_norm, graal_bids in method_to_graal_blocks.items():
        candidate_comps: Set[int] = set()
        for m in method_alternatives(method_norm):
            for cid in method_to_comps.get(m, []):
                candidate_comps.add(cid)

        if not candidate_comps:
            continue

        best_score = -1
        best_cid: Optional[int] = None

        for cid in sorted(candidate_comps):
            score = 0
            for m in method_alternatives(method_norm):
                for gb in graal_bids:
                    if (cid, m, gb) in node_map:
                        score += 1

            if score > best_score or (score == best_score and best_cid is not None and cid > best_cid):
                best_score = score
                best_cid = cid

        if best_cid is not None and best_score > 0:
            best_comp_for_method[method_norm] = best_cid

    return best_comp_for_method

def find_comp_for_method_block(
    method_norm: str,
    block_id: int,
    method_to_comps: Dict[str, List[int]],
    node_map: Dict[Tuple[int, str, int], int],
    enable_fallback: bool,
) -> Optional[int]:
    comps = method_to_comps.get(method_norm, [])
    for cid in reversed(comps):
        if (cid, method_norm, block_id) in node_map:
            return cid

    if not enable_fallback:
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

def build_totals_from_raw(
    slowdown_input_file: str,
    loops_csv: str,
    markerphase_json: Optional[str],
    output_loop_totals_csv: str,
    output_block_map_csv: str,
    slowdown_block_id_is_vtune: bool,
    bridge_json: Optional[str],
    enable_method_fallback_match: bool,
    min_normal_time_per_block: float,
) -> None:
    blocks, matched, total = read_slowdown_rows(slowdown_input_file)
    method_to_comps, node_map = read_dot_node_map(loops_csv)

    vtune_to_graal_by_method: Dict[str, Dict[int, int]] = {}
    vtune_to_graal_fallback_by_method: Dict[str, Dict[int, int]] = {}

    if slowdown_block_id_is_vtune:
        have_bridge = bool(bridge_json and os.path.isfile(bridge_json))
        have_marker = bool(markerphase_json and os.path.isfile(markerphase_json))

        if have_bridge:
            vtune_to_graal_by_method = read_bridge_vtune_to_graal(bridge_json)  # type: ignore[arg-type]
        else:
            vtune_to_graal_by_method = {}

        if have_marker:
            graal_to_vtune_by_method = read_markerphase_graal_to_vtune(markerphase_json)  # type: ignore[arg-type]
            vtune_to_graal_fallback_by_method = invert_graal_to_vtune_to_vtune_to_graal(graal_to_vtune_by_method)
        else:
            vtune_to_graal_fallback_by_method = {}

        if not have_bridge and not have_marker:
            raise SystemExit(
                "block id is vtune, requires bridge json or markerphase json to map vtune to graal"
            )

    best_comp_by_method = choose_best_comp_per_method(
        blocks=blocks,
        method_to_comps=method_to_comps,
        node_map=node_map,
        slowdown_block_id_is_vtune=slowdown_block_id_is_vtune,
        vtune_to_graal_by_method=vtune_to_graal_by_method,
        vtune_to_graal_fallback_by_method=vtune_to_graal_fallback_by_method,
        enable_method_fallback_match=enable_method_fallback_match,
    )

    grouped: Dict[Tuple[int, str, int], LoopAgg] = defaultdict(LoopAgg)
    block_rows: List[Tuple] = []

    missing_methodblock = 0
    missing_block = 0
    used = 0
    missing_mapping = 0
    skipped_normal = 0

    for br in blocks:
        if br.normal_time <= min_normal_time_per_block:
            skipped_normal += 1
            continue

        method_norm = br.method_norm

        vtune_block_id: Optional[int] = None
        graal_block_id: Optional[int] = None

        if slowdown_block_id_is_vtune:
            vtune_block_id = br.block_id

            graal_block = vtune_to_graal_by_method.get(method_norm, {}).get(vtune_block_id)
            if graal_block is None:
                graal_block = vtune_to_graal_fallback_by_method.get(method_norm, {}).get(vtune_block_id)

            if graal_block is None:
                missing_mapping += 1
                continue

            graal_block_id = graal_block
            block_id_for_node_map = graal_block_id
        else:
            graal_block_id = br.block_id
            block_id_for_node_map = graal_block_id

        comp_id = best_comp_by_method.get(method_norm)
        if comp_id is None:
            comp_id = find_comp_for_method_block(
                method_norm,
                block_id_for_node_map,
                method_to_comps,
                node_map,
                enable_method_fallback_match
            )
        if comp_id is None:
            missing_methodblock += 1
            continue

        loop_id = node_map.get((comp_id, method_norm, block_id_for_node_map))
        if loop_id is None:
            missing_block += 1
            continue

        g = grouped[(comp_id, method_norm, loop_id)]
        g.num_blocks += 1
        g.sum_normal += br.normal_time
        g.sum_slow += br.slowdown_time
        used += 1

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

    out_rows = []
    for (comp_id, method_norm, loop_id), g in grouped.items():
        pct = safe_pct_increase(g.sum_normal, g.sum_slow)
        out_rows.append((
            comp_id, method_norm, loop_id,
            g.num_blocks,
            g.sum_normal, g.sum_slow,
            pct
        ))

    out_rows.sort(key=lambda r: (r[0], r[1], r[2]))

    os.makedirs(os.path.dirname(output_loop_totals_csv) or ".", exist_ok=True)
    with open(output_loop_totals_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "comp_id", "method", "loop_id",
            "num_blocks_matched",
            "total_normal_time", "total_slowdown_time",
            "median_pct_slowdown"
        ])
        w.writerows(out_rows)

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

    os.makedirs(os.path.dirname(output_block_map_csv) or ".", exist_ok=True)
    with open(output_block_map_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "comp_id", "method", "loop_id",
            "vtune_block_id", "graal_block_id",
            "normal_time", "slowdown_time", "pct_increase_block",
            "loop_total_normal_time", "loop_total_slowdown_time", "pct_increase_loop_total",
            "block_share_of_loop_normal", "block_share_of_loop_slowdown"
        ])
        w.writerows(enriched_block_rows)

    print(f"[INFO] Read {total} lines, matched {matched} per block lines.")
    print(f"[INFO] Used {used} block to loop matches.")
    print(f"[INFO] Missing comp match: {missing_methodblock}")
    print(f"[INFO] Missing loop id:    {missing_block}")
    if slowdown_block_id_is_vtune:
        print(f"[INFO] Missing vtune to graal mapping: {missing_mapping}")
    print(f"[INFO] Skipped rows where normal_time <= {min_normal_time_per_block}: {skipped_normal}")

    print(f"[OK] Wrote loop totals: {output_loop_totals_csv}")
    print(f"[OK] Wrote block map:   {output_block_map_csv}")

# ============================================================
# Pipeline entrypoint
# ============================================================

def main() -> None:
    ap = argparse.ArgumentParser(
        description="All in one: debug output to DOTs, DOTs to loops.csv, slowdown blocks to per loop totals"
    )
    ap.add_argument("--debug-out", required=True, help="HumphreysDebugDataPhase console output file, for example baseline_cfg.out")
    ap.add_argument("--slowdown-txt", required=True, help="SlowdownTest output file containing normal and slowdown times per block")
    ap.add_argument("--bridge-json", default=None, help="Final json mapping vtune to graal, optional if markerphase json is provided")
    ap.add_argument("--block-id-is-vtune", action="store_true", help="Interpret Block ID in slowdown txt as vtune block id")
    ap.add_argument("--no-method-fallback", action="store_true", help="Disable method fallback matching between dot and double colon forms")
    ap.add_argument("--min-normal", type=float, default=0.0, help="Skip rows with normal_time <= this value")

    ap.add_argument(
        "--markerphase-json",
        default=None,
        help="MarkerPhaseInfo.json, used to build vtune to graal mapping when bridge json is missing"
    )

    ap.add_argument("--processed-dir", default="processed", help="Base processed output dir")
    args = ap.parse_args()

    processed_dir = args.processed_dir
    dots_dir = os.path.join(processed_dir, "cfg", "dots")
    loops_csv = os.path.join(processed_dir, "cfg", "loops.csv")

    out_loop_totals = os.path.join(processed_dir, "vtune", "total_pct_slowdown_per_loop.csv")
    out_block_map = os.path.join(processed_dir, "vtune", "block_times_per_loop.csv")

    print(f"[STEP] Writing DOT files to: {dots_dir}")
    dot_paths = write_dots_from_debug(args.debug_out, dots_dir)
    print(f"[OK] Wrote {len(dot_paths)} DOT files.")

    print(f"[STEP] Writing loops CSV to: {loops_csv}")
    nrows = write_loops_csv_from_dots(dots_dir, loops_csv)
    print(f"[OK] loops.csv rows: {nrows}")

    print("[STEP] Building per loop totals from slowdown blocks...")
    build_totals_from_raw(
        slowdown_input_file=args.slowdown_txt,
        loops_csv=loops_csv,
        markerphase_json=args.markerphase_json,
        output_loop_totals_csv=out_loop_totals,
        output_block_map_csv=out_block_map,
        slowdown_block_id_is_vtune=args.block_id_is_vtune,
        bridge_json=args.bridge_json,
        enable_method_fallback_match=(not args.no_method_fallback),
        min_normal_time_per_block=args.min_normal,
    )

if __name__ == "__main__":
    main()
