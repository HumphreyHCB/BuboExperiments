#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"

# ============================================================
# CONFIG DEFAULTS (CLI can override)
# ============================================================
ITER_DEFAULT="500"
BENCHMARK_DEFAULT="Mandelbrot"
SUITE_DEFAULT="AWFY"
MODE_DEFAULT="WithoutProbe"
TAG_DEFAULT="AUTO_PIPELINE"

# Which profilers to run. Comma separated list.
PROFILERS_DEFAULT="cfg,vtune,async,jfr"

usage() {
  cat <<'EOF'
Usage: run_all.sh [options]

Options:
  -i, --iter N                 Iterations
  -b, --benchmark NAME         Benchmark name
  -s, --suite AWFY|Renaissance  Suite
  -m, --mode WithoutProbe|WithProbe  Mode (default WithoutProbe)
  -t, --tag TAG                Tag for rawdata folders
  --profilers LIST             Comma list: cfg,vtune,async,jfr (default cfg,vtune,async,jfr)
  -h, --help                   Show help
EOF
}

ITER="" BENCHMARK="" SUITE="" MODE="" TAG="" PROFILERS=""

while (($#)); do
  case "$1" in
    -i|--iter)       ITER="${2:?missing value for $1}"; shift 2;;
    -b|--benchmark)  BENCHMARK="${2:?missing value for $1}"; shift 2;;
    -s|--suite)      SUITE="${2:?missing value for $1}"; shift 2;;
    -m|--mode)       MODE="${2:?missing value for $1}"; shift 2;;
    -t|--tag)        TAG="${2:?missing value for $1}"; shift 2;;
    --profilers)     PROFILERS="${2:?missing value for $1}"; shift 2;;
    -h|--help)       usage; exit 0;;
    --)              shift; break;;
    *)               echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

ITER="${ITER:-$ITER_DEFAULT}"
BENCHMARK="${BENCHMARK:-$BENCHMARK_DEFAULT}"
SUITE="${SUITE:-$SUITE_DEFAULT}"
MODE="${MODE:-$MODE_DEFAULT}"
TAG="${TAG:-$TAG_DEFAULT}"
PROFILERS="${PROFILERS:-$PROFILERS_DEFAULT}"

case "$SUITE" in AWFY|Renaissance) ;; *) echo "Bad SUITE: $SUITE" >&2; exit 2;; esac
case "$MODE" in WithProbe|WithoutProbe) ;; *) echo "Bad MODE: $MODE" >&2; exit 2;; esac

PROCESSED_DIR="${ROOT_DIR}/processed/${SUITE}/${BENCHMARK}/${MODE}"
mkdir -p "${PROCESSED_DIR}" "${ROOT_DIR}/plots"

echo "[CONFIG] SUITE=${SUITE} BENCHMARK=${BENCHMARK} MODE=${MODE} ITER=${ITER} TAG=${TAG} PROFILERS=${PROFILERS}"
echo "[PATH] PROCESSED_DIR=${PROCESSED_DIR}"

run_one() {
  local name="$1"
  case ",${PROFILERS}," in
    *",${name},"*) return 0;;
    *) return 1;;
  esac
}

# if run_one "cfg"; then
#   echo "[STEP 0] CFG stage into rawdata/cfg/"
#   BENCHMARK="${BENCHMARK}" SUITE="${SUITE}" MODE="${MODE}" ITER="${ITER}" TAG="${TAG}" \
#     "${ROOT_DIR}/scripts/run_cfg.sh"
# fi

# if run_one "vtune"; then
#   echo "[STEP 0.5] VTune slowdown-blocks diff into rawdata/vtune/"
#   BENCHMARK="${BENCHMARK}" SUITE="${SUITE}" MODE="${MODE}" ITER="${ITER}" TAG="${TAG}" \
#     "${ROOT_DIR}/scripts/run_vtune_slowdown_blocks.sh"
# fi

# if run_one "async"; then
#   echo "[STEP 1] async-profiler stage into rawdata/async/"
#   BENCHMARK="${BENCHMARK}" SUITE="${SUITE}" MODE="${MODE}" ITER="${ITER}" TAG="${TAG}" \
#     "${ROOT_DIR}/scripts/run_async.sh"
# fi

# if run_one "jfr"; then
#   echo "[STEP 2] JFR stage into rawdata/jfr/"
#   BENCHMARK="${BENCHMARK}" SUITE="${SUITE}" MODE="${MODE}" ITER="${ITER}" TAG="${TAG}" \
#     "${ROOT_DIR}/scripts/run_jfr.sh"
# fi


BUILD_VTUNE="${ROOT_DIR}/scripts/build_total_pct_slowdown_per_loop.py"

DEBUG_OUT="${ROOT_DIR}/rawdata/cfg/${SUITE}/${BENCHMARK}/${MODE}/${BENCHMARK}_baseline_cfg.out"
SLOWDOWN_TXT="${ROOT_DIR}/rawdata/vtune/${SUITE}/${BENCHMARK}/${MODE}/${TAG}/slowdown_blocks.txt"

MARKERPHASE_JSON="/home/hb478/repos/GTSlowdownSchedular/FinalBuboTests/${MODE}/${SUITE}/${BENCHMARK}/MarkerPhaseInfo.json"

# if run_one "vtune"; then
#   echo "[STEP 3] Building per loop totals into: ${PROCESSED_DIR}"
#   args=( "${PY}" "${BUILD_VTUNE}"
#     --debug-out "${DEBUG_OUT}"
#     --slowdown-txt "${SLOWDOWN_TXT}"
#     --block-id-is-vtune
#     --markerphase-json "${MARKERPHASE_JSON}"
#     --processed-dir "${PROCESSED_DIR}"
#     --min-normal 1e-9
#   )


#   "${args[@]}"
# fi

echo "[STEP 3] Converting async and jfr dumps into CSVs under processed/"

PROCESS_PROFILERS="${ROOT_DIR}/scripts/process_async_jfr_to_csv.py"

# Must match the run scripts defaults, but allow override via env
ASYNC_VARIANT="${ASYNC_VARIANT:-cpu_10ms}"
JFR_VARIANT="${JFR_VARIANT:-profile_10ms}"

RAW_ASYNC_DIR="${ROOT_DIR}/rawdata/async/${SUITE}/${BENCHMARK}/${MODE}/${ASYNC_VARIANT}/${TAG}"
RAW_JFR_DIR="${ROOT_DIR}/rawdata/jfr/${SUITE}/${BENCHMARK}/${MODE}/${JFR_VARIANT}/${TAG}"

ASYNC_NO="${RAW_ASYNC_DIR}/${BENCHMARK}_async_no_slowdown.txt"
ASYNC_SLOW="${RAW_ASYNC_DIR}/${BENCHMARK}_async_slowdown.txt"

JFR_NO="${RAW_JFR_DIR}/${BENCHMARK}_no_slowdown.jfr"
JFR_SLOW="${RAW_JFR_DIR}/${BENCHMARK}_slowdown.jfr"

JFR_NO_TXT="${RAW_JFR_DIR}/${BENCHMARK}_no_slowdown.jfr.print.txt"
JFR_SLOW_TXT="${RAW_JFR_DIR}/${BENCHMARK}_slowdown.jfr.print.txt"

# Hard fail if missing, because we want BOTH runs and both profilers
[[ -f "${ASYNC_NO}" ]]   || { echo "[ERROR] Missing async no_slowdown: ${ASYNC_NO}" >&2; exit 1; }
[[ -f "${ASYNC_SLOW}" ]] || { echo "[ERROR] Missing async slowdown:    ${ASYNC_SLOW}" >&2; exit 1; }
[[ -f "${JFR_NO}" ]]     || { echo "[ERROR] Missing jfr no_slowdown:   ${JFR_NO}" >&2; exit 1; }
[[ -f "${JFR_SLOW}" ]]   || { echo "[ERROR] Missing jfr slowdown:      ${JFR_SLOW}" >&2; exit 1; }

#Convert JFR binaries to text for the python parser
echo "[STEP 3.1] jfr print (no_slowdown) -> ${JFR_NO_TXT}"
jfr print --events jdk.ExecutionSample,jdk.NativeMethodSample "${JFR_NO}" > "${JFR_NO_TXT}"

echo "[STEP 3.2] jfr print (slowdown) -> ${JFR_SLOW_TXT}"
jfr print --events jdk.ExecutionSample,jdk.NativeMethodSample "${JFR_SLOW}" > "${JFR_SLOW_TXT}"

# Write per-run CSVs into processed/{async,jfr}/
# (I am assuming the python script writes to processed-dir/{async|jfr}/loops_profile.csv,
# so we give it different processed dirs to avoid overwriting.)
PROC_NO="${PROCESSED_DIR}/no_slowdown"
PROC_SLOW="${PROCESSED_DIR}/slowdown"
mkdir -p "${PROC_NO}" "${PROC_SLOW}"

echo "[STEP 3.3] Build CSVs (no_slowdown)"
"${PY}" "${PROCESS_PROFILERS}" \
  --benchmark "${BENCHMARK}" \
  --suite "${SUITE}" \
  --mode "${MODE}" \
  --tag "${TAG}" \
  --processed-dir "${PROC_NO}" \
  --async-txt "${ASYNC_NO}" \
  --jfr-txt "${JFR_NO_TXT}"

echo "[STEP 3.4] Build CSVs (slowdown)"
"${PY}" "${PROCESS_PROFILERS}" \
  --benchmark "${BENCHMARK}" \
  --suite "${SUITE}" \
  --mode "${MODE}" \
  --tag "${TAG}" \
  --processed-dir "${PROC_SLOW}" \
  --async-txt "${ASYNC_SLOW}" \
  --jfr-txt "${JFR_SLOW_TXT}"

PLOT="${ROOT_DIR}/scripts/plot_loopbenchmarks.py"

VTUNE_CSV="${PROCESSED_DIR}/vtune/total_pct_slowdown_per_loop.csv"

ASYNC_NO="${PROCESSED_DIR}/no_slowdown/async/loops_profile.csv"
ASYNC_SLOW="${PROCESSED_DIR}/slowdown/async/loops_profile.csv"

JFR_NO="${PROCESSED_DIR}/no_slowdown/jfr/loops_profile.csv"
JFR_SLOW="${PROCESSED_DIR}/slowdown/jfr/loops_profile.csv"

"${PY}" "${PLOT}" \
  --benchmark "${BENCHMARK}" \
  --suite "${SUITE}" \
  --mode "${MODE}" \
  --processed-dir "${PROCESSED_DIR}" \
  --vtune-csv "${VTUNE_CSV}" \
  --async-no "${ASYNC_NO}" \
  --async-slow "${ASYNC_SLOW}" \
  --jfr-no "${JFR_NO}" \
  --jfr-slow "${JFR_SLOW}" \
  --min-share 2.0




echo
echo "[DONE]"

