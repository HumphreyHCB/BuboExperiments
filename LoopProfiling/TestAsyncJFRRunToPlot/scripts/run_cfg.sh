#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ============================================================
# CONFIG (edit here OR pass via env from run_all.sh)
# ============================================================

JAVA_BIN="${JAVA_BIN:-/home/hb478/repos/graal-instrumentation/vm/latest_graalvm_home/bin/java}"
CP="${CP:-/home/hb478/repos/are-we-fast-yet/benchmarks/Java/benchmarks.jar}"
RENAISSANCE_JAR="${RENAISSANCE_JAR:-/home/hb478/repos/renaissance/renaissance-gpl-0.16.1.jar}"

BENCHMARK="${BENCHMARK:-LoopBenchmarks}"
SUITE="${SUITE:-AWFY}"                 # AWFY | Renaissance
MODE="${MODE:-WithoutProbe}"           # WithoutProbe | WithProbe (allowed, but pipeline intends WithoutProbe)
ITER="${ITER:-}"

BENCH_ARGS_JSON="${BENCH_ARGS_JSON:-/home/hb478/repos/GTSlowdownSchedular/GTResources/AWFY_Benchmarks.json}"

# Optional profile replay
GTS_ROOT="${GTS_ROOT:-/home/hb478/repos/GTSlowdownSchedular}"
PROFILE_PATH_DEFAULT="${GTS_ROOT}/FinalBuboTests/WithoutProbe/${SUITE}/${BENCHMARK}/${BENCHMARK}_CompilerReplay"
PROFILE_PATH="${PROFILE_PATH:-${PROFILE_PATH_DEFAULT}}"

RAW_CFG_DIR="${ROOT_DIR}/rawdata/cfg/${SUITE}/${BENCHMARK}/${MODE}"
OUT_CFG_DEBUG="${RAW_CFG_DIR}/${BENCHMARK}_baseline_cfg.out"

# ============================================================
# JVM / Graal options
# ============================================================

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

# Required for later matching
ASSIGNDEBUG_OPTS="
  -Djdk.graal.GTAssignDebug=true
"

# CFG dump phase
DEBUGDATA_OPTS="
  -Djdk.graal.HumphreysDebugData=true
"

die() { echo "[ERROR] $*" >&2; exit 1; }

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

  if command -v jq >/dev/null 2>&1; then
    jq -r --arg section "${section}" --arg bench "${bench}" \
      '.[$section][$bench].extra_args // empty' "${json}"
  else
    python3 - <<PY
import json
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

if [[ "${SUITE}" == "AWFY" ]]; then
  [[ -f "${CP}" ]] || die "Benchmarks jar not found: ${CP}"
  [[ -n "${ITER}" ]] || die "ITER is empty. For AWFY you must set ITER."
elif [[ "${SUITE}" == "Renaissance" ]]; then
  [[ -f "${RENAISSANCE_JAR}" ]] || die "Renaissance jar not found: ${RENAISSANCE_JAR}"
else
  die "Unknown SUITE='${SUITE}'. Expected 'AWFY' or 'Renaissance'."
fi

mkdir -p "${RAW_CFG_DIR}"

LOAD_PROFILES_OPT=""
if [[ -d "${PROFILE_PATH}" ]]; then
  LOAD_PROFILES_OPT="-Djdk.graal.LoadProfiles=${PROFILE_PATH}"
else
  echo "[WARN] Profile directory not found: ${PROFILE_PATH}"
  echo "       Running WITHOUT -Djdk.graal.LoadProfiles."
fi

EXTRA_ARGS="$(get_extra_args "${SUITE}" "${BENCHMARK}" "${BENCH_ARGS_JSON}")"
[[ -n "${EXTRA_ARGS}" ]] || die "Could not find extra_args for ${SUITE}/${BENCHMARK} in ${BENCH_ARGS_JSON}"

LAUNCH_SEQ=""
if [[ "${SUITE}" == "AWFY" ]]; then
  LAUNCH_SEQ="-cp ${CP} Harness ${BENCHMARK} ${ITER} ${EXTRA_ARGS}"
else
  LAUNCH_SEQ="-Xms12G -Xmx12G -jar ${RENAISSANCE_JAR} -r ${EXTRA_ARGS} ${BENCHMARK}"
fi

echo "=============================="
echo "CFG DebugData run"
echo "  SUITE     : ${SUITE}"
echo "  BENCHMARK : ${BENCHMARK}"
echo "  MODE      : ${MODE}"
echo "  LAUNCH    : ${LAUNCH_SEQ}"
echo "  EXTRA_ARGS: ${EXTRA_ARGS}"
echo "  PROFILES  : ${PROFILE_PATH}"
echo "  OUT       : ${OUT_CFG_DEBUG}"
echo "=============================="

"${JAVA_BIN}" \
  ${BASE_OPTS} \
  ${ASSIGNDEBUG_OPTS} \
  ${DEBUGDATA_OPTS} \
  ${LOAD_PROFILES_OPT} \
  ${LAUNCH_SEQ} \
  | tee "${OUT_CFG_DEBUG}"

echo
echo "[DONE] Wrote:"
echo "  ${OUT_CFG_DEBUG}"
