#!/usr/bin/env python3

from pathlib import Path
import re
import csv
import subprocess
import matplotlib.pyplot as plt
from itertools import cycle

# --------------------------------------------------------------------
# Config
# --------------------------------------------------------------------

ROOT_ASYNC = Path("Mandelbrot_AsyncRuns")
ROOT_JFR = Path("Mandelbrot_JFRRuns")
LEVELS = [50, 100, 150, 200]  # will skip missing ones

# Path to JFR CLI; adjust if needed (e.g. "$JAVA_HOME/bin/jfr")
JFR_BIN = "jfr"

MAX_LOOPS = 10  # max loops shown in overall plots


# --------------------------------------------------------------------
# Async parsing
# --------------------------------------------------------------------

TOTAL_SAMPLES_RE = re.compile(r"^Total samples\s*:\s*(\d+)")
BLOCK_HEADER_RE = re.compile(
    r"^---\s+(\d+)\s+ns\s+\(([0-9.]+)%\),\s+(\d+)\s+samples"
)
FRAME_RE = re.compile(r"^\s*\[\s*\d+\s*\]\s+(.*)$")

MARKER_DELIM = "BuboAgentCompilerMarkers.MarkerDelimiter"
MARKER_RE = re.compile(r"BuboAgentCompilerMarkers\.Marker(\d+)\b")


def parse_total_samples(lines):
    for l in lines:
        m = TOTAL_SAMPLES_RE.match(l)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def iter_blocks(lines):
    """Yield (header_line, frame_lines) for async-profiler text output."""
    i = 0
    n = len(lines)
    while i < n:
        m = BLOCK_HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue

        header = lines[i].rstrip("\n")
        i += 1
        frames = []
        while i < n and not lines[i].startswith("--- "):
            if FRAME_RE.match(lines[i]):
                frames.append(lines[i].rstrip("\n"))
            i += 1

        yield header, frames


def parse_block_samples(header_line):
    m = BLOCK_HEADER_RE.match(header_line)
    return int(m.group(3))  # sample count


def extract_marker_ids_from_funcs(funcs):
    """
    Common marker decoding logic: given a list of 'function-like' strings,
    find the delimiter and decode (comp_id, loop_id).
    """
    try:
        d = next(i for i, f in enumerate(funcs) if MARKER_DELIM in f)
    except StopIteration:
        return None

    # Loop id = marker immediately before delimiter
    loop_id = None
    if d > 0:
        before = funcs[d - 1]
        mb = MARKER_RE.search(before)
        if mb:
            loop_id = int(mb.group(1))
    if loop_id is None:
        return None

    # Comp id digits = markers after delimiter
    digits = []
    for f in funcs[d + 1:]:
        mm = MARKER_RE.search(f)
        if not mm:
            break
        digits.append(mm.group(1))
    if not digits:
        return None

    comp_id = int("".join(reversed(digits)))
    return (comp_id, loop_id)


def extract_marker_ids(frame_lines):
    """
    Async-specific: frame_lines are lines like:
      "[ 0] jdk...."
    We first strip to function strings, then call extract_marker_ids_from_funcs.
    """
    funcs = []
    for fl in frame_lines:
        m = FRAME_RE.match(fl)
        if m:
            funcs.append(m.group(1))
    if not funcs:
        return None
    return extract_marker_ids_from_funcs(funcs)


def parse_async_marker_file(path: Path):
    """
    Parse *_GTAssignDebug.txt into:
      total_samples: int or None
      results: dict[(comp_id, loop_id)] = samples
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = parse_total_samples(lines)
    results = {}

    for hdr, frames in iter_blocks(lines):
        block_samples = parse_block_samples(hdr)
        ids = extract_marker_ids(frames)
        if ids is None:
            continue
        results[ids] = results.get(ids, 0) + block_samples

    return total, results


# --------------------------------------------------------------------
# Bubo parsing (from .out files)
# --------------------------------------------------------------------

TOTAL_RUNTIME_RE = re.compile(r"Total Runtime:\s+(\d+)us")
COMP_RE = re.compile(r"^Comp\s+(\d+)\s+\((.+?)\)\s+loops:")
LOOP_RE = re.compile(r"loop\s+(\d+)\s+Cycles:\s+(\d+)")
LOOP_CALL_RE = re.compile(r"LoopCallCount:\s*(\d+)")
ENCODING_RE = re.compile(r"Found Encoding\s*:\s*(.*)")


def parse_bubo_file(path: Path):
    """
    Parse Bubo .out file.

    Returns:
      total_runtime_us: int or None
      loops:        dict[(comp_id, method_name, loop_id)] = inclusive_cycles
      parents:      dict[(comp_id, loop_id)] = parent_loop_id or None
      loop_calls:   dict[(comp_id, loop_id)] = LoopCallCount (int)
    """
    total_runtime = None
    loops = {}
    parents = {}
    loop_calls = {}
    current_comp_id = None
    current_method = None

    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")

            m = TOTAL_RUNTIME_RE.search(line)
            if m:
                try:
                    total_runtime = int(m.group(1))
                except ValueError:
                    pass
                continue

            m = COMP_RE.match(line)
            if m:
                current_comp_id = int(m.group(1))
                current_method = m.group(2).strip()
                continue

            m = ENCODING_RE.search(line)
            if m and current_comp_id is not None:
                enc_str = m.group(1).strip()
                if enc_str:
                    parts = enc_str.split(",")
                    for p in parts:
                        p = p.strip()
                        if not p or ":" not in p:
                            continue
                        lid_str, pid_str = p.split(":", 1)
                        try:
                            lid = int(lid_str)
                            pid = int(pid_str)
                        except ValueError:
                            continue
                        parent = None if pid < 0 else pid
                        parents[(current_comp_id, lid)] = parent
                continue

            if current_comp_id is not None:
                m = LOOP_RE.search(line)
                if m:
                    loop_id = int(m.group(1))
                    cycles = int(m.group(2))

                    call_match = LOOP_CALL_RE.search(line)
                    if call_match:
                        try:
                            call_count = int(call_match.group(1))
                        except ValueError:
                            call_count = 0
                    else:
                        call_count = 0

                    loops[(current_comp_id, current_method, loop_id)] = cycles
                    loop_calls[(current_comp_id, loop_id)] = call_count
                    continue

            if not line.strip():
                current_comp_id = None
                current_method = None

    return total_runtime, loops, parents, loop_calls


def compute_exclusive_cycles(loops, parents):
    """
    Given:
      loops:   dict[(comp_id, method, loop_id)] = inclusive_cycles
      parents: dict[(comp_id, loop_id)] = parent_loop_id or None

    Return:
      exclusive: dict[(comp_id, method, loop_id)] = exclusive_cycles
    """
    key_by_id = {}
    for (comp_id, method, loop_id) in loops.keys():
        key_by_id[(comp_id, loop_id)] = (comp_id, method, loop_id)

    children = {}
    for (comp_id, loop_id), parent in parents.items():
        if parent is None:
            continue
        children.setdefault((comp_id, parent), []).append(loop_id)

    exclusive = {}
    for (comp_id, method, loop_id), inclusive in loops.items():
        child_ids = children.get((comp_id, loop_id), [])
        child_sum = 0
        for cid in child_ids:
            child_key = key_by_id.get((comp_id, cid))
            if child_key is not None:
                child_sum += loops.get(child_key, 0)

        excl = inclusive - child_sum
        if excl < 0:
            excl = 0
        exclusive[(comp_id, method, loop_id)] = excl

    return exclusive


# --------------------------------------------------------------------
# JFR parsing: use `jfr print --events ExecutionSample`
# --------------------------------------------------------------------

def parse_jfr_execution_samples(jfr_path: Path):
    """
    Return dict[(comp_id, loop_id)] = sample_count for this JFR file.

    We call:
      jfr print --events ExecutionSample <file.jfr>
    """
    DEBUG_JFR = False  # flip to True if you want verbose output

    if DEBUG_JFR:
        print(f"[JFR DEBUG] ----------------------------------------")
        print(f"[JFR DEBUG] Parsing JFR file: {jfr_path}")

    cmd = [JFR_BIN, "print", "--events", "ExecutionSample", str(jfr_path)]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] jfr print failed for {jfr_path}: {e}")
        print(e.stderr)
        return {}

    lines = proc.stdout.splitlines()
    if DEBUG_JFR:
        print(f"[JFR DEBUG] jfr print produced {len(lines)} lines")

    results = {}
    current_funcs = []
    in_stack = False

    sample_count = 0
    sample_with_frames = 0
    sample_with_ids = 0

    frames_with_delim = []
    frames_with_marker = []

    for line in lines:
        stripped = line.strip()

        # New sample start: jdk.ExecutionSample {
        if stripped.startswith("jdk.ExecutionSample"):
            # Flush previous sample
            if current_funcs:
                sample_with_frames += 1
                ids = extract_marker_ids_from_funcs(current_funcs)
                if ids is not None:
                    sample_with_ids += 1
                    results[ids] = results.get(ids, 0) + 1

                if DEBUG_JFR and len(frames_with_delim) < 3:
                    print("[JFR DEBUG] Example sample frames (truncated):")
                    for f in current_funcs[:6]:
                        print(f"    {f}")

            current_funcs = []
            in_stack = False
            sample_count += 1
            continue

        # Detect the start of the stackTrace block
        if "stackTrace =" in line and "[" in line:
            in_stack = True
            continue

        # Inside stackTrace block: collect frame lines until ']'
        if in_stack:
            if stripped.startswith("]"):
                in_stack = False
                continue
            if stripped:
                current_funcs.append(stripped)
                if MARKER_DELIM in stripped and len(frames_with_delim) < 5:
                    frames_with_delim.append(stripped)
                if "BuboAgentCompilerMarkers.Marker" in stripped and len(frames_with_marker) < 5:
                    frames_with_marker.append(stripped)
            continue

    # Flush last sample at EOF
    if current_funcs:
        sample_with_frames += 1
        ids = extract_marker_ids_from_funcs(current_funcs)
        if ids is not None:
            sample_with_ids += 1
            results[ids] = results.get(ids, 0) + 1

        if DEBUG_JFR and len(frames_with_delim) < 3:
            print("[JFR DEBUG] Example sample frames (truncated, final sample):")
            for f in current_funcs[:6]:
                print(f"    {f}")

    if DEBUG_JFR:
        print(f"[JFR DEBUG] Total ExecutionSample events seen: {sample_count}")
        print(f"[JFR DEBUG] Samples with any stack frames:   {sample_with_frames}")
        print(f"[JFR DEBUG] Samples with decoded IDs:        {sample_with_ids}")
        print(f"[JFR DEBUG] Unique (CompId,LoopId) entries:  {len(results)}")

        if frames_with_delim:
            print("[JFR DEBUG] Frames containing MARKER_DELIM:")
            for f in frames_with_delim:
                print(f"    {f}")
        else:
            print("[JFR DEBUG] No frames contained MARKER_DELIM string.")

        if frames_with_marker:
            print("[JFR DEBUG] Frames containing Bubo markers:")
            for f in frames_with_marker:
                print(f"    {f}")
        else:
            print("[JFR DEBUG] No frames contained 'BuboAgentCompilerMarkers.MarkerNNN'.")

    return results


# --------------------------------------------------------------------
# Per-level async + JFR aggregation (Bubo OFF)
# --------------------------------------------------------------------

def compute_percent_changes_no_bubo():
    """
    Returns:
      level_data: dict[level] -> dict[(CompId, LoopId)] -> {
        'Level', 'CompId', 'LoopId',
        'BaselineSamples',      # async baseline
        'AsyncSlowSamples',     # async slowdown
        'AsyncNoBubo',          # % change vs async baseline
        'JfrBaselineSamples',   # JFR no-slowdown samples
        'JfrSamples',           # JFR slowdown samples
        'JfrNoBubo',            # % change vs JFR baseline
      }
    """
    level_data = {}

    for level in LEVELS:
        lvl_str = str(level)
        async_dir = ROOT_ASYNC / f"Mandelbrot{level}"
        jfr_dir = ROOT_JFR / f"Mandelbrot{level}"

        if not async_dir.is_dir():
            print(f"[WARN] Missing async dir for level {level}: {async_dir}")
            continue
        if not jfr_dir.is_dir():
            print(f"[WARN] Missing JFR dir for level {level}: {jfr_dir}")
            continue

        # Async files
        base_path = async_dir / f"Mandelbrot_{lvl_str}_NoSlowdown_BuboOff_GTAssignDebug.txt"
        slow_path = async_dir / f"Mandelbrot_{lvl_str}_Slowdown_BuboOff_GTAssignDebug.txt"

        # JFR files (now both baseline and slowdown)
        jfr_base_path = jfr_dir / f"Mandelbrot_{lvl_str}_JFR_NoSlowdown_BuboOff.jfr"
        jfr_slow_path = jfr_dir / f"Mandelbrot_{lvl_str}_JFR_Slowdown_BuboOff.jfr"

        if not base_path.exists() or not slow_path.exists():
            print(f"[WARN] Skipping level {level} (missing async no-bubo files).")
            continue
        if not jfr_base_path.exists() or not jfr_slow_path.exists():
            print(f"[WARN] Skipping level {level} (missing JFR no-bubo files).")
            continue

        _, async_base_map = parse_async_marker_file(base_path)
        _, async_slow_map = parse_async_marker_file(slow_path)
        jfr_base_map = parse_jfr_execution_samples(jfr_base_path)
        jfr_slow_map = parse_jfr_execution_samples(jfr_slow_path)

        if not async_base_map and not jfr_base_map and not jfr_slow_map:
            print(f"[WARN] No async/JFR samples at level {level} (no-Bubo).")
            continue

        per_loop = {}
        all_keys = (
            set(async_base_map.keys()) |
            set(async_slow_map.keys()) |
            set(jfr_base_map.keys()) |
            set(jfr_slow_map.keys())
        )

        for key in all_keys:
            base_samples = async_base_map.get(key, 0)
            slow_samples = async_slow_map.get(key, 0)

            jfr_base = jfr_base_map.get(key, 0)
            jfr_slow = jfr_slow_map.get(key, 0)

            # Async % vs async baseline
            if base_samples > 0:
                async_pct = (slow_samples - base_samples) / float(base_samples) * 100.0
            else:
                async_pct = 0.0

            # JFR % vs JFR baseline
            if jfr_base > 0:
                jfr_pct = (jfr_slow - jfr_base) / float(jfr_base) * 100.0
            else:
                jfr_pct = 0.0

            comp_id, loop_id = key
            per_loop[key] = {
                "Level": level,
                "CompId": comp_id,
                "LoopId": loop_id,
                "BaselineSamples": base_samples,
                "AsyncSlowSamples": slow_samples,
                "AsyncNoBubo": async_pct,
                "JfrBaselineSamples": jfr_base,
                "JfrSamples": jfr_slow,
                "JfrNoBubo": jfr_pct,
            }

        level_data[level] = per_loop

    return level_data


# --------------------------------------------------------------------
# Per-level async + JFR + Bubo aggregation (Bubo ON)
# --------------------------------------------------------------------

def compute_percent_changes_bubo_on():
    """
    Returns:
      level_data: dict[level] -> dict[(CompId, LoopId)] -> {
        'Level', 'CompId', 'LoopId',
        'BaselineSamples',      # async baseline
        'AsyncSlowSamples',     # async slowdown
        'AsyncBuboOn',          # % vs async baseline
        'BuboBaselineCycles',
        'BuboSlowdownCycles',
        'BuboPercentChange',    # % vs Bubo baseline cycles
        'JfrBaselineSamples',   # JFR no-slowdown
        'JfrSamples',           # JFR slowdown
        'JfrBuboOn',            # % vs JFR baseline
      }
    """
    level_data = {}

    for level in LEVELS:
        lvl_str = str(level)
        async_dir = ROOT_ASYNC / f"Mandelbrot{level}"
        jfr_dir = ROOT_JFR / f"Mandelbrot{level}"

        if not async_dir.is_dir():
            print(f"[WARN] Missing async dir for level {level}: {async_dir}")
            continue
        if not jfr_dir.is_dir():
            print(f"[WARN] Missing JFR dir for level {level}: {jfr_dir}")
            continue

        # Async
        async_base_path = async_dir / f"Mandelbrot_{lvl_str}_NoSlowdown_BuboOn_GTAssignDebug.txt"
        async_slow_path = async_dir / f"Mandelbrot_{lvl_str}_Slowdown_BuboOn_GTAssignDebug.txt"

        # Bubo (from .out)
        bubo_base_path = async_dir / f"Mandelbrot_{lvl_str}_NoSlowdown_BuboOn.out"
        bubo_slow_path = async_dir / f"Mandelbrot_{lvl_str}_Slowdown_BuboOn.out"

        # JFR
        jfr_base_path = jfr_dir / f"Mandelbrot_{lvl_str}_JFR_NoSlowdown_BuboOn.jfr"
        jfr_slow_path = jfr_dir / f"Mandelbrot_{lvl_str}_JFR_Slowdown_BuboOn.jfr"

        if not async_base_path.exists() or not async_slow_path.exists():
            print(f"[WARN] Skipping level {level} (missing async BuboOn files).")
            continue
        if not bubo_base_path.exists() or not bubo_slow_path.exists():
            print(f"[WARN] Skipping level {level} (missing Bubo stdout files).")
            continue
        if not jfr_base_path.exists() or not jfr_slow_path.exists():
            print(f"[WARN] Skipping level {level} (missing JFR BuboOn files).")
            continue

        # Async
        _, async_base_map = parse_async_marker_file(async_base_path)
        _, async_slow_map = parse_async_marker_file(async_slow_path)

        # JFR
        jfr_base_map = parse_jfr_execution_samples(jfr_base_path)
        jfr_slow_map = parse_jfr_execution_samples(jfr_slow_path)

        # Bubo baseline + slowdown
        _, loops_base, parents_base, _ = parse_bubo_file(bubo_base_path)
        excl_base = compute_exclusive_cycles(loops_base, parents_base)
        bubo_base_map = {(comp_id, loop_id): cycles
                         for (comp_id, method, loop_id), cycles in excl_base.items()}

        _, loops_slow, parents_slow, _ = parse_bubo_file(bubo_slow_path)
        excl_slow = compute_exclusive_cycles(loops_slow, parents_slow)
        bubo_slow_map = {(comp_id, loop_id): cycles
                         for (comp_id, method, loop_id), cycles in excl_slow.items()}

        if not async_base_map and not jfr_base_map and not bubo_base_map:
            print(f"[WARN] No async/JFR/Bubo data at level {level} (BuboOn).")
            continue

        per_loop = {}
        all_keys = (
            set(async_base_map.keys()) |
            set(async_slow_map.keys()) |
            set(jfr_base_map.keys()) |
            set(jfr_slow_map.keys()) |
            set(bubo_base_map.keys()) |
            set(bubo_slow_map.keys())
        )

        for key in all_keys:
            comp_id, loop_id = key

            base_samples = async_base_map.get(key, 0)
            slow_samples = async_slow_map.get(key, 0)

            # Async % vs async baseline
            if base_samples > 0:
                async_pct = (slow_samples - base_samples) / float(base_samples) * 100.0
            else:
                async_pct = 0.0

            # Bubo
            b_base = bubo_base_map.get(key, 0)
            b_slow = bubo_slow_map.get(key, 0)
            if b_base > 0:
                bubo_pct = (b_slow - b_base) / float(b_base) * 100.0
            else:
                bubo_pct = 0.0

            # JFR % vs JFR baseline
            jfr_base = jfr_base_map.get(key, 0)
            jfr_slow = jfr_slow_map.get(key, 0)
            if jfr_base > 0:
                jfr_pct = (jfr_slow - jfr_base) / float(jfr_base) * 100.0
            else:
                jfr_pct = 0.0

            per_loop[key] = {
                "Level": level,
                "CompId": comp_id,
                "LoopId": loop_id,
                "BaselineSamples": base_samples,
                "AsyncSlowSamples": slow_samples,
                "AsyncBuboOn": async_pct,
                "BuboBaselineCycles": b_base,
                "BuboSlowdownCycles": b_slow,
                "BuboPercentChange": bubo_pct,
                "JfrBaselineSamples": jfr_base,
                "JfrSamples": jfr_slow,
                "JfrBuboOn": jfr_pct,
            }

        level_data[level] = per_loop

    return level_data


# --------------------------------------------------------------------
# CSV writers
# --------------------------------------------------------------------

def write_no_bubo_csv(level_data, out_path: Path):
    """
    Flatten no-Bubo level_data into a CSV.
    """
    fieldnames = [
        "Level",
        "CompId",
        "LoopId",
        "BaselineSamples",
        "AsyncSlowSamples",
        "AsyncNoBubo",
        "JfrBaselineSamples",
        "JfrSamples",
        "JfrNoBubo",
    ]

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for level in sorted(level_data.keys()):
            per_loop = level_data[level]
            for (comp_id, loop_id), row in per_loop.items():
                writer.writerow({
                    "Level": level,
                    "CompId": comp_id,
                    "LoopId": loop_id,
                    "BaselineSamples": row.get("BaselineSamples", 0),
                    "AsyncSlowSamples": row.get("AsyncSlowSamples", 0),
                    "AsyncNoBubo": row.get("AsyncNoBubo", 0.0),
                    "JfrBaselineSamples": row.get("JfrBaselineSamples", 0),
                    "JfrSamples": row.get("JfrSamples", 0),
                    "JfrNoBubo": row.get("JfrNoBubo", 0.0),
                })

    print(f"[INFO] Wrote CSV (no-bubo): {out_path}")


def write_bubo_on_csv(level_data, out_path: Path):
    """
    Flatten Bubo-on level_data into a CSV.
    """
    fieldnames = [
        "Level",
        "CompId",
        "LoopId",
        "BaselineSamples",
        "AsyncSlowSamples",
        "AsyncBuboOn",
        "BuboBaselineCycles",
        "BuboSlowdownCycles",
        "BuboPercentChange",
        "JfrBaselineSamples",
        "JfrSamples",
        "JfrBuboOn",
    ]

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for level in sorted(level_data.keys()):
            per_loop = level_data[level]
            for (comp_id, loop_id), row in per_loop.items():
                writer.writerow({
                    "Level": level,
                    "CompId": comp_id,
                    "LoopId": loop_id,
                    "BaselineSamples": row.get("BaselineSamples", 0),
                    "AsyncSlowSamples": row.get("AsyncSlowSamples", 0),
                    "AsyncBuboOn": row.get("AsyncBuboOn", 0.0),
                    "BuboBaselineCycles": row.get("BuboBaselineCycles", 0),
                    "BuboSlowdownCycles": row.get("BuboSlowdownCycles", 0),
                    "BuboPercentChange": row.get("BuboPercentChange", 0.0),
                    "JfrBaselineSamples": row.get("JfrBaselineSamples", 0),
                    "JfrSamples": row.get("JfrSamples", 0),
                    "JfrBuboOn": row.get("JfrBuboOn", 0.0),
                })

    print(f"[INFO] Wrote CSV (bubo-on): {out_path}")


# --------------------------------------------------------------------
# Helper: filter loops by >= threshold% of total for any tool at ref level
# --------------------------------------------------------------------

def select_significant_loops(level_data, has_bubo: bool, threshold_pct: float):
    """
    Return a set of (CompId, LoopId) that contribute at least `threshold_pct`
    of the total for ANY tool (Async, JFR, Bubo) at the reference level.
    """
    if not level_data:
        return set()

    levels = sorted(level_data.keys())
    ref_level = min(levels)
    ref_map = level_data[ref_level]

    total_async = sum(row.get("BaselineSamples", 0) for row in ref_map.values())
    total_jfr = sum(row.get("JfrBaselineSamples", 0) for row in ref_map.values())
    total_bubo = 0
    if has_bubo:
        total_bubo = sum(row.get("BuboBaselineCycles", 0) for row in ref_map.values())

    selected = set()
    for key, row in ref_map.items():
        async_share = 0.0
        jfr_share = 0.0
        bubo_share = 0.0

        if total_async > 0:
            async_share = 100.0 * row.get("BaselineSamples", 0) / float(total_async)
        if total_jfr > 0:
            jfr_share = 100.0 * row.get("JfrBaselineSamples", 0) / float(total_jfr)
        if has_bubo and total_bubo > 0:
            bubo_share = 100.0 * row.get("BuboBaselineCycles", 0) / float(total_bubo)

        if (async_share >= threshold_pct or
            jfr_share >= threshold_pct or
            bubo_share >= threshold_pct):
            selected.add(key)

    return selected


# --------------------------------------------------------------------
# Overall plots
# --------------------------------------------------------------------

def overall_plot(
    level_data,
    async_key: str,
    jfr_key: str,
    title: str,
    out_path: Path,
    bubo_key: str,
    min_share_pct: float = None,
):
    """
    level_data: dict[level] -> dict[(CompId, LoopId)] -> row dict.
    async_key: field name for async percent change.
    jfr_key:   field name for JFR percent change.
    bubo_key:  optional field name for Bubo percent change.

    If min_share_pct is set, only include loops that are >= that percent
    of total for ANY tool (Async/JFR/Bubo) at the reference level.
    """
    if not level_data:
        print("[WARN] No data; skipping overall plot:", out_path)
        return

    levels = sorted(level_data.keys())
    per_level_maps = level_data

    # Loops common to all levels
    common_keys = None
    for lvl in levels:
        keys = set(per_level_maps[lvl].keys())
        common_keys = keys if common_keys is None else (common_keys & keys)

    if not common_keys:
        print("[WARN] No loops common to all levels; skipping plot:", out_path)
        return

    # Optionally filter by significance
    if min_share_pct is not None:
        has_bubo = bubo_key is not None
        significant = select_significant_loops(level_data, has_bubo, min_share_pct)
        common_keys = common_keys & significant
        if not common_keys:
            print(f"[WARN] No loops >= {min_share_pct}% share at ref level; skipping plot:", out_path)
            return

    # Rank by async baseline samples at smallest level
    ref_level = min(levels)
    ref_map = per_level_maps[ref_level]

    sorted_keys = sorted(
        common_keys,
        key=lambda k: ref_map[k]["BaselineSamples"],
        reverse=True,
    )
    if len(sorted_keys) > MAX_LOOPS:
        sorted_keys = sorted_keys[:MAX_LOOPS]

    print(f"[INFO] Overall plot {out_path}: using {len(sorted_keys)} loops.")

    fig, ax = plt.subplots(figsize=(10, 6))

    x_positions = list(range(len(levels)))
    x_labels = [str(l) for l in levels]

    base_colors = plt.rcParams['axes.prop_cycle'].by_key().get('color', [])
    if not base_colors:
        base_colors = ["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]
    colour_cycle = cycle(base_colors)

    for (comp_id, loop_id) in sorted_keys:
        colour = next(colour_cycle)

        async_vals = []
        jfr_vals = []
        bubo_vals = [] if bubo_key is not None else None

        for lvl in levels:
            row = per_level_maps[lvl].get((comp_id, loop_id))
            if row is None:
                async_vals.append(0.0)
                jfr_vals.append(0.0)
                if bubo_vals is not None:
                    bubo_vals.append(0.0)
            else:
                async_vals.append(row[async_key])
                jfr_vals.append(row[jfr_key])
                if bubo_vals is not None:
                    bubo_vals.append(row[bubo_key])

        ax.plot(
            x_positions,
            async_vals,
            marker="o",
            linestyle="-",
            linewidth=1.5,
            color=colour,
            label=f"C{comp_id}-L{loop_id} Async",
        )

        ax.plot(
            x_positions,
            jfr_vals,
            marker="x",
            linestyle="--",
            linewidth=1.5,
            color=colour,
            label=f"C{comp_id}-L{loop_id} JFR",
        )

        if bubo_vals is not None:
            ax.plot(
                x_positions,
                bubo_vals,
                marker="s",
                linestyle=":",
                linewidth=1.5,
                color=colour,
                label=f"C{comp_id}-L{loop_id} Bubo",
            )

    ax.set_xticks(x_positions)
    ax.set_xticklabels(x_labels)
    ax.set_xlabel("Slowdown level")
    ax.set_ylabel("Percent change vs own no-slowdown baseline [%]")
    ax.set_title(title)

    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)

    print(f"[INFO] Wrote overall plot: {out_path}")


# --------------------------------------------------------------------
# Main
# --------------------------------------------------------------------

def main():
    # 1) Async vs JFR, Bubo OFF
    no_bubo_data = compute_percent_changes_no_bubo()

    out_no_bubo_plot = Path("Mandelbrot_Overall_NoBubo_Async_vs_JFR.png")
    overall_plot(
        no_bubo_data,
        async_key="AsyncNoBubo",
        jfr_key="JfrNoBubo",
        title="Mandelbrot – Async vs JFR (Bubo OFF) across slowdown levels",
        out_path=out_no_bubo_plot,
        bubo_key=None,
        min_share_pct=None,
    )

    out_no_bubo_plot_top = Path("Mandelbrot_Overall_NoBubo_Async_vs_JFR_Top20pct.png")
    overall_plot(
        no_bubo_data,
        async_key="AsyncNoBubo",
        jfr_key="JfrNoBubo",
        title="Mandelbrot – Async vs JFR (Bubo OFF, top ≥20% loops)",
        out_path=out_no_bubo_plot_top,
        bubo_key=None,
        min_share_pct=20.0,
    )

    out_no_bubo_csv = Path("Mandelbrot_Overall_NoBubo_PerLoop.csv")
    write_no_bubo_csv(no_bubo_data, out_no_bubo_csv)

    # 2) Bubo + Async + JFR, Bubo ON
    bubo_on_data = compute_percent_changes_bubo_on()

    out_bubo_on_plot = Path("Mandelbrot_Overall_BuboOn_Bubo_Async_JFR.png")
    overall_plot(
        bubo_on_data,
        async_key="AsyncBuboOn",
        jfr_key="JfrBuboOn",
        title="Mandelbrot – Bubo vs Async vs JFR (Bubo ON) across slowdown levels",
        out_path=out_bubo_on_plot,
        bubo_key="BuboPercentChange",
        min_share_pct=None,
    )

    out_bubo_on_plot_top = Path("Mandelbrot_Overall_BuboOn_Bubo_Async_JFR_Top20pct.png")
    overall_plot(
        bubo_on_data,
        async_key="AsyncBuboOn",
        jfr_key="JfrBuboOn",
        title="Mandelbrot – Bubo vs Async vs JFR (Bubo ON, top ≥20% loops)",
        out_path=out_bubo_on_plot_top,
        bubo_key="BuboPercentChange",
        min_share_pct=20.0,
    )

    out_bubo_on_csv = Path("Mandelbrot_Overall_BuboOn_PerLoop.csv")
    write_bubo_on_csv(bubo_on_data, out_bubo_on_csv)


if __name__ == "__main__":
    main()
