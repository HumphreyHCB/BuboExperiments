#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"

# ---- Scripts ----
BUILD_VTUNE="${ROOT_DIR}/scripts/build_total_pct_slowdown_per_loop.py"
PLOT_SCRIPT="${ROOT_DIR}/scripts/plot_loopbenchmarks.py"

# ---- Raw inputs (fixed) ----
DEBUG_OUT="${ROOT_DIR}/rawdata/cfg/LoopBenchmarks_baseline_withBubo.out"
SLOWDOWN_TXT="${ROOT_DIR}/rawdata/vtune/slowdown_blocks.txt"

BUBO_BASELINE="${ROOT_DIR}/rawdata/bubo/LoopBenchmarks_baseline_withBubo.out"
BUBO_SLOWDOWN="${ROOT_DIR}/rawdata/bubo/LoopBenchmarks_slowdown_withBubo.out"

OVERHEADS="${ROOT_DIR}/rawdata/overheads/cycles_overhead.csv"

# ---- Source-of-truth bridge JSON ----
BRIDGE_SRC="/home/hb478/repos/GTSlowdownSchedular/FinalBuboTests/LoopBenchmarks/Final_LoopBenchmarks.json"

# ---- Outputs ----
PROCESSED_DIR="${ROOT_DIR}/processed"
BRIDGE_DST="${PROCESSED_DIR}/vtune/Final_LoopBenchmarks.json"

# echo "[INFO] Root: ${ROOT_DIR}"

# # ---- Sanity checks ----
# [[ -f "${BUILD_VTUNE}" ]] || { echo "[ERROR] Missing ${BUILD_VTUNE}"; exit 1; }
# [[ -f "${PLOT_SCRIPT}" ]] || { echo "[ERROR] Missing ${PLOT_SCRIPT}"; exit 1; }

# [[ -f "${DEBUG_OUT}" ]] || { echo "[ERROR] Missing ${DEBUG_OUT}"; exit 1; }
# [[ -f "${SLOWDOWN_TXT}" ]] || { echo "[ERROR] Missing ${SLOWDOWN_TXT}"; exit 1; }

# [[ -f "${BUBO_BASELINE}" ]] || { echo "[ERROR] Missing ${BUBO_BASELINE}"; exit 1; }
# [[ -f "${BUBO_SLOWDOWN}" ]] || { echo "[ERROR] Missing ${BUBO_SLOWDOWN}"; exit 1; }

# [[ -f "${OVERHEADS}" ]] || { echo "[ERROR] Missing ${OVERHEADS}"; exit 1; }

# [[ -f "${BRIDGE_SRC}" ]] || { echo "[ERROR] Missing ${BRIDGE_SRC}"; exit 1; }

# mkdir -p "${PROCESSED_DIR}/cfg" "${PROCESSED_DIR}/vtune" "${ROOT_DIR}/plots"



echo "[STEP 0] Producing raw Bubo + CFG outputs into rawdata/"
"${ROOT_DIR}/scripts/run_bubo_and_cfg.sh"

echo "[STEP 0.5] Producing VTune slowdown block-times file into rawdata/vtune/"
"${ROOT_DIR}/scripts/run_vtune_slowdown_blocks.sh"

# ---- Stage 1: Build processed VTune per-loop totals ----
echo "[STEP 1] Copying latest bridge JSON"
cp -f "${BRIDGE_SRC}" "${BRIDGE_DST}"

MARKERPHASE_JSON="/home/hb478/repos/GTSlowdownSchedular/FinalBuboTests/LoopBenchmarks/result.json"

echo "[STEP 1] Building processed/vtune/total_pct_slowdown_per_loop.csv"
"${PY}" "${BUILD_VTUNE}" \
  --debug-out "${DEBUG_OUT}" \
  --slowdown-txt "${SLOWDOWN_TXT}" \
  --block-id-is-vtune \
  --bridge-json "${BRIDGE_DST}" \
  --markerphase-json "${MARKERPHASE_JSON}" \
  --processed-dir "${PROCESSED_DIR}" \
  --min-normal 1e-9

# ---- Stage 2: Plot ----
echo "[STEP 2] Generating final plot into plots/"
"${PY}" "${PLOT_SCRIPT}"

echo
echo "[DONE]"
echo "Key outputs:"
echo "  processed/vtune/total_pct_slowdown_per_loop.csv"
echo "  plots/LoopBenchmarks_bubo_loops.png"
echo "  plots/LoopBenchmarks_bubo_loops.csv"
