#!/usr/bin/env python3
import argparse
import csv
import os
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, List

import matplotlib.pyplot as plt

Key2 = Tuple[int, int]  # (comp_id, loop_id)


def safe_pct_increase(normal: float, slow: float) -> float:
    if normal <= 0.0:
        return 0.0
    return ((slow - normal) / normal) * 100.0


def norm_method(s: str) -> str:
    return (s or "").strip().replace("::", ".")


def load_vtune_loop_pct(path: str) -> Dict[Key2, float]:
    if not os.path.isfile(path):
        raise SystemExit(f"[ERROR] VTune CSV missing: {path}")

    out: Dict[Key2, float] = {}
    rows = 0
    bad = 0

    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.DictReader(f)
        need = {"comp_id", "loop_id", "median_pct_slowdown"}
        if not need.issubset(set(r.fieldnames or [])):
            raise SystemExit(
                f"[ERROR] VTune CSV missing columns {sorted(need)}\nFound: {r.fieldnames}"
            )

        for row in r:
            rows += 1
            try:
                comp_id = int((row.get("comp_id") or "").strip())
                loop_id = int((row.get("loop_id") or "").strip())
                pct = float((row.get("median_pct_slowdown") or "").strip())
            except Exception:
                bad += 1
                continue

            out[(comp_id, loop_id)] = pct

    print(f"[VTUNE] Loaded {len(out)} keys (comp_id,loop_id) from {path} (rows={rows}, bad={bad})")
    return out


@dataclass
class RunLoopRow:
    comp_id: int
    loop_id: int
    method: str
    samples: float
    runtime_share_pct: float


def load_profile_csv(path: str, label: str) -> Dict[Key2, RunLoopRow]:
    if not os.path.isfile(path):
        raise SystemExit(f"[ERROR] {label} CSV missing: {path}")

    rows = 0
    bad = 0

    # If duplicates exist for same (comp_id, loop_id), keep the row with largest runtime_share_pct
    out: Dict[Key2, RunLoopRow] = {}
    dup = 0

    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.DictReader(f)
        need = {"method", "comp_id", "loop_id", "samples", "runtime_share_pct"}
        if not need.issubset(set(r.fieldnames or [])):
            raise SystemExit(
                f"[ERROR] {label} CSV missing columns {sorted(need)}\nFound: {r.fieldnames}"
            )

        for row in r:
            rows += 1
            try:
                method = norm_method(row.get("method") or "")
                comp_s = (row.get("comp_id") or "").strip()
                loop_s = (row.get("loop_id") or "").strip()
                if not comp_s or not loop_s:
                    raise ValueError("missing comp_id/loop_id")

                comp_id = int(comp_s)
                loop_id = int(loop_s)
                samples = float((row.get("samples") or "").strip())
                share = float((row.get("runtime_share_pct") or "").strip())
            except Exception:
                bad += 1
                continue

            k = (comp_id, loop_id)
            cur = out.get(k)
            if cur is not None:
                dup += 1
                if share <= cur.runtime_share_pct:
                    continue

            out[k] = RunLoopRow(
                comp_id=comp_id,
                loop_id=loop_id,
                method=method,
                samples=samples,
                runtime_share_pct=share
            )

    print(f"[{label}] Loaded {len(out)} keys (comp_id,loop_id) from {path} (rows={rows}, bad={bad}, dups={dup})")
    return out


def compute_tool_slowdown_pct_samples(
    no_run: Dict[Key2, RunLoopRow],
    slow_run: Dict[Key2, RunLoopRow],
    tool_name: str,
) -> Dict[Key2, float]:
    """
    Compute pct increase using SAMPLES (not runtime_share_pct):
      pct = (samples_slow - samples_no) / samples_no * 100
    """
    out: Dict[Key2, float] = {}
    missing_no = 0
    missing_slow = 0
    zero_no = 0

    keys = set(no_run.keys()) | set(slow_run.keys())
    for k in keys:
        a = no_run.get(k)
        b = slow_run.get(k)
        if a is None:
            missing_no += 1
            continue
        if b is None:
            missing_slow += 1
            continue

        no_samples = float(a.samples)
        slow_samples = float(b.samples)

        if no_samples <= 0.0:
            zero_no += 1
            continue

        out[k] = safe_pct_increase(no_samples, slow_samples)

    print(f"[{tool_name}] Computed pct_increase for {len(out)} keys using samples")
    print(f"[{tool_name}] Missing in no_slowdown: {missing_no}")
    print(f"[{tool_name}] Missing in slowdown:    {missing_slow}")
    print(f"[{tool_name}] Skipped no_samples==0:  {zero_no}")
    return out


def parse_args():
    ap = argparse.ArgumentParser(description="Plot VTune vs async vs JFR per-loop slowdown (match by comp_id+loop_id, ignore method in join).")
    ap.add_argument("--benchmark", required=True)
    ap.add_argument("--suite", required=True)
    ap.add_argument("--mode", required=True)
    ap.add_argument("--processed-dir", required=True)

    ap.add_argument("--vtune-csv", required=True)

    ap.add_argument("--async-no", required=True)
    ap.add_argument("--async-slow", required=True)

    ap.add_argument("--jfr-no", required=True)
    ap.add_argument("--jfr-slow", required=True)

    ap.add_argument("--min-share", type=float, default=2.0)
    ap.add_argument("--out-png", default=None)
    ap.add_argument("--out-csv", default=None)
    return ap.parse_args()


def main():
    args = parse_args()

    print("================================")
    print("[PLOT] VTune vs async vs JFR")
    print(f"[PLOT] benchmark: {args.benchmark}")
    print(f"[PLOT] suite:     {args.suite}")
    print(f"[PLOT] mode:      {args.mode}")
    print(f"[PLOT] processed: {args.processed_dir}")
    print(f"[PLOT] vtune:     {args.vtune_csv}")
    print(f"[PLOT] async no:  {args.async_no}")
    print(f"[PLOT] async slow:{args.async_slow}")
    print(f"[PLOT] jfr no:    {args.jfr_no}")
    print(f"[PLOT] jfr slow:  {args.jfr_slow}")
    print("================================")

    vtune_pct = load_vtune_loop_pct(args.vtune_csv)

    async_no = load_profile_csv(args.async_no, "ASYNC no_slowdown")
    async_slow = load_profile_csv(args.async_slow, "ASYNC slowdown")
    async_pct = compute_tool_slowdown_pct_samples(async_no, async_slow, "ASYNC")

    jfr_no = load_profile_csv(args.jfr_no, "JFR no_slowdown")
    jfr_slow = load_profile_csv(args.jfr_slow, "JFR slowdown")
    jfr_pct = compute_tool_slowdown_pct_samples(jfr_no, jfr_slow, "JFR")

    def slow_share(k: Key2) -> float:
        # keep using slowdown-run runtime_share_pct for filtering and sorting
        if k in async_slow:
            return async_slow[k].runtime_share_pct
        if k in jfr_slow:
            return jfr_slow[k].runtime_share_pct
        return 0.0

    keys_all = set(vtune_pct.keys()) | set(async_pct.keys()) | set(jfr_pct.keys())
    keys_plot = [k for k in keys_all if slow_share(k) >= args.min_share]
    keys_plot.sort(key=lambda k: slow_share(k), reverse=True)

    print(f"[PLOT] Total unique keys across tools: {len(keys_all)}")
    print(f"[PLOT] Keys passing min_share {args.min_share}%: {len(keys_plot)}")

    if not keys_plot:
        raise SystemExit("[WARN] No loops passed the filter. Try lowering --min-share.")

    # Method label: prefer async slowdown method, else jfr slowdown, else blank
    def label_method(k: Key2) -> str:
        if k in async_slow and async_slow[k].method:
            return async_slow[k].method
        if k in jfr_slow and jfr_slow[k].method:
            return jfr_slow[k].method
        return ""

    rows_out = []
    for (comp_id, loop_id) in keys_plot:
        k = (comp_id, loop_id)
        rows_out.append({
            "benchmark": args.benchmark,
            "suite": args.suite,
            "mode": args.mode,
            "comp_id": comp_id,
            "loop_id": loop_id,
            "method": label_method(k),
            "slow_run_share_pct": slow_share(k),
            "vtune_pct": vtune_pct.get(k, ""),
            "async_pct": async_pct.get(k, ""),
            "jfr_pct": jfr_pct.get(k, ""),
        })

    print("[PLOT] Per key values (what will be plotted)")
    for r in rows_out:
        print(
            f"  C{r['comp_id']} L{r['loop_id']} "
            f"share={r['slow_run_share_pct']:.6f}% "
            f"vtune={r['vtune_pct']} "
            f"async={r['async_pct']} "
            f"jfr={r['jfr_pct']} "
            f"method={r['method']}"
        )

    out_csv = args.out_csv or os.path.join(args.processed_dir, "plots", f"{args.benchmark}_vtune_async_jfr.csv")
    out_png = args.out_png or os.path.join(args.processed_dir, "plots", f"{args.benchmark}_vtune_async_jfr.png")
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)
    print(f"[OK] Wrote CSV: {out_csv}")

    def val_or_zero(x) -> float:
        if x == "" or x is None:
            return 0.0
        return float(x)

    labels = [
        f"C{r['comp_id']} L{r['loop_id']}\n{r['slow_run_share_pct']:.1f}%\n{r['method']}"
        for r in rows_out
    ]
    vt_vals = [val_or_zero(r["vtune_pct"]) for r in rows_out]
    as_vals = [val_or_zero(r["async_pct"]) for r in rows_out]
    jf_vals = [val_or_zero(r["jfr_pct"]) for r in rows_out]

    x = list(range(len(rows_out)))
    width = 0.26
    x_v = [i - width for i in x]
    x_a = [i for i in x]
    x_j = [i + width for i in x]

    plt.figure(figsize=(20, 14.5))
    ax = plt.gca()

    ax.bar(x_v, vt_vals, width=width, label="VTune time pct (median_pct_slowdown)")
    ax.bar(x_a, as_vals, width=width, label="async samples pct increase")
    ax.bar(x_j, jf_vals, width=width, label="JFR samples pct increase")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.set_ylabel("Percent increase (%)")
    ax.set_title(f"{args.benchmark}: VTune vs async vs JFR per-loop increase")
    ax.legend(loc="upper left")
    ax.set_ylim(0, 200)

    plt.tight_layout()
    plt.savefig(out_png, dpi=200)
    plt.close()

    print(f"[OK] Wrote PNG: {out_png}")


if __name__ == "__main__":
    main()
