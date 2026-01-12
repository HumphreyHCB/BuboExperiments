#!/usr/bin/env python3
import argparse
import csv
import os
import re
import subprocess
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# ============================================================
# Regexes
# ============================================================

# Markers in frames like:
#   my.custom.BuboAgentCompilerMarkers.Marker2
#   my.custom.BuboAgentCompilerMarkers.MarkerDelimiter
MARKER_NUM_RE = re.compile(r"\bBuboAgentCompilerMarkers\.Marker(\d+)\b")
DELIM_RE = re.compile(r"\bBuboAgentCompilerMarkers\.MarkerDelimiter\b")

# Async GTAssignDebug tree format:
#   Total samples : 8490023
#   --- ... , 7903524 samples
#     [ 0] frame
ASYNC_TOTAL_RE = re.compile(r"\s*Total samples\s*:\s*(\d+)")
ASYNC_BLOCK_RE = re.compile(r"^---\s+.*,\s*(\d+)\s+samples")
ASYNC_FRAME_RE = re.compile(r"\s*\[\s*\d+\]\s+(.+)")


# ============================================================
# Marker decoding to (compunit, loop)
# ============================================================

def extract_comp_loop_from_frames(frames_top_first: List[str]) -> Tuple[Optional[int], Optional[int], str]:
    """
    CONFIRMED encoding from your example:

      loop_id      = Marker<N> immediately BEFORE MarkerDelimiter (closest to top)
      compunit_id  = sequence of Marker<N> AFTER MarkerDelimiter, read BOTTOM->TOP, concatenated

    Example:
      [0] Marker1
      [1] MarkerDelimiter
      [2] Marker2
      [3] Marker2
      [4] Marker1
      => loop = 1
      => after delimiter (top->bottom) = [2,2,1]
      => bottom->top = [1,2,2]
      => compunit = 122
    """
    delim_index: Optional[int] = None
    markers: List[Tuple[int, int]] = []  # (frame_index, marker_num) in top->bottom order

    for idx, fr in enumerate(frames_top_first):
        s = fr.strip().rstrip(",")
        if not s:
            continue
        if s.startswith("-at "):
            s = s[4:].strip()

        if delim_index is None and DELIM_RE.search(s):
            delim_index = idx

        m = MARKER_NUM_RE.search(s)
        if m:
            markers.append((idx, int(m.group(1))))

    if not markers:
        return None, None, "no-markers"

    # No delimiter: fallback:
    # loop = first marker
    # comp = remaining markers bottom->top concatenated (if any)
    if delim_index is None:
        loop = markers[0][1]
        tail = [num for (_i, num) in markers[1:]]
        if not tail:
            return None, loop, "no-delim-loop-only"
        comp = int("".join(str(d) for d in reversed(tail)))
        return comp, loop, "no-delim-fallback"

    # loop = first marker that occurs before delimiter (closest to top)
    loop: Optional[int] = None
    for idx, num in markers:
        if idx < delim_index:
            loop = num
            break

    # comp = markers after delimiter, bottom->top
    after = [num for (idx, num) in markers if idx > delim_index]
    if not after:
        return None, loop, "delim-no-comp"

    comp = int("".join(str(d) for d in reversed(after)))
    return comp, loop, "delim-bottom-up"


# ============================================================
# Async parsing (marker-based -> comp/loop)
# ============================================================

def parse_async_tree(path: str) -> Tuple[int, Dict[Tuple[Optional[int], Optional[int]], int]]:
    """
    Returns:
      total_samples_from_header (0 if missing),
      {(compunit, loop) -> samples}
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    header_total = 0
    for line in lines:
        m = ASYNC_TOTAL_RE.match(line)
        if m:
            header_total = int(m.group(1))
            break

    counts: Dict[Tuple[Optional[int], Optional[int]], int] = defaultdict(int)

    i, n = 0, len(lines)
    while i < n:
        m = ASYNC_BLOCK_RE.match(lines[i])
        if not m:
            i += 1
            continue

        block_samples = int(m.group(1))
        i += 1

        frames: List[str] = []
        while i < n:
            l2 = lines[i]
            if l2.strip() == "" or l2.startswith("---"):
                break
            fm = ASYNC_FRAME_RE.match(l2)
            if fm:
                frames.append(fm.group(1).strip())
            i += 1

        comp, loop, _why = extract_comp_loop_from_frames(frames)
        counts[(comp, loop)] += block_samples

    return header_total, dict(counts)


# ============================================================
# JFR parsing via `jfr print --events jdk.ExecutionSample`
# ============================================================

def jfr_print_exec_samples(jfr_bin: str, jfr_path: str) -> List[str]:
    proc = subprocess.run(
        [jfr_bin, "print", "--events", "jdk.ExecutionSample", jfr_path],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"jfr print failed for {jfr_path}:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc.stdout.splitlines()


def parse_jfr(jfr_bin: str, jfr_path: str) -> Tuple[int, Dict[Tuple[Optional[int], Optional[int]], int]]:
    """
    Each jdk.ExecutionSample counts as 1 sample.
    Returns:
      total_samples, {(compunit, loop) -> samples}
    """
    lines = jfr_print_exec_samples(jfr_bin, jfr_path)

    total = 0
    counts: Dict[Tuple[Optional[int], Optional[int]], int] = defaultdict(int)

    in_stack = False
    current_stack: List[str] = []

    def flush_stack():
        nonlocal total, current_stack
        if not current_stack:
            return
        comp, loop, _why = extract_comp_loop_from_frames(current_stack)
        counts[(comp, loop)] += 1
        total += 1
        current_stack = []

    for line in lines:
        s = line.strip()

        if s.startswith("jdk.ExecutionSample {"):
            flush_stack()
            in_stack = False
            continue

        if "stackTrace = [" in s:
            in_stack = True
            current_stack = []
            continue

        if in_stack:
            if s.startswith("]"):
                flush_stack()
                in_stack = False
                continue
            if s:
                current_stack.append(s.rstrip(","))
            continue

    flush_stack()
    return total, dict(counts)


# ============================================================
# CSV output (tool-separated rows)
# ============================================================

def write_csv(
    out_csv: str,
    tool_totals_header: Dict[str, int],
    tool_totals_used: Dict[str, int],
    tool_counts: Dict[Tuple[str, Optional[int], Optional[int]], int],
) -> None:
    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)

    rows = []
    for (tool, comp, loop), samples in sorted(tool_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        total_used = tool_totals_used.get(tool, 0)
        total_hdr = tool_totals_header.get(tool, 0)

        comp_s = "" if comp is None else str(comp)
        loop_s = "" if loop is None else str(loop)

        key = "NO_MARKERS" if (comp is None and loop is None) else f"C{comp_s}_L{loop_s}"

        rows.append({
            "tool": tool,  # <-- always 'async' or 'jfr'
            "comp_unit_id": comp_s,
            "loop_id": loop_s,
            "comp_loop_key": key,
            "samples": samples,
            "total_samples_tool_used": total_used,
            "total_samples_tool_header": total_hdr,
            "runtime_share_pct_tool": (samples / total_used * 100.0) if total_used > 0 else 0.0,
        })

    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "tool",
                "comp_unit_id",
                "loop_id",
                "comp_loop_key",
                "samples",
                "total_samples_tool_used",
                "total_samples_tool_header",
                "runtime_share_pct_tool",
            ],
        )
        w.writeheader()
        w.writerows(rows)


# ============================================================
# Main
# ============================================================

def main():
    ap = argparse.ArgumentParser(
        description="Parse Async GTAssignDebug and/or JFR ExecutionSample into (comp_unit_id, loop_id) keys using BuboAgentCompilerMarkers + MarkerDelimiter. One CSV, but rows are separated by tool."
    )
    ap.add_argument("--async", dest="async_path", default=None, help="Path to async tree txt.")
    ap.add_argument("--jfr", dest="jfr_path", default=None, help="Path to .jfr.")
    ap.add_argument("--jfr-bin", default="jfr", help="Path/name of 'jfr' tool.")
    ap.add_argument("--out-csv", required=True, help="Output CSV path.")
    args = ap.parse_args()

    if not args.async_path and not args.jfr_path:
        raise SystemExit("Provide at least one of --async or --jfr")

    tool_totals_header: Dict[str, int] = {}
    tool_totals_used: Dict[str, int] = {}
    tool_counts: Dict[Tuple[str, Optional[int], Optional[int]], int] = defaultdict(int)

    if args.async_path:
        if not os.path.isfile(args.async_path):
            raise SystemExit(f"Async file not found: {args.async_path}")
        header_total, counts = parse_async_tree(args.async_path)
        used_total = header_total if header_total > 0 else sum(counts.values())

        tool_totals_header["async"] = header_total
        tool_totals_used["async"] = used_total

        for (comp, loop), v in counts.items():
            tool_counts[("async", comp, loop)] += v

    if args.jfr_path:
        if not os.path.isfile(args.jfr_path):
            raise SystemExit(f"JFR file not found: {args.jfr_path}")
        used_total, counts = parse_jfr(args.jfr_bin, args.jfr_path)

        tool_totals_header["jfr"] = used_total  # jfr has no separate header total in our parse
        tool_totals_used["jfr"] = used_total

        for (comp, loop), v in counts.items():
            tool_counts[("jfr", comp, loop)] += v

    write_csv(args.out_csv, tool_totals_header, tool_totals_used, dict(tool_counts))
    print(f"[OK] wrote {args.out_csv}")
    print(f"[OK] totals_used: {tool_totals_used}  totals_header: {tool_totals_header}")
    print(f"[OK] rows: {len(tool_counts)}")


if __name__ == "__main__":
    main()
