#! /usr/bin/env bash
set -euo pipefail

# ===== User-configurable settings =====

# Input: text file with HumphreysDebugDataPhase output
INPUT_TXT="Mandelbrot.txt"

# Output directory: all DOT and SVG files go here
OUT_DIR="MandelbrotCFGGraphs"

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
