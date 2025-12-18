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
# Part 1: HumphreysDebugDataPhase output -> per-comp DOT files
# ============================================================

@dataclass
class Block:
    bid: int
    successors: List[int] = field(default_factory=list)
    loop: Optional[str] = None
    sources: List[str] = field(default_factory=list)

    bubo_lines: List[str] = field(default_factory=list)
    marker_classes: List[str] = field(default_factory=list)
    marker_loop_ids: Dict[str, int] = field(default_factory=dict)

    has_rdtsc: bool = False
    has_gt_marker: bool = False


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
    last_marker_class: Optional[str] = None

    for raw in lines:
        line = raw.strip()

        if line.startswith("=== HumphreysDebugDataPhase ==="):
            in_comp = True
            comp_name = None
            blocks = {}
            current_block = None
            mode = None
            last_marker_class = None
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
            last_marker_class = None
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
            last_marker_class = None
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

        if line.startswith("BuboLoopMakers:"):
            mode = "bubo"
            last_marker_class = None
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

        if mode == "bubo":
            if not line:
                continue

            current_block.bubo_lines.append(line)

            m = re.search(r"Found in this block\s*:\s*(?:class\s+)?(.+)$", line)
            if m:
                cls = m.group(1).strip()
                current_block.marker_classes.append(cls)
                last_marker_class = cls

                u = cls.upper()
                if "RDTSC" in u or "RDTSCP" in u or "RDTCP" in u:
                    current_block.has_rdtsc = True

                if "GT" in u or "SLOWDOWN" in u or "GTSLOW" in u:
                    current_block.has_gt_marker = True
                continue

            m = re.match(r"LoopID:\s*(\d+)", line)
            if m and last_marker_class is not None:
                current_block.marker_loop_ids[last_marker_class] = int(m.group(1))
                continue

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

        if block.marker_classes or block.bubo_lines:
            label_lines.append("---")
            label_lines.append("BuboLoopMakers:")
            if block.marker_classes:
                for cls in block.marker_classes:
                    short = cls.split(".")[-1]
                    if cls in block.marker_loop_ids:
                        label_lines.append(f"  {short} (LoopID={block.marker_loop_ids[cls]})")
                    else:
                        label_lines.append(f"  {short}")
            else:
                label_lines.extend([f"  {x}" for x in block.bubo_lines])

        label = gv_escape("\n".join(label_lines))
        fill = "white" if block.loop is None else loop_color.get(block.loop, "white")

        attrs = {
            "label": f"\"{label}\"",
            "fillcolor": f"\"{fill}\"",
        }

        if block.has_rdtsc:
            attrs["penwidth"] = "4"
            attrs["peripheries"] = "2"

        if block.has_gt_marker:
            attrs["color"] = "\"red\""
            attrs["penwidth"] = "4"
            attrs["peripheries"] = "2"

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
# Part 2: DOT folder -> loops.csv (node -> LoopID)
#          + NEW: probe_nodes.csv (probe-node -> LoopID)
# ============================================================

GRAPH_LABEL_RE = re.compile(r'^\s*label="([^"]+)";\s*$')
NODE_START_RE = re.compile(r'^\s*(b\d+)\s*\[\s*label="')
EDGE_RE = re.compile(r'^\s*(b\d+)\s*->\s*(b\d+)\s*;')
LOOP_LINE_RE = re.compile(r'Loop:\s*(<none>|L\d+)\b')
RDTSC_RE = re.compile(r'AMD64BuboRDTSCToSlot\s*\(LoopID=(\d+)\)')


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


def parse_dot(path: str) -> Tuple[Optional[int], str, Dict[str, str], Dict[str, int], List[Tuple[str, str]]]:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines(True)

    graph_label: Optional[str] = None
    node_looplabel: Dict[str, str] = {}
    node_rdtsc_loopid: Dict[str, int] = {}
    edges: List[Tuple[str, str]] = []

    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n")

        m = GRAPH_LABEL_RE.match(line.strip())
        if m:
            graph_label = m.group(1)

        em = EDGE_RE.match(line)
        if em:
            edges.append((em.group(1), em.group(2)))
            i += 1
            continue

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

            rm = RDTSC_RE.search(label_text)
            if rm:
                node_rdtsc_loopid[node_id] = int(rm.group(1))

            i = j
            continue

        i += 1

    comp_id, method = (None, "")
    if graph_label is not None:
        comp_id, method = parse_graph_label(graph_label)

    return comp_id, method, node_looplabel, node_rdtsc_loopid, edges


def infer_looplabel_to_loopid(
    node_looplabel: Dict[str, str],
    node_rdtsc_loopid: Dict[str, int],
    edges: List[Tuple[str, str]]
) -> Dict[str, int]:
    looplabel_to_id: Dict[str, int] = {}

    succs: Dict[str, List[str]] = {}
    for s, d in edges:
        succs.setdefault(s, []).append(d)

    for src, k in node_rdtsc_loopid.items():
        for dst in succs.get(src, []):
            lx = node_looplabel.get(dst)
            if lx is None:
                continue
            if lx not in looplabel_to_id:
                looplabel_to_id[lx] = k

    return looplabel_to_id


def infer_probe_nodes_for_loopid(
    node_looplabel: Dict[str, str],
    node_rdtsc_loopid: Dict[str, int],
    edges: List[Tuple[str, str]],
    looplabel_to_id: Dict[str, int],
) -> Dict[int, Set[str]]:
    """
    NEW:
    Determine which CFG nodes are "probe nodes" for each loop_id.

    Heuristic:
      - A probe node is a node that contains the RDTSC marker (node_rdtsc_loopid).
      - Assign it to the loop it *targets* (successor node with a loop label),
        using looplabel_to_id.
      - If we can't find a loop-labeled successor, fall back to the marker LoopID.
    """
    succs: Dict[str, List[str]] = {}
    for s, d in edges:
        succs.setdefault(s, []).append(d)

    out: Dict[int, Set[str]] = defaultdict(set)

    for src, marker_loopid in node_rdtsc_loopid.items():
        assigned: Optional[int] = None
        for dst in succs.get(src, []):
            lx = node_looplabel.get(dst)
            if lx is None:
                continue
            lid = looplabel_to_id.get(lx)
            if lid is not None:
                assigned = lid
                break

        if assigned is None:
            assigned = marker_loopid

        out[assigned].add(src)

    return out


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
        comp_id, method, node_looplabel, node_rdtsc_loopid, edges = parse_dot(path)
        looplabel_to_id = infer_looplabel_to_loopid(node_looplabel, node_rdtsc_loopid, edges)

        for node, lx in sorted(node_looplabel.items(), key=lambda kv: int(kv[0][1:])):
            if lx not in looplabel_to_id:
                continue
            rows.append({
                "comp_id": comp_id if comp_id is not None else "",
                "method": method,
                "node": node,
                "loop_id": looplabel_to_id[lx],
            })

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["comp_id", "method", "node", "loop_id"])
        w.writeheader()
        w.writerows(rows)

    return len(rows)


def write_probe_nodes_csv_from_dots(dots_dir: str, out_csv: str) -> int:
    """
    NEW:
    Writes a mapping of (comp_id, method, loop_id) -> probe_node (bNNN) and probe_graal_block_id (NNN).

    This is used later to add the *RDTSC segment time* for the probe blocks into each loop total.
    """
    dot_files: List[str] = []
    for root, _, files in os.walk(dots_dir):
        for fn in files:
            if fn.lower().endswith(".dot"):
                dot_files.append(os.path.join(root, fn))
    dot_files.sort()
    if not dot_files:
        raise SystemExit(f"No .dot files found in: {dots_dir}")

    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)

    NODE_RE_LOCAL = re.compile(r"^b(\d+)$")

    rows = []
    for path in dot_files:
        comp_id, method, node_looplabel, node_rdtsc_loopid, edges = parse_dot(path)
        if comp_id is None:
            continue

        looplabel_to_id = infer_looplabel_to_loopid(node_looplabel, node_rdtsc_loopid, edges)
        probe_nodes_by_loopid = infer_probe_nodes_for_loopid(
            node_looplabel=node_looplabel,
            node_rdtsc_loopid=node_rdtsc_loopid,
            edges=edges,
            looplabel_to_id=looplabel_to_id,
        )

        for loop_id, nodes in probe_nodes_by_loopid.items():
            for node in sorted(nodes, key=lambda n: int(n[1:])):
                m = NODE_RE_LOCAL.match(node)
                if not m:
                    continue
                graal_block_id = int(m.group(1))
                rows.append({
                    "comp_id": comp_id,
                    "method": method,
                    "loop_id": loop_id,
                    "probe_node": node,
                    "probe_graal_block_id": graal_block_id,
                })

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["comp_id", "method", "loop_id", "probe_node", "probe_graal_block_id"]
        )
        w.writeheader()
        w.writerows(rows)

    return len(rows)


# ============================================================
# Part 3: slowdown blocks + loops.csv (+ bridge) -> totals csv
#          + NEW: Add probe RDTSC segment times into loop totals
# ============================================================

LINE_RE = re.compile(
    r"^Method:\s*(?P<method>.*?),\s*"
    r"Block ID:\s*(?P<block>\d+),\s*"
    r"Normal Time:\s*(?P<normal>-?\d+(?:\.\d+)?),\s*"
    r"Slowdown Time:\s*(?P<slow>-?\d+(?:\.\d+)?),\s*"
    r"Percentage Increase:\s*(?P<pct>-?\d+(?:\.\d+)?)(?:%)?\s*$"
)

# NEW: probe-segment lines (same input file)
RDTSC_LINE_RE = re.compile(
    r"^Method:\s*(?P<method>.*?),\s*"
    r"Block ID:\s*(?P<block>\d+),\s*"
    r"RDTSC Normal Time:\s*(?P<normal>-?\d+(?:\.\d+)?),\s*"
    r"RDTSC Slowdown Time:\s*(?P<slow>-?\d+(?:\.\d+)?),\s*"
    r"RDTSC Percentage Increase:\s*(?P<pct>-?\d+(?:\.\d+)?)(?:%)?\s*$"
)

NODE_RE = re.compile(r"^b(?P<num>\d+)$")
BRIDGE_KEY_RE = re.compile(r"^\s*(?P<graal>\d+)\s*\(Vtune Block\s*(?P<vtune>\d+)\)\s*$")


@dataclass(frozen=True)
class BlockRow:
    method_raw: str
    method_norm: str
    block_id: int
    normal_time: float
    slowdown_time: float


@dataclass(frozen=True)
class RdtscRow:
    method_raw: str
    method_norm: str
    vtune_block_id: int
    rdtsc_normal: float
    rdtsc_slow: float


def normalise_method_name(s: str) -> str:
    return s.strip().replace("::", ".")


def read_slowdown_and_rdtsc_rows(path: str) -> Tuple[List[BlockRow], Dict[Tuple[str, int], RdtscRow], int, int, int]:
    """
    Reads ONE file (slowdown_blocks.txt) and extracts:
      - Normal per-block lines (BlockRow)  [existing behaviour]
      - RDTSC probe-segment lines (RdtscRow) keyed by (method_norm, vtune_block_id)  [NEW]

    Returns:
      (block_rows, rdtsc_map, matched_blocks, matched_rdtsc, total_lines)
    """
    block_rows: List[BlockRow] = []
    rdtsc_map: Dict[Tuple[str, int], RdtscRow] = {}

    matched_blocks = 0
    matched_rdtsc = 0
    total = 0

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            total += 1
            line = raw.strip()
            if not line:
                continue

            m = LINE_RE.match(line)
            if m:
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
                continue

            r = RDTSC_LINE_RE.match(line)
            if r:
                method_raw = r.group("method").strip()
                method_norm = normalise_method_name(method_raw)
                vtune_block_id = int(r.group("block"))
                rr = RdtscRow(
                    method_raw=method_raw,
                    method_norm=method_norm,
                    vtune_block_id=vtune_block_id,
                    rdtsc_normal=float(r.group("normal")),
                    rdtsc_slow=float(r.group("slow")),
                )
                # If duplicates exist, keep the last (they should be identical; last-wins is fine)
                rdtsc_map[(method_norm, vtune_block_id)] = rr
                matched_rdtsc += 1
                continue

    return block_rows, rdtsc_map, matched_blocks, matched_rdtsc, total


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
    """
    NEW:
    MarkerPhaseInfo.json gives entries like:
      { "VtuneBlock": "202", "GraalID": "79", ... }

    We invert it to: method_norm -> (graal_block_id -> vtune_block_id)
    """
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


def read_probe_nodes_csv(path: str) -> Dict[Tuple[int, str, int], Set[int]]:
    """
    NEW:
    Reads probe_nodes.csv and returns:
      (comp_id, method_norm, loop_id) -> set(graal_probe_block_ids)
    """
    out: Dict[Tuple[int, str, int], Set[int]] = defaultdict(set)

    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        r = csv.DictReader(f)
        needed = {"comp_id", "method", "loop_id", "probe_graal_block_id"}
        if not needed.issubset(set(r.fieldnames or [])):
            raise ValueError(f"{path} must have columns: {sorted(needed)}")

        for row in r:
            comp_s = (row.get("comp_id") or "").strip()
            method = (row.get("method") or "").strip()
            loop_s = (row.get("loop_id") or "").strip()
            graal_s = (row.get("probe_graal_block_id") or "").strip()

            if not comp_s or not method or not loop_s or not graal_s:
                continue

            try:
                comp_id = int(comp_s)
                loop_id = int(loop_s)
                graal_bid = int(graal_s)
            except ValueError:
                continue

            method_norm = normalise_method_name(method)
            out[(comp_id, method_norm, loop_id)].add(graal_bid)

    return out


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
    # NEW: probe segment sums
    probe_sum_normal: float = 0.0
    probe_sum_slow: float = 0.0


def safe_pct_increase(normal: float, slow: float) -> float:
    if normal <= 0.0:
        return 0.0
    return ((slow - normal) / normal) * 100.0


def build_totals_from_raw(
    slowdown_input_file: str,
    loops_csv: str,
    probe_nodes_csv: str,
    markerphase_json: str,
    output_loop_totals_csv: str,
    output_block_map_csv: str,
    slowdown_block_id_is_vtune: bool,
    bridge_json: Optional[str],
    enable_method_fallback_match: bool,
    min_normal_time_per_block: float,
) -> None:
    # Read ONE file for both normal lines and rdtsc lines
    blocks, rdtsc_map, matched, matched_rdtsc, total = read_slowdown_and_rdtsc_rows(slowdown_input_file)

    method_to_comps, node_map = read_dot_node_map(loops_csv)

    # Existing vtune->graal mapping (for normal block lines)
    vtune_to_graal_by_method: Dict[str, Dict[int, int]] = {}
    if slowdown_block_id_is_vtune:
        if not bridge_json:
            raise SystemExit("SLOWDOWN_BLOCK_ID_IS_VTUNE is true but no bridge JSON was provided.")
        vtune_to_graal_by_method = read_bridge_vtune_to_graal(bridge_json)

    # NEW: markerphase Graal->VTune mapping (for probe lookup)
    graal_to_vtune_by_method = read_markerphase_graal_to_vtune(markerphase_json)

    # NEW: probe nodes per loop (graal block ids)
    probe_blocks_by_loop = read_probe_nodes_csv(probe_nodes_csv)

    grouped: Dict[Tuple[int, str, int], LoopAgg] = defaultdict(LoopAgg)
    block_rows: List[Tuple] = []

    missing_methodblock = 0
    missing_block = 0
    used = 0
    missing_bridge = 0
    skipped_normal = 0

    # --------------------------
    # Base aggregation (UNCHANGED)
    # --------------------------
    for br in blocks:
        # if br.normal_time <= min_normal_time_per_block:
        #     skipped_normal += 1
        #     continue

        method_norm = br.method_norm

        vtune_block_id: Optional[int] = None
        graal_block_id: Optional[int] = None

        if slowdown_block_id_is_vtune:
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
            graal_block_id = br.block_id
            block_id_for_node_map = graal_block_id

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

    # --------------------------
    # NEW: add probe RDTSC segment times per loop
    # --------------------------
    probe_added_keys = 0
    probe_added_blocks = 0
    probe_missing_marker_map = 0
    probe_missing_rdtsc_line = 0

    for (comp_id, method_norm, loop_id), g in grouped.items():
        probe_graal_blocks = probe_blocks_by_loop.get((comp_id, method_norm, loop_id))
        if not probe_graal_blocks:
            continue

        graal_to_vtune = graal_to_vtune_by_method.get(method_norm)
        if not graal_to_vtune:
            probe_missing_marker_map += len(probe_graal_blocks)
            continue

        any_added = False

        for graal_bid in probe_graal_blocks:
            vtune_bid = graal_to_vtune.get(graal_bid)
            if vtune_bid is None:
                probe_missing_marker_map += 1
                continue

            rr = rdtsc_map.get((method_norm, vtune_bid))
            if rr is None:
                probe_missing_rdtsc_line += 1
                continue

            g.probe_sum_normal += rr.rdtsc_normal
            g.probe_sum_slow += rr.rdtsc_slow
            probe_added_blocks += 1
            any_added = True

        if any_added:
            probe_added_keys += 1

    # loop totals (now include probe sums)
    out_rows = []
    for (comp_id, method_norm, loop_id), g in grouped.items():
        total_n = g.sum_normal + g.probe_sum_normal
        total_s = g.sum_slow + g.probe_sum_slow
        pct = safe_pct_increase(total_n, total_s)

        out_rows.append((
            comp_id, method_norm, loop_id,
            g.num_blocks,
            total_n, total_s,
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

    # block map enriched (loop totals now include probe sums)
    loop_totals: Dict[Tuple[int, str, int], Tuple[float, float, float]] = {}
    for (comp_id, method_norm, loop_id), g in grouped.items():
        tn = g.sum_normal + g.probe_sum_normal
        ts = g.sum_slow + g.probe_sum_slow
        loop_totals[(comp_id, method_norm, loop_id)] = (tn, ts, safe_pct_increase(tn, ts))

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

    print(f"[INFO] Read {total} lines, matched {matched} per-block lines.")
    print(f"[INFO] Matched {matched_rdtsc} RDTSC probe-segment lines (from the same file).")
    print(f"[INFO] Used {used} block->loop matches.")
    print(f"[INFO] Missing method+block: {missing_methodblock}")
    print(f"[INFO] Missing blocks:      {missing_block}")
    if slowdown_block_id_is_vtune:
        print(f"[INFO] Bridge misses:      {missing_bridge}")
    print(f"[INFO] Skipped (normal_time <= {min_normal_time_per_block}): {skipped_normal}")

    print(f"[INFO] Probe augmentation: loops updated: {probe_added_keys}, probe blocks added: {probe_added_blocks}")
    print(f"[INFO] Probe augmentation: missing MarkerPhaseInfo map entries: {probe_missing_marker_map}")
    print(f"[INFO] Probe augmentation: missing RDTSC lines in slowdown txt: {probe_missing_rdtsc_line}")

    print(f"[OK] Wrote loop totals: {output_loop_totals_csv}")
    print(f"[OK] Wrote block map:   {output_block_map_csv}")


# ============================================================
# Pipeline entrypoint
# ============================================================

def main() -> None:
    ap = argparse.ArgumentParser(
        description="All-in-one: debug output -> DOTs -> loops.csv -> total_pct_slowdown_per_loop.csv (plus probe RDTSC segment augmentation)"
    )
    ap.add_argument("--debug-out", required=True, help="HumphreysDebugDataPhase console output file (baseline_withBubo.out).")
    ap.add_argument("--slowdown-txt", required=True, help="SlowdownTest output file (contains both normal block lines and RDTSC probe-segment lines).")
    ap.add_argument("--bridge-json", default=None, help="Final_*.json mapping (vtune->graal). Required if --block-id-is-vtune.")
    ap.add_argument("--block-id-is-vtune", action="store_true", help="Interpret Block ID in slowdown-txt normal lines as VTune block id.")
    ap.add_argument("--no-method-fallback", action="store_true", help="Disable :: <-> . method fallback matching.")
    ap.add_argument("--min-normal", type=float, default=0.0, help="Skip rows with normal_time <= this value (default: 0.0).")

    # NEW but defaulted, so your bash scripts do NOT need to change.
    ap.add_argument(
        "--markerphase-json",
        default="/home/hb478/repos/GTSlowdownSchedular/FinalBuboTests/LoopBenchmarks/MarkerPhaseInfo.json",
        help="MarkerPhaseInfo.json (used to map graal probe block ids -> vtune block ids for RDTSC lookup)."
    )

    ap.add_argument("--processed-dir", default="processed", help="Base processed output dir (default: processed).")
    args = ap.parse_args()

    processed_dir = args.processed_dir
    dots_dir = os.path.join(processed_dir, "cfg", "dots")
    loops_csv = os.path.join(processed_dir, "cfg", "loops.csv")

    # NEW: probe nodes output
    probe_nodes_csv = os.path.join(processed_dir, "cfg", "probe_nodes.csv")

    out_loop_totals = os.path.join(processed_dir, "vtune", "total_pct_slowdown_per_loop.csv")
    out_block_map = os.path.join(processed_dir, "vtune", "block_times_per_loop.csv")

    # 1) debug -> dots
    print(f"[STEP] Writing DOT files to: {dots_dir}")
    dot_paths = write_dots_from_debug(args.debug_out, dots_dir)
    print(f"[OK] Wrote {len(dot_paths)} DOT files.")

    # 2) dots -> loops.csv
    print(f"[STEP] Writing loops CSV to: {loops_csv}")
    nrows = write_loops_csv_from_dots(dots_dir, loops_csv)
    print(f"[OK] loops.csv rows: {nrows}")

    # 2.5) dots -> probe_nodes.csv (NEW)
    print(f"[STEP] Writing probe nodes CSV to: {probe_nodes_csv}")
    pn = write_probe_nodes_csv_from_dots(dots_dir, probe_nodes_csv)
    print(f"[OK] probe_nodes.csv rows: {pn}")

    # 3) slowdown + loops.csv (+ bridge) -> totals
    print(f"[STEP] Building per-loop totals from slowdown blocks...")
    build_totals_from_raw(
        slowdown_input_file=args.slowdown_txt,
        loops_csv=loops_csv,
        probe_nodes_csv=probe_nodes_csv,
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
