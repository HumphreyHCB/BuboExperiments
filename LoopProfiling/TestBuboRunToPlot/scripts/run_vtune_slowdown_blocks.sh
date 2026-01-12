#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ============================================================
# CONFIG (edit here OR pass via env from run_all.sh)
# ============================================================

# GTSlowdownSchedular repo
GTS_ROOT="${GTS_ROOT:-/home/hb478/repos/GTSlowdownSchedular}"
RESULTS_DIR="${RESULTS_DIR:-${GTS_ROOT}/Tests/TestResults}"

# Benchmark selection (must match how SlowdownTest names its output)
BENCHMARK="${BENCHMARK:-LoopBenchmarks}"
SUITE="${SUITE:-AWFY}"
PROBE_MODE="${PROBE_MODE:-WithProbe}"

# Tag used by SlowdownTest when it writes results
TAG="${TAG:-AUTO_PIPELINE}"

# Iterations to pass into SlowdownTest.run(...). If empty, we fall back to a default.
ITER="${ITER:-}"

# Where to place the pipeline’s copy of the newest result
RAW_VTUNE_DIR="${RAW_VTUNE_DIR:-${ROOT_DIR}/rawdata/vtune/${SUITE}/${BENCHMARK}/${PROBE_MODE}}"
OUT_TXT="${OUT_TXT:-${RAW_VTUNE_DIR}/slowdown_blocks.txt}"

# Optional: control compilation output folder (inside GTS_ROOT)
BUILD_DIR="${BUILD_DIR:-build_pipeline}"

mkdir -p "${RAW_VTUNE_DIR}"

echo "[INFO] Building + running SlowdownTest from repo root: ${GTS_ROOT}"
echo "       Results: ${RESULTS_DIR}"
echo "       Tag: ${TAG}"
echo "       SUITE: ${SUITE}"
echo "       BENCHMARK: ${BENCHMARK}"
echo "       PROBE_MODE: ${PROBE_MODE}"
echo "       Output: ${OUT_TXT}"

# ---- Find the JSON jar anywhere in the repo ----
JSON_JAR="$(find "${GTS_ROOT}" -type f -name "org.json-1.6-20240205.jar" 2>/dev/null | head -n 1 || true)"
if [[ -z "${JSON_JAR}" ]]; then
  echo "[ERROR] Could not find org.json-1.6-20240205.jar anywhere under: ${GTS_ROOT}"
  exit 1
fi
echo "[INFO] Using JSON jar: ${JSON_JAR}"

pushd "${GTS_ROOT}" >/dev/null

echo "[STEP] Compiling ALL Java sources in GTSlowdownSchedular ..."
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

# This matches what you said you normally do, but puts classes in ${BUILD_DIR}/
javac \
  -cp ".:${JSON_JAR}" \
  -d "${BUILD_DIR}" \
  $(find . -name "*.java" -not -path "./${BUILD_DIR}/*")

# ---- Determine main class (package-aware) ----
SLOWDOWN_SRC="${GTS_ROOT}/Tests/SlowdownTest.java"
if [[ ! -f "${SLOWDOWN_SRC}" ]]; then
  echo "[ERROR] Cannot find SlowdownTest.java at: ${SLOWDOWN_SRC}"
  exit 1
fi

pkg="$(grep -E '^\s*package\s+' "${SLOWDOWN_SRC}" | head -n 1 | sed -E 's/^\s*package\s+([^;]+)\s*;.*/\1/')"
if [[ -n "${pkg}" ]]; then
  MAIN_CLASS="${pkg}.SlowdownTest"
else
  MAIN_CLASS="SlowdownTest"
fi

# ------------------------------------------------------------
# IMPORTANT:
# SlowdownTest.main(args) now expects:
#   args[0] = path      (folder containing Final_<BENCHMARK>.json)
#   args[1] = benchmark
#   args[2] = iterations
#
# We build the exact folder that contains the slowdown files:
#   ${GTS_ROOT}/FinalBuboTests/${PROBE_MODE}/${SUITE}/${BENCHMARK}
# ------------------------------------------------------------
SLOWDOWN_DIR="${GTS_ROOT}/FinalBuboTests/${PROBE_MODE}/${SUITE}"


echo "[STEP] Running ${MAIN_CLASS} ..."
echo "       path       = ${SLOWDOWN_DIR}"
echo "       benchmark  = ${BENCHMARK}"
echo "       iterations = ${ITER}"
java -cp "${BUILD_DIR}:${JSON_JAR}" "${MAIN_CLASS}" "${SLOWDOWN_DIR}" "${BENCHMARK}" "${ITER}"

popd >/dev/null

# ---- Copy newest matching result into pipeline rawdata ----
if [[ ! -d "${RESULTS_DIR}" ]]; then
  echo "[ERROR] Results directory not found: ${RESULTS_DIR}"
  exit 1
fi

# Match the file for *this* benchmark (and the tag)
latest="$(ls -t "${RESULTS_DIR}"/*_"${BENCHMARK}"_SlowdownTest_*"${TAG}"*.txt 2>/dev/null | head -n 1 || true)"
if [[ -z "${latest}" ]]; then
  echo "[ERROR] Could not find any result file matching:"
  echo "        ${RESULTS_DIR}/*_${BENCHMARK}_SlowdownTest_*${TAG}*.txt"
  echo "        Check SlowdownTest identifer/tag and benchmark and that it wrote a result."
  exit 1
fi

echo "[STEP] Copying newest result:"
echo "       ${latest}"
cp -f "${latest}" "${OUT_TXT}"

echo "[OK] Wrote: ${OUT_TXT}"
