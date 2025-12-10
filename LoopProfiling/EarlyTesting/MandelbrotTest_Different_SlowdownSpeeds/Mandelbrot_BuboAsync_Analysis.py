#!/usr/bin/env python3

from pathlib import Path
import re
import csv
import matplotlib.pyplot as plt

# ---------- Regexes and parsers (same as before) ----------

TOTAL_RUNTIME_RE = re.compile(r"Total Runtime:\s+(\d+)us")
COMP_RE = re.compile(r"^Comp\s+(\d+)\s+\((.+?)\)\s+loops:")
LOOP_RE = re.compile(r"loop\s+(\d+)\s+Cycles:\s+(\d+)")
LOOP_CALL_RE = re.compile(r"LoopCallCount:\s*(\d+)")
ENCODING_RE = re.compile(r"Found Encoding\s*:\s*(.*)")
HARNESS_AVG_RE = re.compile(r"average:\s+(\d+)us\s+total:\s+(\d+)us")

TOTAL_SAMPLES_RE = re.compile(r"^Total samples\s*:\s*(\d+)")
BLOCK_HEADER_RE = re.compile(
    r"^---\s+(\d+)\s+ns\s+\(([0-9.]+)%\),\s+(\d+)\s+samples"
)
FRAME_RE = re.compile(r"^\s*\[\s*\d+\s*\]\s+(.*)$")

MARKER_DELIM = "BuboAgentCompilerMarkers.MarkerDelimiter"
MARKER_RE = re.compile(r"BuboAgentCompilerMarkers\.Marker(\d+)\b")

MAX_LOOPS = 40
COVERAGE_THRESHOLD = 99.0  # percent of baseline Bubo coverage


def parse_bubo_file(path: Path):
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


def extract_average_runtime_us(path: Path):
    avg = None
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = HARNESS_AVG_RE.search(line)
            if m:
                try:
                    avg = int(m.group(1))
                except ValueError:
                    continue
    return avg


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
        b = funcs[d - 1]
        mb = MARKER_RE.search(b)
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


# ---------- Plotting helpers ----------

def create_level_plot(level_label, rows, out_png: Path, overhead_percent=None):
    if not rows:
        print(f"  [WARN] No loops to plot for level {level_label}")
        return

    k = len(rows)
    x = list(range(k))
    width = 0.35

    bubo_changes = [r["BuboPercentChange"] for r in rows]
    async_changes = [r["AsyncPercentChange"] for r in rows]

    fig_width = max(10, k * 0.6)
    fig, ax = plt.subplots(figsize=(fig_width, 6))

    x_bubo = [i - width / 2 for i in x]
    x_async = [i + width / 2 for i in x]

    ax.bar(x_bubo, bubo_changes, width=width, label="Bubo (% change cycles)")
    ax.bar(x_async, async_changes, width=width, label="Async (% change samples)")

    if overhead_percent is not None:
        ax.axhline(
            overhead_percent,
            linestyle="--",
            linewidth=1.5,
            label=f"Runtime overhead {overhead_percent:+.1f}%",
        )

    labels = []
    for r in rows:
        comp = r["CompId"]
        loop = r["LoopId"]
        b_slow_share = r.get("BuboSlowdownSharePercent", 0.0)
        a_slow_share = r.get("AsyncSlowdownSharePercent", 0.0)

        line1 = f"B {b_slow_share:.1f}% / A {a_slow_share:.1f}%"
        line2 = f"C{comp}-L{loop}"
        labels.append(line1 + "\n" + line2)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    for lbl in ax.get_xticklabels():
        lbl.set_fontsize(8)
        lbl.set_rotation(45)

    ax.set_ylabel("Percent change vs baseline [%]")
    ax.set_xlabel(f"Mandelbrot, level {level_label}")
    ax.set_title(f"Mandelbrot – level {level_label}: Bubo vs Async")

    ax.legend()
    fig.tight_layout()
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    print(f"  -> wrote level plot: {out_png}")


def create_async_only_plot(level_label, rows, out_png: Path):
    if not rows:
        print(f"  [WARN] No async-only rows to plot for level {level_label}")
        return

    k = len(rows)
    x = list(range(k))
    async_changes = [r["AsyncPercentChange_NoBubo"] for r in rows]

    fig_width = max(10, k * 0.6)
    fig, ax = plt.subplots(figsize=(fig_width, 6))

    ax.bar(x, async_changes)

    labels = [f"C{r['CompId']}-L{r['LoopId']}" for r in rows]
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    for lbl in ax.get_xticklabels():
        lbl.set_fontsize(8)
        lbl.set_rotation(45)

    ax.set_ylabel("Async percent change vs no-Bubo baseline [%]")
    ax.set_xlabel(f"Mandelbrot, level {level_label} – Async only (Bubo OFF)")
    ax.set_title(
        f"Mandelbrot – level {level_label}: Async-only change\n"
        "(NoSlowdown_BuboOff -> Slowdown_BuboOff)"
    )

    fig.tight_layout()
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    print(f"  -> wrote async-only plot: {out_png}")


def create_overall_plot(level_rows_map, out_png: Path):
    levels = sorted(level_rows_map.keys())
    if not levels:
        print("  [WARN] No level data for overall plot.")
        return

    per_level_maps = {}
    for lvl, rows in level_rows_map.items():
        m = {}
        for r in rows:
            m[(r["CompId"], r["LoopId"])] = r
        per_level_maps[lvl] = m

    common_keys = None
    for lvl in levels:
        keys = set(per_level_maps[lvl].keys())
        common_keys = keys if common_keys is None else (common_keys & keys)

    if not common_keys:
        print("  [WARN] No common (CompId, LoopId) across all levels, skipping overall plot.")
        return

    common_keys = sorted(common_keys)

    fig, ax = plt.subplots(figsize=(10, 6))

    x_positions = list(range(len(levels)))
    x_labels = [str(lvl) for lvl in levels]

    for (comp_id, loop_id) in common_keys:
        bubo_vals = []
        async_vals = []
        for lvl in levels:
            row = per_level_maps[lvl][(comp_id, loop_id)]
            bubo_vals.append(row["BuboPercentChange"])
            async_vals.append(row["AsyncPercentChange"])

        ax.plot(
            x_positions,
            bubo_vals,
            marker="o",
            linestyle="-",
            label=f"C{comp_id}-L{loop_id} Bubo",
        )
        ax.plot(
            x_positions,
            async_vals,
            marker="x",
            linestyle="--",
            label=f"C{comp_id}-L{loop_id} Async",
        )

    ax.set_xticks(x_positions)
    ax.set_xticklabels(x_labels)
    ax.set_xlabel("Slowdown level")
    ax.set_ylabel("Percent change vs baseline [%]")
    ax.set_title(
        "Mandelbrot – Bubo vs Async across slowdown levels\n"
        "(loops common to all levels, Bubo ON slowdown)"
    )

    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    print(f"  -> wrote overall plot: {out_png}")


# ---------- Per-level analysis ----------

def analyze_level(level: int, level_dir: Path, out_root: Path):
    lvl_str = str(level)
    print(f"[INFO] Analyzing Mandelbrot level {lvl_str} in {level_dir}")

    # Bubo + Async (Bubo ON)
    bubo_base_path = level_dir / f"Mandelbrot_{lvl_str}_NoSlowdown_BuboOn.out"
    bubo_slow_path = level_dir / f"Mandelbrot_{lvl_str}_Slowdown_BuboOn.out"
    async_base_path = level_dir / f"Mandelbrot_{lvl_str}_NoSlowdown_BuboOn_GTAssignDebug.txt"
    async_slow_on_path = level_dir / f"Mandelbrot_{lvl_str}_Slowdown_BuboOn_GTAssignDebug.txt"

    # Async-only (Bubo OFF): new baseline + slowdown
    async_base_nobubo_path = level_dir / f"Mandelbrot_{lvl_str}_NoSlowdown_BuboOff_GTAssignDebug.txt"
    async_slow_off_path = level_dir / f"Mandelbrot_{lvl_str}_Slowdown_BuboOff_GTAssignDebug.txt"

    for p in [bubo_base_path, bubo_slow_path, async_base_path, async_slow_on_path]:
        if not p.exists():
            print(f"  [WARN] Missing file: {p}, skipping this level.")
            return []

    baseline_avg = extract_average_runtime_us(bubo_base_path)
    slowdown_avg = extract_average_runtime_us(bubo_slow_path)

    overhead_percent = None
    if baseline_avg is not None and slowdown_avg is not None and baseline_avg > 0:
        overhead_percent = (slowdown_avg - baseline_avg) / float(baseline_avg) * 100.0
        print(
            f"  [INFO] Runtime averages (us): "
            f"baseline={baseline_avg}, slowdown={slowdown_avg}, "
            f"overhead={overhead_percent:+.2f}%"
        )
    else:
        print("  [WARN] Could not compute overhead (missing or invalid averages).")

    # --- Bubo baseline + slowdown ---
    _, loops_base, parents_base, loop_calls_base = parse_bubo_file(bubo_base_path)
    exclusive_base = compute_exclusive_cycles(loops_base, parents_base)

    bubo_baseline_map = {}
    for (comp_id, method, loop_id), cycles in exclusive_base.items():
        call_count = loop_calls_base.get((comp_id, loop_id), None)
        if call_count == 0:
            bubo_baseline_map[(comp_id, loop_id)] = (method, cycles)

    if not bubo_baseline_map:
        print("  [WARN] No baseline Bubo loops with LoopCallCount == 0, skipping this level.")
        return []

    bubo_slow_map = {}
    _, loops_slow, parents_slow, _ = parse_bubo_file(bubo_slow_path)
    exclusive_slow = compute_exclusive_cycles(loops_slow, parents_slow)
    for (comp_id, method, loop_id), cycles in exclusive_slow.items():
        if (comp_id, loop_id) in bubo_baseline_map:
            base_method, _ = bubo_baseline_map[(comp_id, loop_id)]
            bubo_slow_map[(comp_id, loop_id)] = (base_method, cycles)

    # --- Async baseline + slowdown (Bubo ON) ---
    _, async_baseline_map = parse_async_marker_file(async_base_path)
    _, async_slow_on_map = parse_async_marker_file(async_slow_on_path)

    if not async_baseline_map:
        print("  [WARN] No async baseline samples found, skipping this level.")
        return []

    common_keys = sorted(
        set(bubo_baseline_map.keys()) & set(async_baseline_map.keys()),
        key=lambda k: bubo_baseline_map[k][1],
        reverse=True,
    )
    if not common_keys:
        print("  [WARN] No common (CompId, LoopId) between Bubo(LoopCallCount==0) and Async baseline.")
        return []

    total_bubo_base = sum(bubo_baseline_map[k][1] for k in common_keys)
    if total_bubo_base == 0:
        print("  [WARN] Zero total baseline Bubo at this level, skipping.")
        return []

    total_bubo_slow_all = sum(bubo_slow_map.get(k, (None, 0))[1] for k in common_keys)
    total_async_slow_all = sum(async_slow_on_map.get(k, 0) for k in common_keys)

    selected_rows = []
    coverage = 0.0

    for i, (comp_id, loop_id) in enumerate(common_keys):
        if i >= MAX_LOOPS:
            break

        method, b_cycles_base = bubo_baseline_map[(comp_id, loop_id)]
        _, b_cycles_slow = bubo_slow_map.get((comp_id, loop_id), (method, 0))

        a_samples_base = async_baseline_map[(comp_id, loop_id)]
        a_samples_slow_on = async_slow_on_map.get((comp_id, loop_id), 0)

        if b_cycles_base > 0:
            bubo_pct_change = (b_cycles_slow - b_cycles_base) / float(b_cycles_base) * 100.0
        else:
            bubo_pct_change = 0.0

        if a_samples_base > 0:
            async_pct_change = (a_samples_slow_on - a_samples_base) / float(a_samples_base) * 100.0
        else:
            async_pct_change = 0.0

        if total_bubo_slow_all > 0:
            b_slow_share = (b_cycles_slow / float(total_bubo_slow_all)) * 100.0
        else:
            b_slow_share = 0.0

        if total_async_slow_all > 0:
            a_slow_share = (a_samples_slow_on / float(total_async_slow_all)) * 100.0
        else:
            a_slow_share = 0.0

        b_share_base = (b_cycles_base / float(total_bubo_base)) * 100.0

        row = {
            "Level": level,
            "CompId": comp_id,
            "LoopId": loop_id,
            "Method": method,
            "BuboExclusiveCyclesBaseline": b_cycles_base,
            "BuboExclusiveCyclesSlowdown": b_cycles_slow,
            "AsyncSamplesBaseline_BuboOn": a_samples_base,
            "AsyncSamplesSlowdown_BuboOn": a_samples_slow_on,
            "BuboPercentChange": bubo_pct_change,
            "AsyncPercentChange": async_pct_change,
            "BuboSlowdownSharePercent": b_slow_share,
            "AsyncSlowdownSharePercent": a_slow_share,
            "BaselineAvgUs": baseline_avg,
            "SlowdownAvgUs": slowdown_avg,
            "OverheadPercent": overhead_percent,
        }
        selected_rows.append(row)

        coverage += b_share_base
        if coverage >= COVERAGE_THRESHOLD:
            break

    if not selected_rows:
        print(f"  [WARN] No rows selected after filtering for level {level}, skipping.")
        return []

    # CSV + plot for Bubo-on comparison
    out_csv = out_root / f"Mandelbrot_{level}_BuboAsync_BuboOn.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=list(selected_rows[0].keys()),
        )
        w.writeheader()
        for r in selected_rows:
            w.writerow(r)
    print(f"  -> wrote CSV (BuboOn slowdown): {out_csv}")

    out_png = out_root / f"Mandelbrot_{level}_BuboAsync_BuboOn.png"
    create_level_plot(lvl_str, selected_rows, out_png, overhead_percent=overhead_percent)

    # ---------- Async-only (no Bubo) using new baseline ----------
    async_only_rows = []
    if async_base_nobubo_path.exists() and async_slow_off_path.exists():
        _, async_base_nobubo_map = parse_async_marker_file(async_base_nobubo_path)
        _, async_slow_off_map = parse_async_marker_file(async_slow_off_path)

        for r in selected_rows:
            key = (r["CompId"], r["LoopId"])
            a_base_nb = async_base_nobubo_map.get(key, 0)
            a_slow_nb = async_slow_off_map.get(key, 0)

            if a_base_nb > 0:
                async_pct_nb = (a_slow_nb - a_base_nb) / float(a_base_nb) * 100.0
            else:
                async_pct_nb = 0.0

            new_row = dict(r)
            new_row["AsyncSamplesBaseline_BuboOff"] = a_base_nb
            new_row["AsyncSamplesSlowdown_BuboOff"] = a_slow_nb
            new_row["AsyncPercentChange_NoBubo"] = async_pct_nb
            async_only_rows.append(new_row)

        if async_only_rows:
            async_csv = out_root / f"Mandelbrot_{level}_AsyncOnly_BuboOff.csv"
            with async_csv.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=list(async_only_rows[0].keys()),
                )
                w.writeheader()
                for rr in async_only_rows:
                    w.writerow(rr)
            print(f"  -> wrote CSV (Async-only, no Bubo): {async_csv}")

            async_png = out_root / f"Mandelbrot_{level}_AsyncOnly_BuboOff.png"
            create_async_only_plot(lvl_str, async_only_rows, async_png)
        else:
            print(f"  [WARN] No async-only rows for level {level}.")
    else:
        print(
            f"  [WARN] Missing async no-Bubo baseline/slowdown for level {level}: "
            f"{async_base_nobubo_path}, {async_slow_off_path}"
        )

    return selected_rows


# ---------- Main ----------

def main():
    root = Path("").resolve()
    print("[INFO] Root:", root)

    levels = [50, 100, 150, 200]
    level_rows_map = {}

    for lvl in levels:
        lvl_dir = root / f"Mandelbrot{lvl}"
        if not lvl_dir.is_dir():
            print(f"[WARN] Missing directory for level {lvl}: {lvl_dir}, skipping.")
            continue

        rows = analyze_level(lvl, lvl_dir, root)
        if rows:
            level_rows_map[lvl] = rows

    if level_rows_map:
        overall_png = root / "Mandelbrot_AllLevels_BuboAsync_Overall.png"
        create_overall_plot(level_rows_map, overall_png)
    else:
        print("[WARN] No per-level data, skipping overall plot.")


if __name__ == "__main__":
    main()
