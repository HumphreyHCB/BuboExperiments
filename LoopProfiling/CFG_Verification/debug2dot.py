#!/usr/bin/env python3
import sys
import argparse
import re
import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional

@dataclass
class Block:
    bid: int
    successors: List[int] = field(default_factory=list)
    loop: Optional[str] = None   # e.g., "L0" or None
    sources: List[str] = field(default_factory=list)

@dataclass
class Compilation:
    name: str
    blocks: Dict[int, Block]

def parse_debug_output(text: str) -> List[Compilation]:
    """
    Parse HumphreysDebugDataPhase console output into a list of Compilation objects.
    """
    lines = text.splitlines()
    comps: List[Compilation] = []

    in_comp = False
    comp_name: Optional[str] = None
    blocks: Dict[int, Block] = {}
    current_block: Optional[Block] = None
    mode: Optional[str] = None  # "succ", "pred", "src", or None

    for raw in lines:
        line = raw.strip()

        if line.startswith("=== HumphreysDebugDataPhase ==="):
            # Start a new compilation section
            in_comp = True
            comp_name = None
            blocks = {}
            current_block = None
            mode = None
            continue

        if not in_comp:
            continue

        if line.startswith("=== End HumphreysDebugDataPhase ==="):
            # End of a compilation section
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
            # We don't actually need this for the graph, skip
            continue

        # Block header
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
            # We don't need predecessors for Graphviz edges, ignore entries
            mode = "pred"
            continue

        if line.startswith("In loop:"):
            if current_block is not None:
                val = line[len("In loop:"):].strip()
                if val != "<none>":
                    current_block.loop = val  # e.g. "L0"
                else:
                    current_block.loop = None
            continue

        if line.startswith("Source positions in block:"):
            mode = "src"
            continue

        # Content depending on mode
        if current_block is None:
            # Not inside a block; ignore random lines
            continue

        if mode == "succ":
            # Lines like "-> 1"
            if "->" in line:
                succ_str = line.split("->", 1)[1].strip()
                if succ_str:
                    try:
                        current_block.successors.append(int(succ_str))
                    except ValueError:
                        pass
            continue

        if mode == "src":
            # Lines listing source positions, e.g. "HotSpotMethod<...>"
            if line and line != "<none>":
                current_block.sources.append(line)
            continue

        # For mode "pred" or None we don't need to do anything more

    return comps

def gv_escape(s: str) -> str:
    """Escape a string for a Graphviz label."""
    s = s.replace("\\", "\\\\")
    s = s.replace("\"", "\\\"")
    s = s.replace("\n", "\\n")
    return s

def compilation_to_dot(comp: Compilation, comp_index: int) -> str:
    """
    Convert a single Compilation into a Graphviz DOT string.
    """
    out: List[str] = []

    graph_name = f"CFG_{comp_index}"
    out.append(f"digraph {graph_name} {{")
    out.append("  rankdir=LR;")
    out.append("  graph [fontsize=20, ranksep=1.5, nodesep=1.0, overlap=false, splines=true];")
    out.append("  node [shape=box, style=filled, fontname=\"Helvetica\", fontsize=10];")
    out.append(f"  label=\"{gv_escape(comp.name)}\";")
    out.append("  labelloc=top;")
    out.append("  labeljust=left;")

    # Palette for loops
    palette = [
        "lightblue", "lightgreen", "lightpink", "gold", "orange",
        "violet", "khaki", "plum", "lightcyan", "lightcoral"
    ]

    # Map loop IDs to colors for this compilation
    loop_ids = sorted({b.loop for b in comp.blocks.values() if b.loop is not None})
    loop_color = {loop: palette[i % len(palette)] for i, loop in enumerate(loop_ids)}

    # Nodes
    for bid, block in sorted(comp.blocks.items()):
        node_name = f"b{bid}"

        # Build label
        label_lines = [f"B{bid}"]
        label_lines.append(f"Loop: {block.loop if block.loop is not None else '<none>'}")
        if block.sources:
            label_lines.append("---")
            label_lines.extend(block.sources)

        label = gv_escape("\n".join(label_lines))

        # Color by loop
        if block.loop is None:
            fill = "white"
        else:
            fill = loop_color.get(block.loop, "white")

        out.append(f"  {node_name} [label=\"{label}\", fillcolor=\"{fill}\"];")

    # Edges
    for bid, block in sorted(comp.blocks.items()):
        src_name = f"b{bid}"
        for succ in block.successors:
            if succ in comp.blocks:
                dst_name = f"b{succ}"
                out.append(f"  {src_name} -> {dst_name};")

    out.append("}")
    return "\n".join(out)

def sanitize_name(name: str) -> str:
    """
    Turn a compilation name into a filesystem-friendly base name.
    """
    # Replace anything non alnum, dot, or underscore with underscore
    base = re.sub(r"[^0-9A-Za-z._]+", "_", name)
    # Trim to something reasonable
    if len(base) > 80:
        base = base[:80]
    if not base:
        base = "compilation"
    return base

def main():
    parser = argparse.ArgumentParser(
        description="Convert HumphreysDebugDataPhase console output to per-compilation Graphviz DOT files."
    )
    parser.add_argument(
        "input",
        help="Input file with HumphreysDebugDataPhase output."
    )
    parser.add_argument(
        "--outdir",
        default="CFG_Graphs",
        help="Output directory for DOT files (default: CFG_Graphs)."
    )
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        text = f.read()

    comps = parse_debug_output(text)
    if not comps:
        sys.stderr.write("No HumphreysDebugDataPhase sections found.\n")
        sys.exit(1)

    # Keep only Bounce-related compilations.
    # Remove this filter if you later want ALL compilations.
    # comps = [c for c in comps if "Bounce" in c.name]

    if not comps:
        sys.stderr.write("No compilations matching 'Bounce' found.\n")
        sys.exit(1)

    os.makedirs(args.outdir, exist_ok=True)

    for idx, comp in enumerate(comps, start=1):
        dot_str = compilation_to_dot(comp, idx)
        base = sanitize_name(comp.name)
        dot_path = os.path.join(args.outdir, f"{idx:03d}_{base}.dot")
        with open(dot_path, "w", encoding="utf-8") as f:
            f.write(dot_str)
        sys.stderr.write(f"Wrote {dot_path}\n")

if __name__ == "__main__":
    main()
