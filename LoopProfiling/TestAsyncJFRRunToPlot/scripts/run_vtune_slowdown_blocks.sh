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
MODE="${MODE:-WithoutProbe}"   # renamed from PROBE_MODE to match new pipeline

# Tag used by SlowdownTest when it writes results
TAG="${TAG:-AUTO_PIPELINE}"

# Iterations to pass into SlowdownTest.run(...)
ITER="${ITER:-}"

# Where to place the pipeline copy of the newest result (tag-scoped like async/jfr)
RAW_VTUNE_RUN_DIR="${RAW_VTUNE_RUN_DIR:-${ROOT_DIR}/rawdata/vtune/${SUITE}/${BENCHMARK}/${MODE}/${TAG}}"
OUT_TXT="${OUT_TXT:-${RAW_VTUNE_RUN_DIR}/slowdown_blocks.txt}"

# Optional: control compilation output folder (inside GTS_ROOT)
BUILD_DIR="${BUILD_DIR:-build_pipeline}"

die() { echo "[ERROR] $*" >&2; exit 1; }

mkdir -p "${RAW_VTUNE_RUN_DIR}"

echo "[INFO] Building + running SlowdownTest from: ${GTS_ROOT}"
echo "       Results dir : ${RESULTS_DIR}"
echo "       Tag         : ${TAG}"
echo "       SUITE       : ${SUITE}"
echo "       BENCHMARK   : ${BENCHMARK}"
echo "       MODE        : ${MODE}"
echo "       Output      : ${OUT_TXT}"

# ---- Find the JSON jar anywhere in the repo ----
JSON_JAR="$(find "${GTS_ROOT}" -type f -name "org.json-1.6-20240205.jar" 2>/dev/null | head -n 1 || true)"
[[ -n "${JSON_JAR}" ]] || die "Could not find org.json-1.6-20240205.jar anywhere under: ${GTS_ROOT}"
echo "[INFO] Using JSON jar: ${JSON_JAR}"

pushd "${GTS_ROOT}" >/dev/null

echo "[STEP] Compiling Java sources into ${BUILD_DIR} ..."
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

# Compile everything (same as before, just safely quoted)
javac \
  -cp ".:${JSON_JAR}" \
  -d "${BUILD_DIR}" \
  $(find . -name "*.java" -not -path "./${BUILD_DIR}/*")

# ---- Determine main class (package-aware) ----
SLOWDOWN_SRC="${GTS_ROOT}/Tests/SlowdownTest.java"
[[ -f "${SLOWDOWN_SRC}" ]] || die "Cannot find SlowdownTest.java at: ${SLOWDOWN_SRC}"

pkg="$(grep -E '^\s*package\s+' "${SLOWDOWN_SRC}" | head -n 1 | sed -E 's/^\s*package\s+([^;]+)\s*;.*/\1/')"
if [[ -n "${pkg}" ]]; then
  MAIN_CLASS="${pkg}.SlowdownTest"
else
  MAIN_CLASS="SlowdownTest"
fi

# ------------------------------------------------------------
# SlowdownTest.main(args) expects:
#   args[0] = path      (folder containing Final_<BENCHMARK>.json)
#   args[1] = benchmark
#   args[2] = iterations
#
# path should be: ${GTS_ROOT}/FinalBuboTests/${MODE}/${SUITE}
# ------------------------------------------------------------
SLOWDOWN_DIR="${GTS_ROOT}/FinalBuboTests/${MODE}/${SUITE}"
[[ -d "${SLOWDOWN_DIR}" ]] || die "Slowdown dir not found: ${SLOWDOWN_DIR}"

echo "[STEP] Running ${MAIN_CLASS} ..."
echo "       path       = ${SLOWDOWN_DIR}"
echo "       benchmark  = ${BENCHMARK}"
echo "       iterations = ${ITER}"

java -cp "${BUILD_DIR}:${JSON_JAR}" "${MAIN_CLASS}" "${SLOWDOWN_DIR}" "${BENCHMARK}" "${ITER}"

popd >/dev/null

# ---- Copy newest matching result into pipeline rawdata ----
[[ -d "${RESULTS_DIR}" ]] || die "Results directory not found: ${RESULTS_DIR}"

latest="$(ls -t "${RESULTS_DIR}"/*_"${BENCHMARK}"_SlowdownTest_*"${TAG}"*.txt 2>/dev/null | head -n 1 || true)"
[[ -n "${latest}" ]] || die "Could not find any result file matching: ${RESULTS_DIR}/*_${BENCHMARK}_SlowdownTest_*${TAG}*.txt"

echo "[STEP] Copying newest result:"
echo "       ${latest}"
cp -f "${latest}" "${OUT_TXT}"

echo "[OK] Wrote: ${OUT_TXT}"
