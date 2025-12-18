#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---------------- Configuration ----------------
JAVA_BIN="/home/hb478/repos/graal-instrumentation/vm/latest_graalvm_home/bin/java"
CP="/home/hb478/repos/are-we-fast-yet/benchmarks/Java/benchmarks.jar"
AGENT="/home/hb478/repos/graal-instrumentation/Bubo-Agent/target/JavaAgent-1.0-SNAPSHOT-jar-with-dependencies.jar"

BENCHMARK="LoopBenchmarks"

# If you want a fixed iteration count, set it here.
# If blank, we fail fast so you don't accidentally run a nonsense config.
ITER=""

# Profiles + slowdown JSON (source-of-truth)
PROFILE_PATH="/home/hb478/repos/GTSlowdownSchedular/FinalBuboTests/LoopBenchmarks/LoopBenchmarks_CompilerReplay"
SLOWDOWN_FILE="/home/hb478/repos/GTSlowdownSchedular/FinalBuboTests/LoopBenchmarks/Final_LoopBenchmarks.json"

# Output rawdata folders (these are what the plotting pipeline expects)
RAW_BUBO_DIR="${ROOT_DIR}/rawdata/bubo"
RAW_CFG_DIR="${ROOT_DIR}/rawdata/cfg"

OUT_BUBO_BASE="${RAW_BUBO_DIR}/${BENCHMARK}_baseline_withBubo.out"
OUT_BUBO_SLOW="${RAW_BUBO_DIR}/${BENCHMARK}_slowdown_withBubo.out"
OUT_CFG_DEBUG="${RAW_CFG_DIR}/${BENCHMARK}_baseline_withBubo.out"

# ---------------- JVM / Graal options ----------------
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
  -cp ${CP}
"

BUBO_OPTS="
  -Djdk.graal.BuboLIRPhase=true
  -javaagent:${AGENT}
"

# DebugData must be ONLY for the CFG run (per your requirement)
DEBUGDATA_OPTS="
  -Djdk.graal.HumphreysDebugData=true
"

# ---------------- Sanity checks ----------------
if [[ ! -x "${JAVA_BIN}" ]]; then
  echo "[ERROR] JAVA_BIN not found or not executable: ${JAVA_BIN}"
  exit 1
fi

if [[ ! -f "${CP}" ]]; then
  echo "[ERROR] Benchmarks jar not found: ${CP}"
  exit 1
fi

if [[ ! -f "${AGENT}" ]]; then
  echo "[ERROR] Bubo agent jar not found: ${AGENT}"
  exit 1
fi

LOAD_PROFILES_OPT=""
if [[ -d "${PROFILE_PATH}" ]]; then
  LOAD_PROFILES_OPT="-Djdk.graal.LoadProfiles=${PROFILE_PATH}"
else
  echo "[WARN] Profile directory not found: ${PROFILE_PATH}"
  echo "       Running WITHOUT -Djdk.graal.LoadProfiles."
fi

if [[ ! -f "${SLOWDOWN_FILE}" ]]; then
  echo "[ERROR] Slowdown JSON not found: ${SLOWDOWN_FILE}"
  exit 1
fi

mkdir -p "${RAW_BUBO_DIR}" "${RAW_CFG_DIR}"

# ---------------- Runs ----------------
echo "=============================="
echo "Producing raw outputs for: ${BENCHMARK}"
echo "=============================="

echo
echo "[RUN A] Bubo baseline (NO slowdown) -> ${OUT_BUBO_BASE}"
"${JAVA_BIN}" \
  ${BASE_OPTS} \
  ${BUBO_OPTS} \
  ${LOAD_PROFILES_OPT} \
  -Djdk.graal.LIRGTSlowDown=false \
  Harness "${BENCHMARK}" 12000 "${ITER}" \
  | tee "${OUT_BUBO_BASE}"

echo
echo "[RUN B] Bubo slowdown -> ${OUT_BUBO_SLOW}"
"${JAVA_BIN}" \
  ${BASE_OPTS} \
  ${BUBO_OPTS} \
  ${LOAD_PROFILES_OPT} \
  -Djdk.graal.LIRBlockSlowdownFileName="${SLOWDOWN_FILE}" \
  -Djdk.graal.LIRGTSlowDown=true \
  Harness "${BENCHMARK}" 12000 "${ITER}" \
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
  Harness "${BENCHMARK}" 12000 "${ITER}" \
  | tee "${OUT_CFG_DEBUG}"

echo
echo "[DONE] Raw outputs written into:"
echo "  ${RAW_BUBO_DIR}"
echo "  ${RAW_CFG_DIR}"
