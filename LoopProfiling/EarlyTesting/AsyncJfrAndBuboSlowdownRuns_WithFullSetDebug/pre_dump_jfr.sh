#!/usr/bin/env bash
set -euo pipefail

# Root directory containing all benchmark subdirectories.
# Default: current directory, or pass as first argument.
ROOT_DIR="${1:-.}"

# JFR command. You can override with:
#   JFR_CMD=/path/to/jfr ./pre_dump_jfr.sh
JFR_CMD="${JFR_CMD:-jfr}"

echo "[INFO] Root directory: $ROOT_DIR"
echo "[INFO] Using JFR command: $JFR_CMD"
echo

# Loop over each benchmark directory
for bench_dir in "$ROOT_DIR"/*/; do
  # Skip if it's not a directory
  [[ -d "$bench_dir" ]] || continue

  bench_name="$(basename "$bench_dir")"
  echo "[INFO] Benchmark: $bench_name"

  for mode in false true; do
    jfr_file="${bench_dir}/${bench_name}_LIR_${mode}.jfr"
    out_txt="${bench_dir}/${bench_name}_LIR_${mode}_JFR.txt"

    if [[ -f "$jfr_file" ]]; then
      echo "  [INFO] Found JFR: $(basename "$jfr_file")"
      echo "         -> dumping to: $(basename "$out_txt")"

      # You can tweak the events list if needed (e.g. add jdk.CPULoad, etc.)
      "$JFR_CMD" print --events jdk.ExecutionSample "$jfr_file" > "$out_txt"
    else
      echo "  [WARN] Missing JFR file: $(basename "$jfr_file")"
    fi
  done

  echo
done

echo "[INFO] Done."
