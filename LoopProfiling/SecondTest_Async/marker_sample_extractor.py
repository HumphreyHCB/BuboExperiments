#!/usr/bin/env python3

"""
Async-profiler marker extractor + slowdown analyser.

For each benchmark directory inside:
    LoopProfiling/SecondTest_Async/

we produce:

  1. One CSV for each async-profiler txt
       <file>.txt.csv

  2. One combined slowdown CSV:
       loop_sample_percent_change.csv

  3. One plot:
       slowdown_percent_plot.png

Baseline  = LIR_false_GTAssignDebug_true.txt
Slowdown  = LIR_true_GTAssignDebug_true.txt
"""

from pathlib import Path
import re
import csv
import math
import matplotlib.pyplot as plt

# ======================================================================
#   Regular expressions and common parser utils
# ======================================================================

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
            return int(m.group(1))
    return None


def iter_blocks(lines):
    """Yield (header_line, frame_lines)."""
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
    return int(m.group(3))


def extract_marker_ids(frame_lines):
    """
    Extract (comp_id, loop_id) from stack frames:

      [0] MarkerA
      [1] MarkerDelimiter
      [2] MarkerX
      [3] MarkerY
      [4] MarkerZ
      ...
    """
    funcs = []
    for fl in frame_lines:
        m = FRAME_RE.match(fl)
        if m:
            funcs.append(m.group(1))

    # delimiter index
    try:
        d = funcs.index(MARKER_DELIM)
    except ValueError:
        return None

    # LO0P ID = marker *before* delimiter
    loop_id = None
    if d > 0:
        b = funcs[d - 1]
        mb = MARKER_RE.search(b)
        if mb:
            loop_id = int(mb.group(1))

    if loop_id is None:
        return None

    # comp digits = markers after delimiter
    digits = []
    for f in funcs[d + 1 :]:
        mm = MARKER_RE.search(f)
        if not mm:
            break
        digits.append(mm.group(1))

    if not digits:
        return None

    comp_id = int("".join(reversed(digits)))
    return (comp_id, loop_id)


def parse_marker_samples(lines):
    total = parse_total_samples(lines)
    results = {}

    for hdr, frames in iter_blocks(lines):
        block_samples = parse_block_samples(hdr)
        ids = extract_marker_ids(frames)
        if ids is None:
            continue

        results[ids] = results.get(ids, 0) + block_samples

    return total, results


# ======================================================================
#   Per-file CSV writer
# ======================================================================

def write_file_csv(txt_path, total_samples, samples_per_pair):
    out_path = txt_path.with_suffix(txt_path.suffix + ".csv")
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["comp_id", "loop_id", "samples", "percentage", "total_samples"])
        for (comp_id, loop_id), s in sorted(samples_per_pair.items()):
            pct = (s / total_samples * 100.0) if total_samples else 0.0
            w.writerow([comp_id, loop_id, s, f"{pct:.6f}", total_samples])

    print(f"  -> wrote per-file csv: {out_path.name}")


# ======================================================================
#   Benchmark-level analysis (baseline vs slowdown)
# ======================================================================

def percent_change(base, new):
    if base == 0:
        return float("nan")
    return (new - base) / float(base) * 100.0


def create_benchmark_plot(bench_name, labels, pct_values, total_pct, core_mask, out_path):
    x = list(range(len(labels)))
    fig_width = max(10, len(labels) * 0.6)
    fig, ax = plt.subplots(figsize=(fig_width, 5))

    colors = ["red" if c else "tab:blue" for c in core_mask]
    ax.bar(x, pct_values, color=colors)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("% change in samples")

    if not math.isnan(total_pct):
        ax.axhline(total_pct, linestyle="--",
                   label=f"Total change: {total_pct:.1f}%")
        ax.legend()

    ax.set_title(f"{bench_name} – Sample % Change (sorted by baseline samples)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)

    print(f"  -> wrote plot {out_path.name}")


def analyze_benchmark(bench_dir):
    print(f"[INFO] Analysing benchmark: {bench_dir.name}")

    # autodetect baseline + slowdown txt files
    base_file = None
    slow_file = None

    for f in bench_dir.iterdir():
        if f.suffix == ".txt" and "GTAssignDebug_true" in f.name:
            if "LIR_false" in f.name:
                base_file = f
            elif "LIR_true" in f.name:
                slow_file = f

    if not base_file or not slow_file:
        print("  [WARN] Missing baseline or slowdown files")
        return

    # parse both
    base_lines = base_file.read_text().splitlines()
    slow_lines = slow_file.read_text().splitlines()

    base_total, base_map = parse_marker_samples(base_lines)
    slow_total, slow_map = parse_marker_samples(slow_lines)

    # merge per-file CSV
    write_file_csv(base_file, base_total, base_map)
    write_file_csv(slow_file, slow_total, slow_map)

    # keys in both:
    common_keys = sorted(
        (set(base_map.keys()) & set(slow_map.keys())),
        key=lambda k: base_map[k],
        reverse=True
    )
    if not common_keys:
        print("  [WARN] No common comp/loop IDs")
        return

    total_base_samples = sum(base_map[k] for k in common_keys)
    total_pct = percent_change(base_total, slow_total)

    rows = []
    labels = []
    pct_values = []
    shares = []

    for (comp_id, loop_id) in common_keys:
        base = base_map[(comp_id, loop_id)]
        slow = slow_map[(comp_id, loop_id)]
        pct = percent_change(base, slow)

        share = (base / total_base_samples * 100.0) if total_base_samples else float("nan")
        shares.append(share)

        rows.append({
            "CompId": comp_id,
            "LoopId": loop_id,
            "BaselineSamples": base,
            "SlowdownSamples": slow,
            "PercentChangeSamples": pct,
            "BaselineSharePercent": share,
        })

        label = f"C{comp_id}-L{loop_id}\n({share:.1f}%)" if not math.isnan(share) else f"C{comp_id}-L{loop_id}"
        labels.append(label)
        pct_values.append(pct)

    # top 95% mask
    core_mask = []
    cum = 0.0
    for s in shares:
        core_mask.append(cum < 95.0)
        cum += s

    # write benchmark-level CSV
    out_csv = bench_dir / "loop_sample_percent_change.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "CompId", "LoopId",
            "BaselineSamples", "SlowdownSamples",
            "PercentChangeSamples",
            "BaselineSharePercent",
        ])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"  -> wrote {out_csv.name}")

    # write plot
    out_png = bench_dir / "slowdown_percent_plot.png"
    create_benchmark_plot(bench_dir.name, labels, pct_values, total_pct, core_mask, out_png)


# ======================================================================
#   Top-level main: process every benchmark directory
# ======================================================================

def main():
    root = Path(".")   # run script from LoopProfiling/SecondTest_Async/
    for bench_dir in sorted(root.iterdir()):
        if bench_dir.is_dir() and not bench_dir.name.startswith("."):
            analyze_benchmark(bench_dir)


if __name__ == "__main__":
    main()
