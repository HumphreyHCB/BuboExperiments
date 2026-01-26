#!/usr/bin/env python3
import argparse
import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

# ============================================================
# Marker decoding (bottom up)
# ============================================================

MARKER_DIGIT_RE = re.compile(r"(?:^|[.$])Marker(?P<d>\d+)\s*(?:\(\s*\))?\s*$")
DELIM_RE = re.compile(r"(?:^|[.$])MarkerDelimiter\s*(?:\(\s*\))?\s*$")


# Async text format blocks
ASYNC_HEADER_RE = re.compile(
    r"^---\s+(?P<ns>\d+)\s+ns\s+\((?P<pct>[0-9.]+)%\),\s+(?P<samples>\d+)\s+samples\s*$"
)
ASYNC_FRAME_RE = re.compile(r"^\s*\[\s*\d+\]\s+(?P<frame>.+?)\s*$")

# JFR print format helpers, we only need stack lines
JFR_STACKTRACE_START_RE = re.compile(r"^\s*stackTrace\s*=\s*\[\s*$")
JFR_STACKTRACE_END_RE = re.compile(r"^\s*\]\s*,?\s*$")
JFR_EVENT_START_RE = re.compile(r"^\s*jdk\.[A-Za-z0-9_]+Sample\s*\{\s*$")
JFR_EVENT_END_RE = re.compile(r"^\s*\}\s*$")


@dataclass(frozen=True)
class LoopKey:
    method: str
    comp_id: Optional[int]
    loop_id: Optional[int]


def _is_marker_digit(frame: str) -> Optional[str]:
    m = MARKER_DIGIT_RE.search(frame.strip())
    if not m:
        return None
    return m.group("d")


def _is_delim(frame: str) -> bool:
    return bool(DELIM_RE.search(frame.strip()))


def decode_comp_and_loop_from_frames(frames_top_to_bottom: List[str]) -> Tuple[Optional[int], Optional[int], str]:
    """
    frames_top_to_bottom is the natural stack ordering:
      index 0 is the hottest frame, increasing index walks callers.

    Decode markers bottom up, meaning from the end of the stack towards the top.

    Rule:
      Traverse frames bottom up and collect Marker<digit> tokens and MarkerDelimiter.

      If MarkerDelimiter is present:
        digits encountered before delimiter (bottom up) form comp_id
        digits encountered after delimiter (bottom up) form loop_id

      If no delimiter:
        comp_id and loop_id are None

    Method selection:
      First non marker frame after the initial marker prefix (top down).
      If everything is markers, method becomes "<unknown>".
    """
    # Method, first non marker or delimiter after initial marker prefix (top down)
    method = "<unknown>"
    for fr in frames_top_to_bottom:
        fr_s = fr.strip()
        if _is_delim(fr_s):
            continue
        if _is_marker_digit(fr_s) is not None:
            continue
        method = fr_s
        break

    # Bottom up decode
    bottom_up = list(reversed(frames_top_to_bottom))

    seen_delim = False
    comp_digits: List[str] = []
    loop_digits: List[str] = []

    for fr in bottom_up:
        fr_s = fr.strip()
        if _is_delim(fr_s):
            seen_delim = True
            continue

        d = _is_marker_digit(fr_s)
        if d is None:
            # Stop once we’ve left the marker zone (markers should be contiguous near the top)
            if seen_delim or comp_digits or loop_digits:
                break
            continue


        if not seen_delim:
            comp_digits.append(d)
        else:
            loop_digits.append(d)

    if not seen_delim:
        return None, None, method

    comp_id = int("".join(comp_digits)) if comp_digits else None
    loop_id = int("".join(loop_digits)) if loop_digits else None
    return comp_id, loop_id, method


def parse_async_tree_txt(path: str) -> List[Tuple[LoopKey, int]]:
    """
    Parse async profiler "tree style" text blocks.

    Each block:
      --- <ns> ns (<pct>%), <samples> samples
        [ 0] frame
        [ 1] frame
        ...

    Returns list of (LoopKey, samples) entries, one per block.
    """
    entries: List[Tuple[LoopKey, int]] = []
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n")
        mh = ASYNC_HEADER_RE.match(line.strip())
        if not mh:
            i += 1
            continue

        samples = int(mh.group("samples"))
        i += 1

        frames: List[str] = []
        while i < len(lines):
            lf = lines[i].rstrip("\n")
            if lf.startswith("--- "):
                break
            mf = ASYNC_FRAME_RE.match(lf)
            if mf:
                frames.append(mf.group("frame").strip())
            else:
                # tolerate blank or unknown lines inside a block
                pass
            i += 1

        comp_id, loop_id, method = decode_comp_and_loop_from_frames(frames)
        key = LoopKey(method=method, comp_id=comp_id, loop_id=loop_id)
        entries.append((key, samples))

    return entries


def parse_jfr_print_txt(path: str) -> List[Tuple[LoopKey, int]]:
    """
    Parse a text dump from `jfr print` style output.

    We look for stackTrace blocks:
      stackTrace = [
        frame
        frame
      ]

    Each event counts as 1 sample by default.
    """
    entries: List[Tuple[LoopKey, int]] = []
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()

    in_event = False
    in_stack = False
    frames: List[str] = []

    for raw in lines:
        line = raw.rstrip("\n")

        if JFR_EVENT_START_RE.match(line):
            in_event = True
            in_stack = False
            frames = []
            continue

        if not in_event:
            continue

        if JFR_STACKTRACE_START_RE.match(line):
            in_stack = True
            frames = []
            continue

        if in_stack:
            if JFR_STACKTRACE_END_RE.match(line):
                in_stack = False

                comp_id, loop_id, method = decode_comp_and_loop_from_frames(frames)
                key = LoopKey(method=method, comp_id=comp_id, loop_id=loop_id)
                entries.append((key, 1))
                continue

            fr = line.strip().rstrip(",")
            if fr:
                frames.append(fr)
            continue

        if JFR_EVENT_END_RE.match(line):
            in_event = False
            in_stack = False
            frames = []
            continue

    return entries


def aggregate(entries: List[Tuple[LoopKey, int]]) -> Tuple[Dict[LoopKey, int], int]:
    agg: Dict[LoopKey, int] = defaultdict(int)
    total = 0
    for k, n in entries:
        agg[k] += n
        total += n
    return agg, total


def write_loops_profile_csv(
    out_csv: str,
    profiler: str,
    benchmark: str,
    suite: str,
    mode: str,
    tag: str,
    agg: Dict[LoopKey, int],
    total_samples: int,
) -> None:
    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)

    rows = []
    for k, samples in agg.items():
        runtime_share_pct = (samples / total_samples * 100.0) if total_samples > 0 else 0.0

        notes = ""
        if k.comp_id is None or k.loop_id is None:
            notes = "no_marker"

        rows.append({
            "benchmark": benchmark,
            "suite": suite,
            "mode": mode,
            "tag": tag,
            "profiler": profiler,
            "method": k.method,
            "comp_id": "" if k.comp_id is None else k.comp_id,
            "loop_id": "" if k.loop_id is None else k.loop_id,
            "samples": samples,
            "total_samples": total_samples,
            "runtime_share_pct": f"{runtime_share_pct:.6f}",
            "notes": notes,
        })

    rows.sort(
        key=lambda r: (
            -(float(r["runtime_share_pct"])),
            str(r["method"]),
            int(r["comp_id"]) if str(r["comp_id"]).isdigit() else 10**9,
            int(r["loop_id"]) if str(r["loop_id"]).isdigit() else 10**9,
        )
    )

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "benchmark", "suite", "mode", "tag", "profiler",
                "method", "comp_id", "loop_id",
                "samples", "total_samples", "runtime_share_pct",
                "notes",
            ],
        )
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert async tree text and JFR print text into per loop CSV files using bottom up marker decoding."
    )
    ap.add_argument("--benchmark", required=True)
    ap.add_argument("--suite", required=True)
    ap.add_argument("--mode", required=True)
    ap.add_argument("--tag", required=True)

    ap.add_argument("--async-txt", default=None, help="Path to async profiler tree text output.")
    ap.add_argument("--jfr-txt", default=None, help="Path to jfr print text output.")

    ap.add_argument("--processed-dir", required=True, help="Processed base dir, for example processed/AWFY/Mandelbrot/WithoutProbe")

    args = ap.parse_args()

    if not args.async_txt and not args.jfr_txt:
        raise SystemExit("Provide at least one of --async-txt or --jfr-txt")

    if args.async_txt:
        async_entries = parse_async_tree_txt(args.async_txt)
        async_agg, async_total = aggregate(async_entries)
        out_csv = os.path.join(args.processed_dir, "async", "loops_profile.csv")
        write_loops_profile_csv(
            out_csv=out_csv,
            profiler="async",
            benchmark=args.benchmark,
            suite=args.suite,
            mode=args.mode,
            tag=args.tag,
            agg=async_agg,
            total_samples=async_total,
        )
        print(f"[OK] Wrote async loops CSV: {out_csv} (total_samples={async_total})")

    if args.jfr_txt:
        jfr_entries = parse_jfr_print_txt(args.jfr_txt)
        jfr_agg, jfr_total = aggregate(jfr_entries)
        out_csv = os.path.join(args.processed_dir, "jfr", "loops_profile.csv")
        write_loops_profile_csv(
            out_csv=out_csv,
            profiler="jfr",
            benchmark=args.benchmark,
            suite=args.suite,
            mode=args.mode,
            tag=args.tag,
            agg=jfr_agg,
            total_samples=jfr_total,
        )
        print(f"[OK] Wrote jfr loops CSV: {out_csv} (total_samples={jfr_total})")


if __name__ == "__main__":
    main()
