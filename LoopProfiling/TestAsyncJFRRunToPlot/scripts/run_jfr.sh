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
JFR_VARIANT="${JFR_VARIANT:-profile_10ms}"

# Slowdown file, required for slowdown run
GTS_ROOT="${GTS_ROOT:-/home/hb478/repos/GTSlowdownSchedular}"
GTS_FINAL_DIR_DEFAULT="${GTS_ROOT}/FinalBuboTests/${MODE}/${SUITE}/${BENCHMARK}"
GTS_FINAL_DIR="${GTS_FINAL_DIR:-${GTS_FINAL_DIR_DEFAULT}}"
SLOWDOWN_FILE_DEFAULT="${GTS_FINAL_DIR}/Final_${BENCHMARK}.json"
SLOWDOWN_FILE="${SLOWDOWN_FILE:-${SLOWDOWN_FILE_DEFAULT}}"

# Optional profile replay
PROFILE_PATH_DEFAULT="${GTS_FINAL_DIR}/${BENCHMARK}_CompilerReplay"
PROFILE_PATH="${PROFILE_PATH:-${PROFILE_PATH_DEFAULT}}"

RAW_JFR_RUN_DIR="${ROOT_DIR}/rawdata/jfr/${SUITE}/${BENCHMARK}/${MODE}/${JFR_VARIANT}/${TAG}"
OUT_STDOUT_NO="${RAW_JFR_RUN_DIR}/${BENCHMARK}_jfr_no_slowdown.out"
OUT_JFR_NO="${RAW_JFR_RUN_DIR}/${BENCHMARK}_no_slowdown.jfr"
OUT_STDOUT_SLOW="${RAW_JFR_RUN_DIR}/${BENCHMARK}_jfr_slowdown.out"
OUT_JFR_SLOW="${RAW_JFR_RUN_DIR}/${BENCHMARK}_slowdown.jfr"

CONFIG_DIR="${ROOT_DIR}/config"
JFC_10MS="${CONFIG_DIR}/jfr_10ms.jfc"

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

ensure_jfc_10ms() {
  mkdir -p "${CONFIG_DIR}"

  if [[ -f "${JFC_10MS}" ]]; then
    return 0
  fi

  local java_home
  java_home="$("${JAVA_BIN}" -XshowSettings:properties -version 2>&1 | awk -F'= ' '/java.home =/ {print $2; exit}')"
  [[ -n "${java_home}" ]] || die "Could not determine java.home from JAVA_BIN"

  local jfr_bin="${java_home}/bin/jfr"
  local base_jfc=""

  if [[ -f "${java_home}/lib/jfr/profile.jfc" ]]; then
    base_jfc="${java_home}/lib/jfr/profile.jfc"
  elif [[ -f "${java_home}/lib/jfr/default.jfc" ]]; then
    base_jfc="${java_home}/lib/jfr/default.jfc"
  else
    die "Could not find base .jfc under ${java_home}/lib/jfr"
  fi

  if [[ -x "${jfr_bin}" ]]; then
    echo "[INFO] Creating ${JFC_10MS} from ${base_jfc}"
    "${jfr_bin}" configure \
      --input "${base_jfc}" \
      --output "${JFC_10MS}" \
      "jdk.ExecutionSample#period=10 ms" \
      "jdk.NativeMethodSample#period=10 ms" \
      >/dev/null
  else
    echo "[WARN] jfr tool not found at ${jfr_bin}"
    echo "       Copying base profile without forcing 10 ms period."
    cp -f "${base_jfc}" "${JFC_10MS}"
  fi
}

[[ -x "${JAVA_BIN}" ]] || die "JAVA_BIN not executable: ${JAVA_BIN}"
[[ -f "${SLOWDOWN_FILE}" ]] || die "Slowdown JSON not found: ${SLOWDOWN_FILE}"

if [[ "${SUITE}" == "AWFY" ]]; then
  [[ -f "${CP}" ]] || die "Benchmarks jar not found: ${CP}"
  [[ -n "${ITER}" ]] || die "ITER is empty. For AWFY you must set ITER."
elif [[ "${SUITE}" == "Renaissance" ]]; then
  [[ -f "${RENAISSANCE_JAR}" ]] || die "Renaissance jar not found: ${RENAISSANCE_JAR}"
else
  die "Unknown SUITE='${SUITE}'."
fi

mkdir -p "${RAW_JFR_RUN_DIR}"
ensure_jfc_10ms

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
  local jfr_file="$2"
  local stdout_file="$3"
  local slowdown_enabled="$4"

  echo
  echo "[RUN ${label}] JFR"
  echo "  jfr: ${jfr_file}"
  echo "  stdout: ${stdout_file}"
  echo "  slowdown: ${slowdown_enabled}"

  if [[ "${slowdown_enabled}" == "true" ]]; then
    "${JAVA_BIN}" \
      ${BASE_OPTS} \
      ${ASSIGNDEBUG_OPTS} \
      ${LOAD_PROFILES_OPT} \
      -Djdk.graal.LIRBlockSlowdownFileName="${SLOWDOWN_FILE}" \
      -Djdk.graal.LIRGTSlowDown=true \
      -XX:StartFlightRecording="filename=${jfr_file},settings=${JFC_10MS},dumponexit=true" \
      ${LAUNCH_SEQ} \
      | tee "${stdout_file}"
  else
    "${JAVA_BIN}" \
      ${BASE_OPTS} \
      ${ASSIGNDEBUG_OPTS} \
      ${LOAD_PROFILES_OPT} \
      -Djdk.graal.LIRGTSlowDown=false \
      -XX:StartFlightRecording="filename=${jfr_file},settings=${JFC_10MS},dumponexit=true" \
      ${LAUNCH_SEQ} \
      | tee "${stdout_file}"
  fi
}

echo "=============================="
echo "JFR dual run"
echo "  SUITE     : ${SUITE}"
echo "  BENCHMARK : ${BENCHMARK}"
echo "  MODE      : ${MODE}"
echo "  VARIANT   : ${JFR_VARIANT}"
echo "  TAG       : ${TAG}"
echo "  SETTINGS  : ${JFC_10MS}"
echo "  SLOWDOWN  : ${SLOWDOWN_FILE}"
echo "=============================="

run_one "A no_slowdown" "${OUT_JFR_NO}" "${OUT_STDOUT_NO}" "false"
run_one "B slowdown"    "${OUT_JFR_SLOW}" "${OUT_STDOUT_SLOW}" "true"

echo
echo "[DONE] Wrote:"
echo "  ${OUT_STDOUT_NO}"
echo "  ${OUT_JFR_NO}"
echo "  ${OUT_STDOUT_SLOW}"
echo "  ${OUT_JFR_SLOW}"
echo "  ${JFC_10MS}"
