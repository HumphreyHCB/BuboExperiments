#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ALL="${ROOT_DIR}/run_all.sh"

# Defaults (override via env)
SUITE="${SUITE:-AWFY}"
MODE="${MODE:-WithoutProbe}"
TAG="${TAG:-AUTO_PIPELINE}"
PROFILERS="${PROFILERS:-cfg,vtune,async,jfr}"

# Default iteration policy
ITER_DEFAULT="${ITER_DEFAULT:-500}"
ITER_LOOPBENCH="${ITER_LOOPBENCH:-12000}"

AWFY_BENCHES=(
  Bounce
  CD
  Json
  Mandelbrot
  NBody
  Sieve
)

echo "[RUN_EVERYTHING] suite=${SUITE} mode=${MODE} tag=${TAG} profilers=${PROFILERS}"
echo "[RUN_EVERYTHING] default_iter=${ITER_DEFAULT} loopbench_iter=${ITER_LOOPBENCH}"
echo

if [[ ! -f "${RUN_ALL}" ]]; then
  echo "[ERROR] Cannot find run_all.sh at: ${RUN_ALL}" >&2
  exit 1
fi

# -------------------------
# AWFY main set
# -------------------------
for BENCH in "${AWFY_BENCHES[@]}"; do
  echo
  echo "---- ${SUITE} / ${BENCH} (ITER=${ITER_DEFAULT}) ----"
  bash "${RUN_ALL}" \
    --benchmark "${BENCH}" \
    --suite "${SUITE}" \
    --mode "${MODE}" \
    --iter "${ITER_DEFAULT}" \
    --tag "${TAG}" \
    --profilers "${PROFILERS}"
done

# -------------------------
# LoopBenchmarks special
# -------------------------
echo
echo "---- ${SUITE} / LoopBenchmarks (ITER=${ITER_LOOPBENCH}) ----"
bash "${RUN_ALL}" \
  --benchmark "LoopBenchmarks" \
  --suite "${SUITE}" \
  --mode "${MODE}" \
  --iter "${ITER_LOOPBENCH}" \
  --tag "${TAG}" \
  --profilers "${PROFILERS}"

echo
echo "[DONE] run_all_everything"
