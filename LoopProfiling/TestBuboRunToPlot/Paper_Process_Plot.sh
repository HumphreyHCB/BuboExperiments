#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_SCRIPT="${ROOT_DIR}/scripts/collect_and_condense_csvs.py"

echo "[INFO] Collecting and condensing CSVs"
python3 "${PYTHON_SCRIPT}"