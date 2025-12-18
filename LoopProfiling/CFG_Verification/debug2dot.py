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

    # Bubo/marker info
    bubo_lines: List[str] = field(default_factory=list)        # free-form lines under BuboLoopMakers
    marker_classes: List[str] = field(default_factory=list)     # extracted marker class names (fully qualified)
    marker_loop_ids: Dict[str, int] = field(default_factory=dict)  # marker class -> LoopID

    has_rdtsc: bool = False
    has_gt_marker: bool = False


@dataclass
class Compilation:
    name: str
    blocks: Dict[int, Block]


def parse_debug_output(text: str) -> List[Compilation]:
    """
    Parse HumphreysDebugDataPhase console output into a list of Compilation objects.
    Supports:
      - Block CFG (successors)
      - Source positions in block
      - BuboLoopMakers section per block:
          Found in this block : class ...
          LoopID: <n>  (applies to the immediately previous marker)
    """
    lines = text.splitlines()
    comps: List[Compilation] = []

    in_comp = False
    comp_name: Optional[str] = None
    blocks: Dict[int, Block] = {}
    current_block: Optional[Block] = None
    mode: Optional[str] = None  # "succ", "pred", "src", "bubo", or None

    # NEW: track the last marker class inside a block so LoopID can attach to it
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

        # Block header
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

        # Bubo marker section
        if line.startswith("BuboLoopMakers:"):
            mode = "bubo"
            last_marker_class = None
            continue

        # Content depending on mode
        if current_block is None:
            continue

        if mode == "succ":
            # Lines like "-> 5"
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
            # Typical lines:
            #   Found in this block : class jdk.graal.compiler.lir.amd64.Bubo.AMD64BuboRDTSCToSlot
            #   Found in this block : class jdk.graal.compiler.lir.amd64.Bubo.AMD64BuboWriteDeltaRDTSC
            #   LoopID: 0
            if not line:
                continue

            current_block.bubo_lines.append(line)

            # Marker class line
            m = re.search(r"Found in this block\s*:\s*(?:class\s+)?(.+)$", line)
            if m:
                cls = m.group(1).strip()
                current_block.marker_classes.append(cls)
                last_marker_class = cls

                # Highlight RDTSC/RDTSCP/RDTCP-ish markers
                u = cls.upper()
                if "RDTSC" in u or "RDTSCP" in u or "RDTCP" in u:
                    current_block.has_rdtsc = True

                # Best-effort "GT marker" detection (tweak to your exact class names if needed)
                if "GT" in u or "SLOWDOWN" in u or "GTSLOW" in u:
                    current_block.has_gt_marker = True
                continue

            # LoopID line (applies to the immediately previous marker)
            m = re.match(r"LoopID:\s*(\d+)", line)
            if m and last_marker_class is not None:
                current_block.marker_loop_ids[last_marker_class] = int(m.group(1))
                continue

            continue

        # mode == "pred" or None: ignore

    return comps


def gv_escape(s: str) -> str:
    s = s.replace("\\", "\\\\")
    s = s.replace("\"", "\\\"")
    s = s.replace("\n", "\\n")
    return s


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

        # Label
        label_lines = [f"B{bid}"]
        label_lines.append(f"Loop: {block.loop if block.loop is not None else '<none>'}")

        if block.sources:
            label_lines.append("---")
            label_lines.extend(block.sources)

        # Show marker info (below everything else)
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
                # Fallback: show raw bubo lines if we didn't parse classes
                label_lines.extend([f"  {x}" for x in block.bubo_lines])

        label = gv_escape("\n".join(label_lines))

        # Fill color by loop
        fill = "white" if block.loop is None else loop_color.get(block.loop, "white")

        # Styling for special blocks
        attrs = {
            "label": f"\"{label}\"",
            "fillcolor": f"\"{fill}\"",
        }

        # RDTSC/RDTSCP blocks: thick border + double border
        if block.has_rdtsc:
            attrs["penwidth"] = "4"
            attrs["peripheries"] = "2"  # double border

        # GT marker blocks: red border + double border
        if block.has_gt_marker:
            attrs["color"] = "\"red\""
            attrs["penwidth"] = "4"
            attrs["peripheries"] = "2"

        attr_str = ", ".join(f"{k}={v}" for k, v in attrs.items())
        out.append(f"  {node_name} [{attr_str}];")

    # Edges
    for bid, block in sorted(comp.blocks.items()):
        src_name = f"b{bid}"
        for succ in block.successors:
            if succ in comp.blocks:
                out.append(f"  {src_name} -> b{succ};")

    out.append("}")
    return "\n".join(out)


def sanitize_name(name: str) -> str:
    base = re.sub(r"[^0-9A-Za-z._]+", "_", name)
    if len(base) > 80:
        base = base[:80]
    if not base:
        base = "compilation"
    return base


def main():
    parser = argparse.ArgumentParser(
        description="Convert HumphreysDebugDataPhase console output to per-compilation Graphviz DOT files."
    )
    parser.add_argument("input", help="Input file with HumphreysDebugDataPhase output.")
    parser.add_argument("--outdir", default="CFG_Graphs",
                        help="Output directory for DOT files (default: CFG_Graphs).")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        text = f.read()

    comps = parse_debug_output(text)
    if not comps:
        sys.stderr.write("No HumphreysDebugDataPhase sections found.\n")
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
