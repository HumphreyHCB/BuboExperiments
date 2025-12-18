#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GTS_ROOT="/home/hb478/repos/GTSlowdownSchedular"
RESULTS_DIR="${GTS_ROOT}/Tests/TestResults"

RAW_VTUNE_DIR="${ROOT_DIR}/rawdata/vtune"
OUT_TXT="${RAW_VTUNE_DIR}/slowdown_blocks.txt"

TAG="AUTO_PIPELINE"

mkdir -p "${RAW_VTUNE_DIR}"

echo "[INFO] Building + running SlowdownTest from repo root: ${GTS_ROOT}"
echo "       Results: ${RESULTS_DIR}"
echo "       Tag: ${TAG}"

# ---- Find the JSON jar anywhere in the repo ----
JSON_JAR="$(find "${GTS_ROOT}" -type f -name "org.json-1.6-20240205.jar" 2>/dev/null | head -n 1 || true)"
if [[ -z "${JSON_JAR}" ]]; then
  echo "[ERROR] Could not find org.json-1.6-20240205.jar anywhere under: ${GTS_ROOT}"
  exit 1
fi
echo "[INFO] Using JSON jar: ${JSON_JAR}"

pushd "${GTS_ROOT}" >/dev/null

echo "[STEP] Compiling ALL Java sources in GTSlowdownSchedular ..."
rm -rf build_pipeline
mkdir -p build_pipeline

# This matches what you said you normally do, but puts classes in build_pipeline/
javac \
  -cp ".:${JSON_JAR}" \
  -d build_pipeline \
  $(find . -name "*.java" -not -path "./build_pipeline/*")

echo "[STEP] Running SlowdownTest ..."
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

echo "[STEP] Running ${MAIN_CLASS} ..."
java -cp "build_pipeline:${JSON_JAR}" "${MAIN_CLASS}"


popd >/dev/null

# ---- Copy newest matching result into pipeline rawdata ----
if [[ ! -d "${RESULTS_DIR}" ]]; then
  echo "[ERROR] Results directory not found: ${RESULTS_DIR}"
  exit 1
fi

latest="$(ls -t "${RESULTS_DIR}"/*_LoopBenchmarks_SlowdownTest_*"${TAG}"*.txt 2>/dev/null | head -n 1 || true)"
if [[ -z "${latest}" ]]; then
  echo "[ERROR] Could not find any result file matching:"
  echo "        ${RESULTS_DIR}/*_LoopBenchmarks_SlowdownTest_*${TAG}*.txt"
  echo "        Check SlowdownTest.main() tag and that it wrote a result."
  exit 1
fi

echo "[STEP] Copying newest result:"
echo "       ${latest}"
cp -f "${latest}" "${OUT_TXT}"

echo "[OK] Wrote: ${OUT_TXT}"
