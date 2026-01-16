#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"

# ============================================================
# CONFIG DEFAULTS (edit these; CLI can override)
# ============================================================
ITER_DEFAULT="500"
BENCHMARK_DEFAULT="Bounce"
SUITE_DEFAULT="AWFY"
PROBE_MODE_DEFAULT="WithProbe"

# Optional default
TAG_DEFAULT="AUTO_PIPELINE"

usage() {
  cat <<'EOF'
Usage: pipeline.sh [options]

Options:
  -i, --iter N                Iterations (default: from script config)
  -b, --benchmark NAME        Benchmark name (default: from script config)
  -s, --suite AWFY|Renaissance Suite (default: from script config)
  -p, --probe WithProbe|WithoutProbe  Probe mode (default: from script config)
  -t, --tag TAG               Tag for VTune step (default: from script config)
  -h, --help                  Show help
EOF
}

# ============================================================
# Parse args (override defaults only if provided)
# ============================================================
ITER="" BENCHMARK="" SUITE="" PROBE_MODE="" TAG=""

while (($#)); do
  case "$1" in
    -i|--iter)       ITER="${2:?missing value for $1}"; shift 2;;
    -b|--benchmark)  BENCHMARK="${2:?missing value for $1}"; shift 2;;
    -s|--suite)      SUITE="${2:?missing value for $1}"; shift 2;;
    -p|--probe)      PROBE_MODE="${2:?missing value for $1}"; shift 2;;
    -t|--tag)        TAG="${2:?missing value for $1}"; shift 2;;
    -h|--help)       usage; exit 0;;
    --)              shift; break;;
    *)               echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

# Apply defaults where args were not provided
ITER="${ITER:-$ITER_DEFAULT}"
BENCHMARK="${BENCHMARK:-$BENCHMARK_DEFAULT}"
SUITE="${SUITE:-$SUITE_DEFAULT}"
PROBE_MODE="${PROBE_MODE:-$PROBE_MODE_DEFAULT}"
TAG="${TAG:-$TAG_DEFAULT}"

# (Optional) validate to catch typos early
case "$SUITE" in AWFY|Renaissance) ;; *) echo "Bad SUITE: $SUITE" >&2; exit 2;; esac
case "$PROBE_MODE" in WithProbe|WithoutProbe) ;; *) echo "Bad PROBE_MODE: $PROBE_MODE" >&2; exit 2;; esac

# ============================================================
# Derived paths (now using final values)
# ============================================================
PROCESSED_DIR="${ROOT_DIR}/processed/${SUITE}/${BENCHMARK}/${PROBE_MODE}"

BUILD_VTUNE="${ROOT_DIR}/scripts/build_total_pct_slowdown_per_loop.py"
PLOT_SCRIPT="${ROOT_DIR}/scripts/plot_loopbenchmarks.py"

DEBUG_OUT="${ROOT_DIR}/rawdata/cfg/${SUITE}/${BENCHMARK}/${PROBE_MODE}/${BENCHMARK}_baseline_withBubo.out"
SLOWDOWN_TXT="${ROOT_DIR}/rawdata/vtune/${SUITE}/${BENCHMARK}/${PROBE_MODE}/slowdown_blocks.txt"

MARKERPHASE_JSON="/home/hb478/repos/GTSlowdownSchedular/FinalBuboTests/${PROBE_MODE}/${SUITE}/${BENCHMARK}/MarkerPhaseInfo.json"
BRIDGE_SRC="/home/hb478/repos/GTSlowdownSchedular/FinalBuboTests/${PROBE_MODE}/${SUITE}/${BENCHMARK}/Final_${BENCHMARK}.json"

mkdir -p "${PROCESSED_DIR}" "${ROOT_DIR}/plots"

echo "[CONFIG] SUITE=${SUITE} BENCHMARK=${BENCHMARK} PROBE_MODE=${PROBE_MODE} ITER=${ITER} TAG=${TAG}"

echo "[STEP 0] Producing raw Bubo + CFG outputs into rawdata/"
BENCHMARK="${BENCHMARK}" SUITE="${SUITE}" PROBE_MODE="${PROBE_MODE}" ITER="${ITER}" \
  "${ROOT_DIR}/scripts/run_bubo_and_cfg.sh"

echo "[STEP 0.5] Producing VTune slowdown block-times file into rawdata/vtune/"
BENCHMARK="${BENCHMARK}" SUITE="${SUITE}" PROBE_MODE="${PROBE_MODE}" ITER="${ITER}" TAG="${TAG}" \
  "${ROOT_DIR}/scripts/run_vtune_slowdown_blocks.sh"

echo "[STEP 1] Building per-loop totals into: ${PROCESSED_DIR}"
"${PY}" "${BUILD_VTUNE}" \
  --debug-out "${DEBUG_OUT}" \
  --slowdown-txt "${SLOWDOWN_TXT}" \
  --block-id-is-vtune \
  --bridge-json "${BRIDGE_SRC}" \
  --markerphase-json "${MARKERPHASE_JSON}" \
  --processed-dir "${PROCESSED_DIR}" \
  --min-normal 1e-9

echo "[STEP 2] Generating final plot into plots/"
"${PY}" "${PLOT_SCRIPT}" \
  --benchmark "${BENCHMARK}" \
  --suite "${SUITE}" \
  --probe-mode "${PROBE_MODE}" \
  --processed-dir "${PROCESSED_DIR}"

CHECK_SCRIPT="${ROOT_DIR}/scripts/check_loop_ids_match.py"


echo "[STEP 3] Checking VTune used same compilation unit as plots (master)..."
"${PY}" "${CHECK_SCRIPT}" \
  --benchmark "${BENCHMARK}" \
  --plots-dir "${ROOT_DIR}/plots" \
  --processed-dir "${PROCESSED_DIR}" \
  --max-errors 100

echo
echo "[DONE]"
echo "Key outputs:"
echo "  ${PROCESSED_DIR}/vtune/total_pct_slowdown_per_loop.csv"
echo "  plots/${BENCHMARK}_bubo_loops.png"
echo "  plots/${BENCHMARK}_bubo_loops.csv"
