#!/usr/bin/env python3
import os
import csv
import re
import subprocess
from collections import defaultdict
from typing import Dict, Tuple, List, Optional

# ============================================================
# Config defaults (override via CLI flags)
# ============================================================

DEFAULT_BASE_DIR = "Data/AsyncJfrSlowdownRuns_WithFullSetDebug"
DEFAULT_OUT_DIR = "ParsedAsyncJfrCSVs"
DEFAULT_JFR_BIN = "jfr"

# ============================================================
# Marker decode (matches your provided approach)
# ============================================================

_marker_re = re.compile(r"BuboAgentCompilerMarkers\.Marker(\d+)")
_delim_re = re.compile(r"BuboAgentCompilerMarkers\.MarkerDelimiter")


def decode_comp_loop_from_stack(frames: List[str]) -> Optional[Tuple[int, int]]:
    """
    Given frame names (top-of-stack first), decode (comp_id, loop_id).

    IMPORTANT: markers BEFORE the delimiter encode the **loop_id**,
               markers AFTER the delimiter encode the **comp_id**.

    This is the same logic you provided. (Not the "tolerant" variant.)
    """
    pre_digits: List[int] = []   # loop id
    post_digits: List[int] = []  # comp id
    seen_delim = False

    for fn in frames:
        if _delim_re.search(fn):
            seen_delim = True
            continue

        m = _marker_re.search(fn)
        if not m:
            # Once we've started seeing markers, stop when we hit non-marker.
            if seen_delim or pre_digits:
                break
            else:
                continue

        digit = int(m.group(1))
        if not seen_delim:
            pre_digits.append(digit)
        else:
            post_digits.append(digit)

    if not pre_digits or not post_digits:
        return None

    loop_id = int("".join(str(d) for d in pre_digits))
    comp_id = int("".join(str(d) for d in post_digits))

    return comp_id, loop_id


# ============================================================
# Async GTAssignDebug tree parsing
# ============================================================

_ASYNC_TOTAL_RE = re.compile(r"\s*Total samples\s*:\s*(\d+)")
_ASYNC_BLOCK_RE = re.compile(r"^---\s+.*,\s*(\d+)\s+samples")
_ASYNC_FRAME_RE = re.compile(r"\s*\[\s*\d+\]\s+(.+)")


def parse_async_tree(path: str) -> Tuple[int, Dict[Tuple[int, int], int]]:
    """
    Parse Async GTAssignDebug "tree" output.

    Returns:
      total_samples_from_header,
      {(comp_id, loop_id) -> samples_accumulated_from_blocks_that_decode}
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    total_samples = 0
    for line in lines:
        m = _ASYNC_TOTAL_RE.match(line)
        if m:
            total_samples = int(m.group(1))
            break

    loop_samples: Dict[Tuple[int, int], int] = defaultdict(int)

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        m = _ASYNC_BLOCK_RE.match(line)
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
            fm = _ASYNC_FRAME_RE.match(l2)
            if fm:
                frames.append(fm.group(1).strip())
            i += 1

        key = decode_comp_loop_from_stack(frames)
        if key is not None:
            loop_samples[key] += block_samples

        # note: do not i += 1 here; we want to re-process next '---' if present

    return total_samples, dict(loop_samples)


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
        raise RuntimeError(f"jfr print failed for {jfr_path}:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.splitlines()


def parse_jfr_report(jfr_bin: str, jfr_path: str) -> Tuple[int, Dict[Tuple[int, int], int]]:
    """
    Treat each jdk.ExecutionSample stackTrace block as ONE sample (weight 1),
    and decode markers into (comp_id, loop_id).
    """
    if not os.path.isfile(jfr_path):
        raise FileNotFoundError(jfr_path)

    lines = jfr_print_exec_samples(jfr_bin, jfr_path)

    total_samples = 0
    loop_samples: Dict[Tuple[int, int], int] = defaultdict(int)

    in_stack = False
    current_stack: List[str] = []

    def flush_stack():
        nonlocal total_samples, current_stack
        if not current_stack:
            return
        key = decode_comp_loop_from_stack(current_stack)
        if key is not None:
            loop_samples[key] += 1
            total_samples += 1
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
    return total_samples, dict(loop_samples)


# ============================================================
# CSV writing helpers
# ============================================================

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def write_counts_csv(
    out_csv: str,
    tool: str,
    benchmark: str,
    run_type: str,  # "noSlow" or "slowdown"
    total: int,
    counts: Dict[Tuple[int, int], int],
) -> None:
    ensure_dir(os.path.dirname(out_csv) or ".")
    rows = []
    for (comp_id, loop_id), samples in sorted(counts.items()):
        rows.append({
            "tool": tool,
            "benchmark": benchmark,
            "run_type": run_type,
            "comp_id": comp_id,
            "loop_id": loop_id,
            "samples": samples,
            "total_samples": total,
            "runtime_share_pct": (samples / total * 100.0) if total > 0 else 0.0,
        })

    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "tool", "benchmark", "run_type",
            "comp_id", "loop_id",
            "samples", "total_samples", "runtime_share_pct",
        ])
        w.writeheader()
        w.writerows(rows)


def write_slowdown_csv(
    out_csv: str,
    tool: str,
    benchmark: str,
    base_total: int,
    base_counts: Dict[Tuple[int, int], int],
    slow_total: int,
    slow_counts: Dict[Tuple[int, int], int],
) -> None:
    ensure_dir(os.path.dirname(out_csv) or ".")
    keys = sorted(set(base_counts.keys()) | set(slow_counts.keys()))
    rows = []
    for comp_id, loop_id in keys:
        b = base_counts.get((comp_id, loop_id), 0)
        s = slow_counts.get((comp_id, loop_id), 0)
        if b == 0:
            # can't compute relative change; keep row but mark pct empty-ish
            slowdown_pct = ""
        else:
            slowdown_pct = (s - b) / b * 100.0

        rows.append({
            "tool": tool,
            "benchmark": benchmark,
            "comp_id": comp_id,
            "loop_id": loop_id,
            "baseline_samples": b,
            "slowdown_samples": s,
            "slowdown_pct": slowdown_pct,
            "runtime_share_pct_slow": (s / slow_total * 100.0) if slow_total > 0 else 0.0,
            "total_samples_baseline": base_total,
            "total_samples_slowdown": slow_total,
        })

    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "tool", "benchmark",
            "comp_id", "loop_id",
            "baseline_samples", "slowdown_samples", "slowdown_pct",
            "runtime_share_pct_slow",
            "total_samples_baseline", "total_samples_slowdown",
        ])
        w.writeheader()
        w.writerows(rows)


# ============================================================
# Discovery + main
# ============================================================

def find_benchmarks(base_dir: str) -> List[str]:
    if not os.path.isdir(base_dir):
        raise SystemExit(f"Base dir not found: {base_dir}")
    bms = []
    for d in sorted(os.listdir(base_dir)):
        p = os.path.join(base_dir, d)
        if os.path.isdir(p):
            bms.append(d)
    return bms


def parse_one_benchmark(base_dir: str, out_dir: str, jfr_bin: str, benchmark: str) -> None:
    bm_dir = os.path.join(base_dir, benchmark)

    # Expected names (matches your conventions)
    async_no = os.path.join(bm_dir, f"{benchmark}_noSlow_GTAssignDebug.txt")
    async_sl = os.path.join(bm_dir, f"{benchmark}_slowdown_GTAssignDebug.txt")
    jfr_no = os.path.join(bm_dir, f"{benchmark}_noSlow.jfr")
    jfr_sl = os.path.join(bm_dir, f"{benchmark}_slowdown.jfr")

    # --- Async ---
    async_no_total, async_no_counts = (0, {})
    async_sl_total, async_sl_counts = (0, {})
    if os.path.isfile(async_no):
        async_no_total, async_no_counts = parse_async_tree(async_no)
    if os.path.isfile(async_sl):
        async_sl_total, async_sl_counts = parse_async_tree(async_sl)

    if os.path.isfile(async_no):
        out_csv = os.path.join(out_dir, "async_counts", f"{benchmark}_noSlow_async.csv")
        write_counts_csv(out_csv, "async", benchmark, "noSlow", async_no_total, async_no_counts)
        print(f"[OK] {benchmark}: wrote {out_csv}")
    else:
        print(f"[WARN] {benchmark}: missing async noSlow file: {async_no}")

    if os.path.isfile(async_sl):
        out_csv = os.path.join(out_dir, "async_counts", f"{benchmark}_slowdown_async.csv")
        write_counts_csv(out_csv, "async", benchmark, "slowdown", async_sl_total, async_sl_counts)
        print(f"[OK] {benchmark}: wrote {out_csv}")
    else:
        print(f"[WARN] {benchmark}: missing async slowdown file: {async_sl}")

    if os.path.isfile(async_no) and os.path.isfile(async_sl):
        out_csv = os.path.join(out_dir, "async_slowdown", f"{benchmark}_async_slowdown.csv")
        write_slowdown_csv(out_csv, "async", benchmark, async_no_total, async_no_counts, async_sl_total, async_sl_counts)
        print(f"[OK] {benchmark}: wrote {out_csv}")

    # --- JFR ---
    jfr_no_total, jfr_no_counts = (0, {})
    jfr_sl_total, jfr_sl_counts = (0, {})
    if os.path.isfile(jfr_no):
        jfr_no_total, jfr_no_counts = parse_jfr_report(jfr_bin, jfr_no)
    if os.path.isfile(jfr_sl):
        jfr_sl_total, jfr_sl_counts = parse_jfr_report(jfr_bin, jfr_sl)

    if os.path.isfile(jfr_no):
        out_csv = os.path.join(out_dir, "jfr_counts", f"{benchmark}_noSlow_jfr.csv")
        write_counts_csv(out_csv, "jfr", benchmark, "noSlow", jfr_no_total, jfr_no_counts)
        print(f"[OK] {benchmark}: wrote {out_csv}")
    else:
        print(f"[WARN] {benchmark}: missing JFR noSlow file: {jfr_no}")

    if os.path.isfile(jfr_sl):
        out_csv = os.path.join(out_dir, "jfr_counts", f"{benchmark}_slowdown_jfr.csv")
        write_counts_csv(out_csv, "jfr", benchmark, "slowdown", jfr_sl_total, jfr_sl_counts)
        print(f"[OK] {benchmark}: wrote {out_csv}")
    else:
        print(f"[WARN] {benchmark}: missing JFR slowdown file: {jfr_sl}")

    if os.path.isfile(jfr_no) and os.path.isfile(jfr_sl):
        out_csv = os.path.join(out_dir, "jfr_slowdown", f"{benchmark}_jfr_slowdown.csv")
        write_slowdown_csv(out_csv, "jfr", benchmark, jfr_no_total, jfr_no_counts, jfr_sl_total, jfr_sl_counts)
        print(f"[OK] {benchmark}: wrote {out_csv}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Parse Async GTAssignDebug + JFR ExecutionSample into CSVs.")
    ap.add_argument("--base-dir", default=DEFAULT_BASE_DIR, help="Directory containing per-benchmark subfolders.")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Where to write CSVs.")
    ap.add_argument("--jfr-bin", default=DEFAULT_JFR_BIN, help="Path/name of the 'jfr' tool.")
    ap.add_argument("--benchmark", default=None, help="If set, only parse this benchmark folder name.")
    args = ap.parse_args()

    if args.benchmark:
        benchmarks = [args.benchmark]
    else:
        benchmarks = find_benchmarks(args.base_dir)

    print("[INFO] base-dir:", args.base_dir)
    print("[INFO] out-dir :", args.out_dir)
    print("[INFO] benchmarks:", ", ".join(benchmarks) if benchmarks else "<none>")

    for bm in benchmarks:
        parse_one_benchmark(args.base_dir, args.out_dir, args.jfr_bin, bm)


if __name__ == "__main__":
    main()
