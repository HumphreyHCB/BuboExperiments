#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ============================================================
# CONFIG (edit here OR pass via env from run_all.sh)
# ============================================================

# Toolchain paths
JAVA_BIN="${JAVA_BIN:-/home/hb478/repos/graal-instrumentation/vm/latest_graalvm_home/bin/java}"
CP="${CP:-/home/hb478/repos/are-we-fast-yet/benchmarks/Java/benchmarks.jar}"
AGENT="${AGENT:-/home/hb478/repos/graal-instrumentation/Bubo-Agent/target/JavaAgent-1.0-SNAPSHOT-jar-with-dependencies.jar}"

# Renaissance jar (ONLY used when SUITE=Renaissance)
RENAISSANCE_JAR="${RENAISSANCE_JAR:-/home/hb478/repos/renaissance/renaissance-gpl-0.16.1.jar}"

# Benchmark selection
BENCHMARK="${BENCHMARK:-LoopBenchmarks}"   # e.g. LoopBenchmarks, Mandelbrot, scrabble, mnemonics
SUITE="${SUITE:-AWFY}"                     # AWFY | Renaissance
PROBE_MODE="${PROBE_MODE:-WithProbe}"      # WithProbe | WithoutProbe

# Iterations (Harness 2nd arg) - REQUIRED for correct behaviour (AWFY ONLY)
ITER="${ITER:-}"

# JSON containing per-benchmark extra args (Harness 3rd arg OR Renaissance -r arg)
BENCH_ARGS_JSON="${BENCH_ARGS_JSON:-/home/hb478/repos/GTSlowdownSchedular/GTResources/AWFY_Benchmarks.json}"

# GTSlowdownSchedular repo + new layout base
GTS_ROOT="${GTS_ROOT:-/home/hb478/repos/GTSlowdownSchedular}"
GTS_FINAL_DIR="${GTS_FINAL_DIR:-${GTS_ROOT}/FinalBuboTests/${PROBE_MODE}/${SUITE}/${BENCHMARK}}"

# Profiles + slowdown JSON (source-of-truth)
PROFILE_PATH="${PROFILE_PATH:-${GTS_FINAL_DIR}/${BENCHMARK}_CompilerReplay}"
SLOWDOWN_FILE="${SLOWDOWN_FILE:-${GTS_FINAL_DIR}/Final_${BENCHMARK}.json}"

# Output rawdata folders (pipeline expects these subdirs now)
RAW_BUBO_DIR="${ROOT_DIR}/rawdata/bubo/${SUITE}/${BENCHMARK}/${PROBE_MODE}"
RAW_CFG_DIR="${ROOT_DIR}/rawdata/cfg/${SUITE}/${BENCHMARK}/${PROBE_MODE}"

OUT_BUBO_BASE="${RAW_BUBO_DIR}/${BENCHMARK}_baseline_withBubo.out"
OUT_BUBO_SLOW="${RAW_BUBO_DIR}/${BENCHMARK}_slowdown_withBubo.out"
OUT_CFG_DEBUG="${RAW_CFG_DIR}/${BENCHMARK}_baseline_withBubo.out"

# ============================================================
# JVM / Graal options
# ============================================================

# NOTE: no "-cp ${CP}" here anymore; AWFY adds it in the launch sequence.
BASE_OPTS="
  -XX:+UnlockExperimentalVMOptions
  -XX:+UnlockDiagnosticVMOptions
  -XX:+EnableJVMCI
  -XX:+UseJVMCICompiler
  -XX:+UseJVMCINativeLibrary
  -XX:+DebugNonSafepoints
  -Djdk.graal.StrictProfiles=false
  -Djdk.graal.WarnAboutCodeSignatureMismatch=false
  -Djdk.graal.TrackNodeSourcePosition=true
  --enable-native-access=ALL-UNNAMED
  -XX:-TieredCompilation
  -XX:-BackgroundCompilation
  -Djdk.graal.LoopHeaderAlignment=0
  -Djdk.graal.IsolatedLoopHeaderAlignment=0
"

BUBO_OPTS="
  -Djdk.graal.BuboLIRPhase=true
  -javaagent:${AGENT}
"

DEBUGDATA_OPTS="
  -Djdk.graal.HumphreysDebugData=true
"

# ============================================================
# Helpers
# ============================================================

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

get_extra_args() {
  local suite="$1"
  local bench="$2"
  local json="$3"
  local section

  if [[ "${suite}" == "AWFY" ]]; then
    section="AWFY Benchmarks"
  elif [[ "${suite}" == "Renaissance" ]]; then
    section="Renaissance Benchmarks"
  else
    die "Unknown SUITE='${suite}'. Expected 'AWFY' or 'Renaissance'."
  fi

  [[ -f "${json}" ]] || die "BENCH_ARGS_JSON not found: ${json}"

  # Prefer jq if present; otherwise use Python
  if command -v jq >/dev/null 2>&1; then
    jq -r --arg section "${section}" --arg bench "${bench}" \
      '.[$section][$bench].extra_args // empty' "${json}"
  else
    python3 - <<PY
import json, sys
path = ${json!r}
section = ${section!r}
bench = ${bench!r}
with open(path, "r") as f:
    data = json.load(f)
v = data.get(section, {}).get(bench, {}).get("extra_args", "")
print("" if v is None else str(v))
PY
  fi
}

# ============================================================
# Sanity checks
# ============================================================

[[ -x "${JAVA_BIN}" ]] || die "JAVA_BIN not found or not executable: ${JAVA_BIN}"
[[ -f "${AGENT}" ]] || die "Bubo agent jar not found: ${AGENT}"
[[ -f "${SLOWDOWN_FILE}" ]] || die "Slowdown JSON not found: ${SLOWDOWN_FILE}"

if [[ "${SUITE}" == "AWFY" ]]; then
  [[ -f "${CP}" ]] || die "Benchmarks jar not found: ${CP}"
  if [[ -z "${ITER}" ]]; then
    die "ITER is empty. For AWFY you must set ITER to the number of iterations (Harness 2nd arg)."
  fi
elif [[ "${SUITE}" == "Renaissance" ]]; then
  [[ -f "${RENAISSANCE_JAR}" ]] || die "Renaissance jar not found: ${RENAISSANCE_JAR}"
else
  die "Unknown SUITE='${SUITE}'. Expected 'AWFY' or 'Renaissance'."
fi

mkdir -p "${RAW_BUBO_DIR}" "${RAW_CFG_DIR}"

LOAD_PROFILES_OPT=""
if [[ -d "${PROFILE_PATH}" ]]; then
  LOAD_PROFILES_OPT="-Djdk.graal.LoadProfiles=${PROFILE_PATH}"
else
  echo "[WARN] Profile directory not found: ${PROFILE_PATH}"
  echo "       Running WITHOUT -Djdk.graal.LoadProfiles."
fi

EXTRA_ARGS="$(get_extra_args "${SUITE}" "${BENCHMARK}" "${BENCH_ARGS_JSON}")"
if [[ -z "${EXTRA_ARGS}" ]]; then
  die "Could not find extra_args for SUITE='${SUITE}', BENCHMARK='${BENCHMARK}' in ${BENCH_ARGS_JSON}"
fi

# ============================================================
# Build the actual benchmark launch sequence (SUITE-specific)
# ============================================================

LAUNCH_SEQ=""
if [[ "${SUITE}" == "AWFY" ]]; then
  # Harness <benchmark> <iterations> <extra_args>
  LAUNCH_SEQ="-cp ${CP} Harness ${BENCHMARK} ${ITER} ${EXTRA_ARGS}"
else
  # Renaissance:
  #   java ... -Xms12G -Xmx12G -jar renaissance.jar -r <innerBenchmarkAmount> <benchmark>
  LAUNCH_SEQ="-Xms12G -Xmx12G -jar ${RENAISSANCE_JAR} -r ${EXTRA_ARGS} ${BENCHMARK}"
fi

# ============================================================
# Runs
# ============================================================

echo "=============================="
echo "Producing raw outputs"
echo "  SUITE      : ${SUITE}"
echo "  BENCHMARK  : ${BENCHMARK}"
echo "  PROBE_MODE : ${PROBE_MODE}"
echo "  LAUNCH     : ${LAUNCH_SEQ}"
echo "  EXTRA_ARGS : ${EXTRA_ARGS}  (from ${BENCH_ARGS_JSON})"
echo "  PROFILES   : ${PROFILE_PATH}"
echo "  SLOWDOWN   : ${SLOWDOWN_FILE}"
echo "=============================="

echo
echo "[RUN A] Bubo baseline (NO slowdown) -> ${OUT_BUBO_BASE}"
"${JAVA_BIN}" \
  ${BASE_OPTS} \
  ${BUBO_OPTS} \
  ${LOAD_PROFILES_OPT} \
  -Djdk.graal.LIRGTSlowDown=false \
  ${LAUNCH_SEQ} \
  | tee "${OUT_BUBO_BASE}"

echo
echo "[RUN B] Bubo slowdown -> ${OUT_BUBO_SLOW}"
"${JAVA_BIN}" \
  ${BASE_OPTS} \
  ${BUBO_OPTS} \
  ${LOAD_PROFILES_OPT} \
  -Djdk.graal.LIRBlockSlowdownFileName="${SLOWDOWN_FILE}" \
  -Djdk.graal.LIRGTSlowDown=true \
  ${LAUNCH_SEQ} \
  | tee "${OUT_BUBO_SLOW}"

echo
echo "[RUN C] CFG DebugData baseline (separate run) -> ${OUT_CFG_DEBUG}"
echo "        (adds -Djdk.graal.HumphreysDebugData=true; does NOT need slowdown)"
"${JAVA_BIN}" \
  ${BASE_OPTS} \
  ${DEBUGDATA_OPTS} \
  ${BUBO_OPTS} \
  ${LOAD_PROFILES_OPT} \
  -Djdk.graal.LIRGTSlowDown=false \
  ${LAUNCH_SEQ} \
  | tee "${OUT_CFG_DEBUG}"

echo
echo "[DONE] Raw outputs written into:"
echo "  ${RAW_BUBO_DIR}"
echo "  ${RAW_CFG_DIR}"
