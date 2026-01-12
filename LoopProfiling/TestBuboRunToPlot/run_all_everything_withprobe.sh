#!/usr/bin/env bash

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ALL="${ROOT_DIR}/run_all.sh"

# ------------------------------------------------------------
# AWFY (WithProbe)
# ------------------------------------------------------------
echo "=============================="
echo "Running AWFY benchmarks (WithProbe)"
echo "=============================="

AWFY_BENCHES=(
  Bounce
  Mandelbrot
  NBody
  Sieve
)

# AWFY_BENCHES=(
#   Bounce
#   CD
#   Json
#   Mandelbrot
#   NBody
#   Sieve
# )

for BENCH in "${AWFY_BENCHES[@]}"; do
  echo
  echo "---- AWFY / ${BENCH} (ITER=500) ----"
  bash "${RUN_ALL}" --benchmark "${BENCH}" --suite AWFY --probe WithProbe --iter 500
done

LoopBenchmarks is special
echo
echo "---- AWFY / LoopBenchmarks (ITER=1) ----"
bash "${RUN_ALL}" --benchmark LoopBenchmarks --suite AWFY --probe WithProbe --iter 12000

# ------------------------------------------------------------
# Renaissance (WithProbe)
# ------------------------------------------------------------
# echo
# echo "=============================="
# echo "Running Renaissance benchmarks (WithProbe)"
# echo "=============================="

# RENAISSANCE_BENCHES=(
#   mnemonics
#   par-mnemonics
#   rx-scrabble
#   scala-doku
#   scala-stm-bench7
#   scrabble
# )

# for BENCH in "${RENAISSANCE_BENCHES[@]}"; do
#   echo
#   echo "---- Renaissance / ${BENCH} (ITER=1) ----"
#   bash "${RUN_ALL}" --benchmark "${BENCH}" --suite Renaissance --probe WithProbe --iter 1
# done

# echo
# echo "=============================="
# echo "[DONE] All WithProbe benchmarks completed"
# echo "=============================="
