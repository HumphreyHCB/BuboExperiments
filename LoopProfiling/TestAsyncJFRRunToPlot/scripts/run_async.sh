#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

JAVA_BIN="${JAVA_BIN:-/home/hb478/repos/graal-instrumentation/vm/latest_graalvm_home/bin/java}"
CP="${CP:-/home/hb478/repos/are-we-fast-yet/benchmarks/Java/benchmarks.jar}"
RENAISSANCE_JAR="${RENAISSANCE_JAR:-/home/hb478/repos/renaissance/renaissance-gpl-0.16.1.jar}"

BENCHMARK="${BENCHMARK:-LoopBenchmarks}"
SUITE="${SUITE:-AWFY}"
MODE="${MODE:-WithoutProbe}"
ITER="${ITER:-}"
TAG="${TAG:-AUTO_PIPELINE}"

BENCH_ARGS_JSON="${BENCH_ARGS_JSON:-/home/hb478/repos/GTSlowdownSchedular/GTResources/AWFY_Benchmarks.json}"

ASYNC_LIB="${ASYNC_LIB:-/home/hb478/repos/are-we-fast-yet/Async/async-profiler-4.2.1-linux-x64/lib/libasyncProfiler.so}"
ASYNC_VARIANT="${ASYNC_VARIANT:-cpu_10ms}"

# Slowdown file, required for slowdown run
GTS_ROOT="${GTS_ROOT:-/home/hb478/repos/GTSlowdownSchedular}"
GTS_FINAL_DIR_DEFAULT="${GTS_ROOT}/FinalBuboTests/${MODE}/${SUITE}/${BENCHMARK}"
GTS_FINAL_DIR="${GTS_FINAL_DIR:-${GTS_FINAL_DIR_DEFAULT}}"
SLOWDOWN_FILE_DEFAULT="${GTS_FINAL_DIR}/Final_${BENCHMARK}.json"
SLOWDOWN_FILE="${SLOWDOWN_FILE:-${SLOWDOWN_FILE_DEFAULT}}"

# Optional profile replay
PROFILE_PATH_DEFAULT="${GTS_FINAL_DIR}/${BENCHMARK}_CompilerReplay"
PROFILE_PATH="${PROFILE_PATH:-${PROFILE_PATH_DEFAULT}}"

RAW_ASYNC_RUN_DIR="${ROOT_DIR}/rawdata/async/${SUITE}/${BENCHMARK}/${MODE}/${ASYNC_VARIANT}/${TAG}"
OUT_STDOUT_NO="${RAW_ASYNC_RUN_DIR}/${BENCHMARK}_async_no_slowdown.out"
OUT_STACKS_NO="${RAW_ASYNC_RUN_DIR}/${BENCHMARK}_async_no_slowdown.txt"
OUT_STDOUT_SLOW="${RAW_ASYNC_RUN_DIR}/${BENCHMARK}_async_slowdown.out"
OUT_STACKS_SLOW="${RAW_ASYNC_RUN_DIR}/${BENCHMARK}_async_slowdown.txt"

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

ASSIGNDEBUG_OPTS="
  -Djdk.graal.GTAssignDebug=true
"

die() { echo "[ERROR] $*" >&2; exit 1; }

get_extra_args() {
  local suite="$1" bench="$2" json="$3" section
  if [[ "${suite}" == "AWFY" ]]; then section="AWFY Benchmarks"
  elif [[ "${suite}" == "Renaissance" ]]; then section="Renaissance Benchmarks"
  else die "Unknown SUITE='${suite}'."; fi

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

[[ -x "${JAVA_BIN}" ]] || die "JAVA_BIN not executable: ${JAVA_BIN}"
[[ -f "${ASYNC_LIB}" ]] || die "ASYNC_LIB not found: ${ASYNC_LIB}"
[[ -f "${SLOWDOWN_FILE}" ]] || die "Slowdown JSON not found: ${SLOWDOWN_FILE}"

if [[ "${SUITE}" == "AWFY" ]]; then
  [[ -f "${CP}" ]] || die "Benchmarks jar not found: ${CP}"
  [[ -n "${ITER}" ]] || die "ITER is empty. For AWFY you must set ITER."
elif [[ "${SUITE}" == "Renaissance" ]]; then
  [[ -f "${RENAISSANCE_JAR}" ]] || die "Renaissance jar not found: ${RENAISSANCE_JAR}"
else
  die "Unknown SUITE='${SUITE}'."
fi

mkdir -p "${RAW_ASYNC_RUN_DIR}"

LOAD_PROFILES_OPT=""
if [[ -d "${PROFILE_PATH}" ]]; then
  LOAD_PROFILES_OPT="-Djdk.graal.LoadProfiles=${PROFILE_PATH}"
fi

EXTRA_ARGS="$(get_extra_args "${SUITE}" "${BENCHMARK}" "${BENCH_ARGS_JSON}")"
[[ -n "${EXTRA_ARGS}" ]] || die "Could not find extra_args for ${SUITE}/${BENCHMARK}"

LAUNCH_SEQ=""
if [[ "${SUITE}" == "AWFY" ]]; then
  LAUNCH_SEQ="-cp ${CP} Harness ${BENCHMARK} ${ITER} ${EXTRA_ARGS}"
else
  LAUNCH_SEQ="-Xms12G -Xmx12G -jar ${RENAISSANCE_JAR} -r ${EXTRA_ARGS} ${BENCHMARK}"
fi

run_one() {
  local label="$1"
  local stacks_file="$2"
  local stdout_file="$3"
  local slowdown_enabled="$4"

  local async_agent_opts="start,event=cpu,interval=10ms,file=${stacks_file}"

  echo
  echo "[RUN ${label}] async-profiler"
  echo "  stacks: ${stacks_file}"
  echo "  stdout: ${stdout_file}"
  echo "  slowdown: ${slowdown_enabled}"

  if [[ "${slowdown_enabled}" == "true" ]]; then
    "${JAVA_BIN}" \
      ${BASE_OPTS} \
      ${ASSIGNDEBUG_OPTS} \
      ${LOAD_PROFILES_OPT} \
      -Djdk.graal.LIRBlockSlowdownFileName="${SLOWDOWN_FILE}" \
      -Djdk.graal.LIRGTSlowDown=true \
      -agentpath:"${ASYNC_LIB}"="${async_agent_opts}" \
      ${LAUNCH_SEQ} \
      | tee "${stdout_file}"
  else
    "${JAVA_BIN}" \
      ${BASE_OPTS} \
      ${ASSIGNDEBUG_OPTS} \
      ${LOAD_PROFILES_OPT} \
      -Djdk.graal.LIRGTSlowDown=false \
      -agentpath:"${ASYNC_LIB}"="${async_agent_opts}" \
      ${LAUNCH_SEQ} \
      | tee "${stdout_file}"
  fi
}

echo "=============================="
echo "async-profiler dual run"
echo "  SUITE     : ${SUITE}"
echo "  BENCHMARK : ${BENCHMARK}"
echo "  MODE      : ${MODE}"
echo "  VARIANT   : ${ASYNC_VARIANT}"
echo "  TAG       : ${TAG}"
echo "  SLOWDOWN  : ${SLOWDOWN_FILE}"
echo "=============================="

run_one "A no_slowdown" "${OUT_STACKS_NO}" "${OUT_STDOUT_NO}" "false"
run_one "B slowdown"    "${OUT_STACKS_SLOW}" "${OUT_STDOUT_SLOW}" "true"

echo
echo "[DONE] Wrote:"
echo "  ${OUT_STDOUT_NO}"
echo "  ${OUT_STACKS_NO}"
echo "  ${OUT_STDOUT_SLOW}"
echo "  ${OUT_STACKS_SLOW}"
