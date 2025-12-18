#! /usr/bin/env bash
set -euo pipefail

# ===== User-configurable settings =====

# Input: text file with HumphreysDebugDataPhase output
INPUT_TXT="/home/hb478/repos/BuboExperiments/LoopProfiling/CFG_Verification/BuboOwnSlowdown_bubo6_DevineWithProbe_cfg/LoopBenchmarks/LoopBenchmarks_baseline_withBubo.out"

# Output directory: all DOT and SVG files go here
OUT_DIR="/home/hb478/repos/BuboExperiments/LoopProfiling/CFG_Verification/BuboOwnSlowdown_bubo6_DevineWithProbe_cfg/LoopBenchmarks/LoopBenchmarks_baseline_withBubo"

# Python script that converts text -> per-comp DOTs
PY_SCRIPT="debug2dot.py"

# ===== Pipeline =====

echo "Creating output directory: ${OUT_DIR}"
mkdir -p "${OUT_DIR}"

echo "Generating DOT files from ${INPUT_TXT}..."
python3 "${PY_SCRIPT}" "${INPUT_TXT}" --outdir "${OUT_DIR}"

echo "Rendering SVGs from DOT files..."
shopt -s nullglob
for dotfile in "${OUT_DIR}"/*.dot; do
    svgfile="${dotfile%.dot}.svg"
    echo "  dot -Tsvg '${dotfile}' -> '${svgfile}'"
    dot -Tsvg "${dotfile}" -o "${svgfile}"
done
shopt -u nullglob

echo "Done."
echo "All DOT and SVG files are in: ${OUT_DIR}"
