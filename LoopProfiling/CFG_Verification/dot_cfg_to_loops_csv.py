#!/usr/bin/env python3
import argparse
import csv
import os
import re
from typing import Dict, List, Optional, Tuple


# ----------------------------
# Regexes
# ----------------------------

GRAPH_LABEL_RE = re.compile(r'^\s*label="([^"]+)";\s*$')
NODE_START_RE = re.compile(r'^\s*(b\d+)\s*\[\s*label="')
EDGE_RE = re.compile(r'^\s*(b\d+)\s*->\s*(b\d+)\s*;')

# Inside label text
LOOP_LINE_RE = re.compile(r'Loop:\s*(<none>|L\d+)\b')
RDTSC_RE = re.compile(r'AMD64BuboRDTSCToSlot\s*\(LoopID=(\d+)\)')


# ----------------------------
# Parsing helpers
# ----------------------------

def extract_label_value(node_chunk: str) -> str:
    """
    Extract label="...".
    Keeps backslash escapes like \n as-is (we don't need to unescape for our regexes).
    """
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
    """
    Example:
      "214-LoopBenchmarks.editDistanceLoop(char[], char[])"
    -> (214, "LoopBenchmarks.editDistanceLoop")
    """
    comp_id = None
    method = label

    m = re.match(r'^\s*(\d+)\s*-\s*(.+?)\s*$', label)
    if m:
        comp_id = int(m.group(1))
        rest = m.group(2).strip()
        method = rest.split("(", 1)[0].strip()
    return comp_id, method


def parse_dot(path: str) -> Tuple[Optional[int], str, Dict[str, str], Dict[str, int], List[Tuple[str, str]]]:
    """
    Returns:
      comp_id, method,
      node_looplabel: node_id -> "Lx" (only if not <none>)
      node_rdtsc_loopid: node_id -> LoopID
      edges: list of (src, dst)
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

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

            # collect node chunk until '];'
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


def infer_looplabel_to_loopid(node_looplabel: Dict[str, str],
                              node_rdtsc_loopid: Dict[str, int],
                              edges: List[Tuple[str, str]]) -> Dict[str, int]:
    """
    Your rule:
      if src node has RDTSCToSlot(LoopID=k) and edge src->dst,
      and dst is in Loop: Lx,
      then Lx corresponds to LoopID=k
    """
    looplabel_to_id: Dict[str, int] = {}

    # Build adjacency for quick lookup
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


def write_min_csv(out_path: str, rows: List[Dict[str, object]]) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["comp_id", "method", "node", "loop_id"])
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> None:
    ap = argparse.ArgumentParser(description="Emit node->Bubo LoopID mapping from Graal CFG .dot files.")
    ap.add_argument("input", help="A .dot file OR a directory containing .dot files")
    ap.add_argument("-o", "--out", default="node_loopid.csv", help="Output CSV (default: node_loopid.csv)")
    args = ap.parse_args()

    dot_files: List[str] = []
    if os.path.isdir(args.input):
        for root, _, files in os.walk(args.input):
            for fn in files:
                if fn.lower().endswith(".dot"):
                    dot_files.append(os.path.join(root, fn))
        dot_files.sort()
    else:
        dot_files = [args.input]

    out_rows: List[Dict[str, object]] = []

    for path in dot_files:
        comp_id, method, node_looplabel, node_rdtsc_loopid, edges = parse_dot(path)
        looplabel_to_id = infer_looplabel_to_loopid(node_looplabel, node_rdtsc_loopid, edges)

        # Emit only nodes that are inside a loop AND that loop has a mapped LoopID
        for node, lx in sorted(node_looplabel.items(), key=lambda kv: int(kv[0][1:])):  # sort by b#
            if lx not in looplabel_to_id:
                continue
            out_rows.append({
                "comp_id": comp_id if comp_id is not None else "",
                "method": method,
                "node": node,
                "loop_id": looplabel_to_id[lx],
            })

    write_min_csv(args.out, out_rows)


if __name__ == "__main__":
    main()
