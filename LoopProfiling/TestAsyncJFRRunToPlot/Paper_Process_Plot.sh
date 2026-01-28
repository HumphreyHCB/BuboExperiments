#!/usr/bin/env bash
set -euo pipefail

# Resolve directory of this script
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY_SCRIPT="${ROOT_DIR}/scripts/collect_and_plot_async_jfr_vs_vtune.py"

echo "[INFO] Running Async / JFR vs VTune analysis"
python3 "${PY_SCRIPT}"

echo "[INFO] Done"