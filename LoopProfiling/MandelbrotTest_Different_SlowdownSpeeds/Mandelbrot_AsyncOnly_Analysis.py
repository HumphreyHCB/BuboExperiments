#!/usr/bin/env python3

"""
Pure async-only analysis for Mandelbrot, Bubo OFF.

Assumes per-level directories:

  Mandelbrot_AsyncAndBuboSlowdownRuns/
    Mandelbrot50/
      Mandelbrot_50_NoSlowdown_BuboOff_GTAssignDebug.txt
      Mandelbrot_50_Slowdown_BuboOff_GTAssignDebug.txt
    Mandelbrot100/
    Mandelbrot150/
    Mandelbrot200/

For each level (50, 100, 150, 200):

  - Parse async baseline (NoSlowdown_BuboOff).
  - Parse async slowdown (Slowdown_BuboOff).
  - For each (CompId, LoopId) in baseline, compute percent change:

        ((slowdown - baseline) / baseline) * 100

  - Write per-level CSV.

Then:

  - Find loops that exist in ALL levels.
  - Pick up to MAX_LOOPS of them (sorted by baseline samples at level 50).
  - Make one overall line plot:

        x-axis: slowdown level (50, 100, 150, 200)
        y-axis: percent change
        one line per loop (CompId, LoopId).
"""

from pathlib import Path
import re
import csv
import matplotlib.pyplot as plt

# -----------------------------------------------------------------------------
# Async parsing (same format as your existing scripts)
# -----------------------------------------------------------------------------

TOTAL_SAMPLES_RE = re.compile(r"^Total samples\s*:\s*(\d+)")
BLOCK_HEADER_RE = re.compile(
    r"^---\s+(\d+)\s+ns\s+\(([0-9.]+)%\),\s+(\d+)\s+samples"
)
FRAME_RE = re.compile(r"^\s*\[\s*\d+\s*\]\s+(.*)$")

MARKER_DELIM = "BuboAgentCompilerMarkers.MarkerDelimiter"
MARKER_RE = re.compile(r"BuboAgentCompilerMarkers\.Marker(\d+)\b")

MAX_LOOPS = 20  # max loops for the overall plot (tweak if you like)


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
    """Yield (header_line, frame_lines) for each async block."""
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


def extract_marker_ids(frame_lines):
    """
    Extract (comp_id, loop_id) from stack frames with marker layout:

      [*] ... Marker<digit> ...
      [*] ... MarkerDelimiter ...
      [*] ... Marker<digit> ...
      ...

    - Loop ID = marker immediately BEFORE the delimiter.
    - Comp ID digits = markers AFTER delimiter, concatenated and reversed.
    """
    funcs = []
    for fl in frame_lines:
        m = FRAME_RE.match(fl)
        if m:
            funcs.append(m.group(1))

    try:
        d = funcs.index(MARKER_DELIM)
    except ValueError:
        return None

    loop_id = None
    if d > 0:
        before = funcs[d - 1]
        mb = MARKER_RE.search(before)
        if mb:
            loop_id = int(mb.group(1))
    if loop_id is None:
        return None

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


# -----------------------------------------------------------------------------
# Per-level analysis
# -----------------------------------------------------------------------------

def analyze_level(level: int, level_dir: Path, out_root: Path):
    """
    Analyze a single slowdown level using async-only, Bubo OFF:

      baseline:  Mandelbrot_<level>_NoSlowdown_BuboOff_GTAssignDebug.txt
      slowdown:  Mandelbrot_<level>_Slowdown_BuboOff_GTAssignDebug.txt

    Returns:
      per_loop: dict[(CompId, LoopId)] = {
        "Level": level,
        "CompId": ...,
        "LoopId": ...,
        "BaselineSamples": ...,
        "SlowdownSamples": ...,
        "PercentChange": ...,
      }
    """
    lvl_str = str(level)
    print(f"[INFO] Level {lvl_str}, directory: {level_dir}")

    base_path = level_dir / f"Mandelbrot_{lvl_str}_NoSlowdown_BuboOff_GTAssignDebug.txt"
    slow_path = level_dir / f"Mandelbrot_{lvl_str}_Slowdown_BuboOff_GTAssignDebug.txt"

    if not base_path.exists():
        print(f"  [WARN] Missing baseline async file: {base_path}")
        return {}
    if not slow_path.exists():
        print(f"  [WARN] Missing slowdown async file: {slow_path}")
        return {}

    _, base_map = parse_async_marker_file(base_path)
    _, slow_map = parse_async_marker_file(slow_path)

    if not base_map:
        print(f"  [WARN] No baseline samples at level {level}")
        return {}

    per_loop = {}

    for key, base_samples in base_map.items():
        slow_samples = slow_map.get(key, 0)
        if base_samples > 0:
            pct = (slow_samples - base_samples) / float(base_samples) * 100.0
        else:
            pct = 0.0

        comp_id, loop_id = key
        per_loop[key] = {
            "Level": level,
            "CompId": comp_id,
            "LoopId": loop_id,
            "BaselineSamples": base_samples,
            "SlowdownSamples": slow_samples,
            "PercentChange": pct,
        }

    # Per-level CSV
    out_csv = out_root / f"Mandelbrot_{level}_AsyncOnly_BuboOff_PerLevel.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "Level",
                "CompId",
                "LoopId",
                "BaselineSamples",
                "SlowdownSamples",
                "PercentChange",
            ],
        )
        writer.writeheader()
        for row in per_loop.values():
            writer.writerow(row)

    print(f"  -> wrote per-level CSV: {out_csv}")

    return per_loop


# -----------------------------------------------------------------------------
# Overall plot across slowdown levels
# -----------------------------------------------------------------------------

def create_overall_plot(level_loop_data, out_png: Path):
    """
    level_loop_data: dict[level] -> dict[(CompId, LoopId)] = row dict.

    Builds a single plot with:

      x-axis: slowdown level (50, 100, 150, 200)
      y-axis: percent change vs baseline at that level
      one line per (CompId, LoopId) that exists in ALL levels.
    """
    levels = sorted(level_loop_data.keys())
    if not levels:
        print("[WARN] No level data; skipping overall plot.")
        return

    # Build mapping: level -> {(CompId, LoopId): row}
    per_level_maps = {lvl: data for lvl, data in level_loop_data.items()}

    # Loops common to all levels (intersection)
    common_keys = None
    for lvl in levels:
        keys = set(per_level_maps[lvl].keys())
        if common_keys is None:
            common_keys = keys
        else:
            common_keys &= keys

    if not common_keys:
        print("[WARN] No (CompId, LoopId) common to all levels; skipping overall plot.")
        return

    # For ranking, use baseline samples from the lowest level (e.g., 50)
    ref_level = min(levels)
    ref_map = per_level_maps[ref_level]

    # Sort common keys by baseline samples at ref_level, descending
    sorted_keys = sorted(
        common_keys,
        key=lambda k: ref_map[k]["BaselineSamples"],
        reverse=True,
    )

    if len(sorted_keys) > MAX_LOOPS:
        sorted_keys = sorted_keys[:MAX_LOOPS]

    if not sorted_keys:
        print("[WARN] No keys selected for overall plot after MAX_LOOPS limit.")
        return

    # Build plot
    fig, ax = plt.subplots(figsize=(10, 6))
    x_positions = list(range(len(levels)))
    x_labels = [str(lvl) for lvl in levels]

    for (comp_id, loop_id) in sorted_keys:
        pct_vals = []
        for lvl in levels:
            row = per_level_maps[lvl].get((comp_id, loop_id))
            if row is None:
                pct_vals.append(0.0)
            else:
                pct_vals.append(row["PercentChange"])

        label = f"C{comp_id}-L{loop_id}"
        ax.plot(
            x_positions,
            pct_vals,
            marker="o",
            linestyle="-",
            label=label,
        )

    ax.set_xticks(x_positions)
    ax.set_xticklabels(x_labels)
    ax.set_xlabel("Slowdown level (Bubo OFF)")
    ax.set_ylabel("Async percent change vs no-slowdown baseline [%]")
    ax.set_title(
        "Mandelbrot – Async-only percent change across slowdown levels\n"
        "(Bubo OFF, loops common to all levels)"
    )

    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_png, dpi=200)
    plt.close(fig)

    print(f"  -> wrote overall async-only plot: {out_png}")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    root = Path("").resolve()
    print("[INFO] Root:", root)

    levels = [50, 100, 150, 200]
    level_loop_data = {}

    for lvl in levels:
        level_dir = root / f"Mandelbrot{lvl}"
        if not level_dir.is_dir():
            print(f"[WARN] Missing directory for level {lvl}: {level_dir}")
            continue

        per_loop = analyze_level(lvl, level_dir, root)
        if per_loop:
            level_loop_data[lvl] = per_loop

    if level_loop_data:
        overall_png = root / "Mandelbrot_AsyncOnly_BuboOff_Overall.png"
        create_overall_plot(level_loop_data, overall_png)
    else:
        print("[WARN] No level data; nothing to plot.")


if __name__ == "__main__":
    main()
