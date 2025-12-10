#!/usr/bin/env python3

from pathlib import Path
import re
import csv
import subprocess
import matplotlib.pyplot as plt

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
# Async parsing (same as before)
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
    # Find delimiter index
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

    and then, for each ExecutionSample, extract the stack frames and
    run the same marker decoding as for async.
    """
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

    results = {}
    current_funcs = []

    for line in lines:
        # New sample marker (heuristic)
        if "Execution Sample" in line:
            # Finish previous sample
            if current_funcs:
                ids = extract_marker_ids_from_funcs(current_funcs)
                if ids is not None:
                    results[ids] = results.get(ids, 0) + 1
            current_funcs = []
            continue

        # Collect potential frame lines (just grab everything non-empty)
        if line.strip() and not line.strip().startswith("startTime") and line.strip() not in ["{", "}"]:
            current_funcs.append(line.strip())

    # Final sample
    if current_funcs:
        ids = extract_marker_ids_from_funcs(current_funcs)
        if ids is not None:
            results[ids] = results.get(ids, 0) + 1

    return results


# --------------------------------------------------------------------
# Per-level async + JFR aggregation (Bubo OFF)
# --------------------------------------------------------------------

def compute_percent_changes_no_bubo():
    """
    Returns:
      level_data: dict[level] -> dict[(CompId, LoopId)] -> {
        'Level', 'CompId', 'LoopId',
        'BaselineSamples',
        'AsyncNoBubo',
        'JfrNoBubo',
      }

    Baseline = Async NoSlowdown_BuboOff at that level.
    Comparisons:
      - AsyncNoBubo: Slowdown_BuboOff vs baseline
      - JfrNoBubo:   JFR_Slowdown_BuboOff vs same baseline
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

        base_path = async_dir / f"Mandelbrot_{lvl_str}_NoSlowdown_BuboOff_GTAssignDebug.txt"
        slow_path = async_dir / f"Mandelbrot_{lvl_str}_Slowdown_BuboOff_GTAssignDebug.txt"
        jfr_path = jfr_dir / f"Mandelbrot_{lvl_str}_JFR_Slowdown_BuboOff.jfr"

        if not base_path.exists() or not slow_path.exists() or not jfr_path.exists():
            print(f"[WARN] Skipping level {level} (missing one of async/JFR files for no-Bubo).")
            continue

        _, async_base_map = parse_async_marker_file(base_path)
        _, async_slow_map = parse_async_marker_file(slow_path)
        jfr_map = parse_jfr_execution_samples(jfr_path)

        if not async_base_map:
            print(f"[WARN] No async baseline samples at level {level} (no-Bubo).")
            continue

        per_loop = {}
        for key, base_samples in async_base_map.items():
            slow_samples = async_slow_map.get(key, 0)
            jfr_samples = jfr_map.get(key, 0)

            if base_samples > 0:
                async_pct = (slow_samples - base_samples) / float(base_samples) * 100.0
                jfr_pct = (jfr_samples - base_samples) / float(base_samples) * 100.0
            else:
                async_pct = 0.0
                jfr_pct = 0.0

            comp_id, loop_id = key
            per_loop[key] = {
                "Level": level,
                "CompId": comp_id,
                "LoopId": loop_id,
                "BaselineSamples": base_samples,
                "AsyncNoBubo": async_pct,
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
        'BaselineSamples',     # from async baseline
        'BuboBaselineCycles',
        'BuboSlowdownCycles',
        'BuboPercentChange',
        'AsyncBuboOn',
        'JfrBuboOn',
      }

    Baseline for percentages:
      - AsyncBuboOn, JfrBuboOn: async NoSlowdown_BuboOn
      - BuboPercentChange:      Bubo NoSlowdown_BuboOn exclusive cycles
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

        # Bubo (from .out, same prefixes)
        bubo_base_path = async_dir / f"Mandelbrot_{lvl_str}_NoSlowdown_BuboOn.out"
        bubo_slow_path = async_dir / f"Mandelbrot_{lvl_str}_Slowdown_BuboOn.out"

        # JFR
        jfr_path = jfr_dir / f"Mandelbrot_{lvl_str}_JFR_Slowdown_BuboOn.jfr"

        if not async_base_path.exists() or not async_slow_path.exists() or not jfr_path.exists():
            print(f"[WARN] Skipping level {level} (missing async/JFR BuboOn files).")
            continue
        if not bubo_base_path.exists() or not bubo_slow_path.exists():
            print(f"[WARN] Skipping level {level} (missing Bubo stdout files).")
            continue

        # Async
        _, async_base_map = parse_async_marker_file(async_base_path)
        _, async_slow_map = parse_async_marker_file(async_slow_path)
        jfr_map = parse_jfr_execution_samples(jfr_path)

        if not async_base_map:
            print(f"[WARN] No async baseline samples at level {level} (BuboOn).")
            continue

        # Bubo baseline + slowdown
        _, loops_base, parents_base, _ = parse_bubo_file(bubo_base_path)
        excl_base = compute_exclusive_cycles(loops_base, parents_base)
        bubo_base_map = {}
        for (comp_id, method, loop_id), cycles in excl_base.items():
            bubo_base_map[(comp_id, loop_id)] = cycles

        _, loops_slow, parents_slow, _ = parse_bubo_file(bubo_slow_path)
        excl_slow = compute_exclusive_cycles(loops_slow, parents_slow)
        bubo_slow_map = {}
        for (comp_id, method, loop_id), cycles in excl_slow.items():
            bubo_slow_map[(comp_id, loop_id)] = cycles

        per_loop = {}
        # Only consider loops seen by async baseline; Bubo/JFR may drop some
        for key, base_samples in async_base_map.items():
            comp_id, loop_id = key

            # Async + JFR
            slow_samples = async_slow_map.get(key, 0)
            jfr_samples = jfr_map.get(key, 0)

            if base_samples > 0:
                async_pct = (slow_samples - base_samples) / float(base_samples) * 100.0
                jfr_pct = (jfr_samples - base_samples) / float(base_samples) * 100.0
            else:
                async_pct = 0.0
                jfr_pct = 0.0

            # Bubo
            b_base = bubo_base_map.get(key, 0)
            b_slow = bubo_slow_map.get(key, 0)
            if b_base > 0:
                bubo_pct = (b_slow - b_base) / float(b_base) * 100.0
            else:
                bubo_pct = 0.0

            per_loop[key] = {
                "Level": level,
                "CompId": comp_id,
                "LoopId": loop_id,
                "BaselineSamples": base_samples,   # async baseline
                "BuboBaselineCycles": b_base,
                "BuboSlowdownCycles": b_slow,
                "BuboPercentChange": bubo_pct,
                "AsyncBuboOn": async_pct,
                "JfrBuboOn": jfr_pct,
            }

        level_data[level] = per_loop

    return level_data


# --------------------------------------------------------------------
# Overall plots (loops common to all levels)
# --------------------------------------------------------------------

def overall_plot(
    level_data,
    async_key: str,
    jfr_key: str,
    title: str,
    out_path: Path,
    bubo_key: str,
):
    """
    level_data: dict[level] -> dict[(CompId, LoopId)] -> row dict.
    async_key: field name for async percent change (e.g. 'AsyncNoBubo' or 'AsyncBuboOn').
    jfr_key:   field name for jfr percent change   (e.g. 'JfrNoBubo'   or 'JfrBuboOn').
    bubo_key:  optional field name for Bubo percent change (e.g. 'BuboPercentChange').
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

    # Rank by baseline samples at smallest level
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

    for (comp_id, loop_id) in sorted_keys:
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
            label=f"C{comp_id}-L{loop_id} Async",
        )
        ax.plot(
            x_positions,
            jfr_vals,
            marker="x",
            linestyle="--",
            label=f"C{comp_id}-L{loop_id} JFR",
        )
        if bubo_vals is not None:
            ax.plot(
                x_positions,
                bubo_vals,
                marker="s",
                linestyle=":",
                label=f"C{comp_id}-L{loop_id} Bubo",
            )

    ax.set_xticks(x_positions)
    ax.set_xticklabels(x_labels)
    ax.set_xlabel("Slowdown level")
    ax.set_ylabel("Percent change vs async no-slowdown baseline [%]")
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
    out_no_bubo = Path("Mandelbrot_Overall_NoBubo_Async_vs_JFR.png")
    overall_plot(
        no_bubo_data,
        async_key="AsyncNoBubo",
        jfr_key="JfrNoBubo",
        title="Mandelbrot – Async vs JFR (Bubo OFF) across slowdown levels",
        out_path=out_no_bubo,
        bubo_key=None,
    )

    # 2) Bubo + Async + JFR, Bubo ON
    bubo_on_data = compute_percent_changes_bubo_on()
    out_bubo_on = Path("Mandelbrot_Overall_BuboOn_Bubo_Async_JFR.png")
    overall_plot(
        bubo_on_data,
        async_key="AsyncBuboOn",
        jfr_key="JfrBuboOn",
        title="Mandelbrot – Bubo vs Async vs JFR (Bubo ON) across slowdown levels",
        out_path=out_bubo_on,
        bubo_key="BuboPercentChange",
    )


if __name__ == "__main__":
    main()
