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

DEFAULT_BASE_DIR = "Data/LoopBenchmarks_AsyncJfrSlowdownRuns_AllDebug"
DEFAULT_OUT_DIR = "LoopBenchmarks_AsyncJfrSlowdownRuns_AllDebug"
DEFAULT_JFR_BIN = "jfr"

# ============================================================
# Method extraction (NO marker dependency)
# ============================================================

# Frames we explicitly ignore (markers etc.)
_IGNORE_FRAME_PREFIXES = (
    "BuboAgentCompilerMarkers.",
)

_IGNORE_FRAME_CONTAINS = (
    "BuboAgentCompilerMarkers.",
    "MarkerDelimiter",
)

# Try to match something that looks like "pkg.Class.method(...)" or "pkg.Class.method"
# We'll canonicalize to "pkg.Class::method"
_METHOD_RE = re.compile(
    r"""(?x)
    ^\s*
    (?P<owner>[A-Za-z0-9_$]+(?:\.[A-Za-z0-9_$]+)*)   # Class or pkg.Class or pkg.deep.Class$Inner
    \.
    (?P<name>[A-Za-z0-9_$<>]+)                       # method name
    (?:\(|$)                                         # optional signature start, or end
    """
)


def frame_to_method_key(frame: str) -> Optional[str]:
    """
    Convert a single frame string to canonical method key: Owner::name.
    Examples:
      - "LoopBenchmarks.fibonacciLoop(int)" -> "LoopBenchmarks::fibonacciLoop"
      - "java.util.HashMap.putVal(HashMap.java:648)" -> "java.util.HashMap::putVal"
      - "java.util.HashMap.putVal" -> "java.util.HashMap::putVal"
    """
    if not frame:
        return None

    s = frame.strip().rstrip(",")
    if not s:
        return None

    # JFR frames often look like "SomeClass.someMethod(...)" but can include trailing " [bci: ..]" etc.
    # Strip common trailing clutter:
    #   "-at X.y(z) [bci: 12]"  -> "X.y(z)"
    if s.startswith("-at "):
        s = s[4:].strip()

    # Ignore markers / delimiter frames
    for p in _IGNORE_FRAME_PREFIXES:
        if s.startswith(p):
            return None
    for c in _IGNORE_FRAME_CONTAINS:
        if c in s:
            return None

    m = _METHOD_RE.match(s)
    if not m:
        return None

    owner = m.group("owner")
    name = m.group("name")
    if not owner or not name:
        return None

    return f"{owner}::{name}"


def pick_method_from_stack(frames_top_first: List[str]) -> Optional[str]:
    """
    Given frames (top-of-stack first), pick the first *non-marker* frame
    that can be converted to a method key.
    """
    for fr in frames_top_first:
        mk = frame_to_method_key(fr)
        if mk:
            return mk
    return None


# ============================================================
# Async GTAssignDebug tree parsing (method-based)
# ============================================================

_ASYNC_TOTAL_RE = re.compile(r"\s*Total samples\s*:\s*(\d+)")
_ASYNC_BLOCK_RE = re.compile(r"^---\s+.*,\s*(\d+)\s+samples")
_ASYNC_FRAME_RE = re.compile(r"\s*\[\s*\d+\]\s+(.+)")

def parse_async_tree(path: str) -> Tuple[int, Dict[str, int]]:
    """
    Parse Async GTAssignDebug "tree" output.

    Returns:
      total_samples_from_header,
      { method_key -> samples_accumulated_from_blocks_that_have_a_method }
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    total_samples = 0
    for line in lines:
        m = _ASYNC_TOTAL_RE.match(line)
        if m:
            total_samples = int(m.group(1))
            break

    method_samples: Dict[str, int] = defaultdict(int)

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

        mk = pick_method_from_stack(frames)  # frames are already top->bottom from the async dump
        if mk is not None:
            method_samples[mk] += block_samples

        # note: do not i += 1 here; we want to re-process next '---' if present

    return total_samples, dict(method_samples)


# ============================================================
# JFR parsing via `jfr print --events jdk.ExecutionSample` (method-based)
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


def parse_jfr_report(jfr_bin: str, jfr_path: str) -> Tuple[int, Dict[str, int]]:
    """
    Treat each jdk.ExecutionSample stackTrace block as ONE sample (weight 1),
    and attribute it to the first non-marker method frame.
    """
    if not os.path.isfile(jfr_path):
        raise FileNotFoundError(jfr_path)

    lines = jfr_print_exec_samples(jfr_bin, jfr_path)

    total_samples = 0
    method_samples: Dict[str, int] = defaultdict(int)

    in_stack = False
    current_stack: List[str] = []

    def flush_stack():
        nonlocal total_samples, current_stack
        if not current_stack:
            return
        mk = pick_method_from_stack(current_stack)  # current_stack is top->bottom from JFR text
        if mk is not None:
            method_samples[mk] += 1
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
    return total_samples, dict(method_samples)


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
    counts: Dict[str, int],
) -> None:
    ensure_dir(os.path.dirname(out_csv) or ".")
    rows = []
    for method_key, samples in sorted(counts.items()):
        rows.append({
            "tool": tool,
            "benchmark": benchmark,
            "run_type": run_type,
            "method_key": method_key,
            "samples": samples,
            "total_samples": total,
            "runtime_share_pct": (samples / total * 100.0) if total > 0 else 0.0,
        })

    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "tool", "benchmark", "run_type",
            "method_key",
            "samples", "total_samples", "runtime_share_pct",
        ])
        w.writeheader()
        w.writerows(rows)


def write_slowdown_csv(
    out_csv: str,
    tool: str,
    benchmark: str,
    base_total: int,
    base_counts: Dict[str, int],
    slow_total: int,
    slow_counts: Dict[str, int],
) -> None:
    ensure_dir(os.path.dirname(out_csv) or ".")
    keys = sorted(set(base_counts.keys()) | set(slow_counts.keys()))
    rows = []
    for method_key in keys:
        b = base_counts.get(method_key, 0)
        s = slow_counts.get(method_key, 0)
        if b == 0:
            slowdown_pct = ""
        else:
            slowdown_pct = (s - b) / b * 100.0

        rows.append({
            "tool": tool,
            "benchmark": benchmark,
            "method_key": method_key,
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
            "method_key",
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
    ap = argparse.ArgumentParser(description="Parse Async GTAssignDebug + JFR ExecutionSample into CSVs (method-based, no markers needed).")
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
