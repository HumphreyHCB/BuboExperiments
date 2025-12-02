# README

## Overview

This mini toolchain turns a noisy Graal log into per-method time attribution.

- **GraalLogParser.java**  
  Parses Graal’s raw text dump, extracts `compilation_id` / `block_id` / LIR lines, disassembles the raw bytes with **Capstone**, and writes a clean CSV.

- **BlockSummary.java**  
  Reads the CSV, filters to `HotSpotCompilation-83[Queens.benchmark()]`, collapses rows per `block_id`, and outputs one line per block with:
  - full concatenated `disasm`
  - `total_instructions`
  - `distinct_sources`
  - `has_source_ratio` (instructions with non-null source ÷ total instructions)
  - `source_counts_json` (source → instruction count; includes `"null"` when present)

- **MethodTimeFromMarkers.java**  
  Joins the block summary with `MarkerPhaseInfo.json` (`GraalID`, `BaseCpuTime`). Distributes each block’s time across sources using the per-block instruction shares, aggregates **time per method**, and reports unmatched/unknown time.

---

## Inputs

- `QueensRawMachineCodeDump.txt` — raw Graal log (input to **GraalLogParser**).
- `MarkerPhaseInfo.json` — per-block timing with `GraalID` and `BaseCpuTime` (input to **MethodTimeFromMarkers**).
- Capstone (only needed at runtime by **GraalLogParser**):
  - `capstone.jar`, `jna-*.jar`
  - native `libcapstone.so` on the library path (e.g. `/usr/local/lib64`)

## Outputs

- `ProcessedMachineCode.csv` — from **GraalLogParser**  
  Columns: `compilation_id,block_id,lir_class,bytes,has_source,source,disasm`
- `Compilation83_BlockSummary.csv` — from **BlockSummary**  
  Columns: `block_id,total_instructions,disasm,distinct_sources,has_source_ratio,source_counts_json`
- `MethodTimeReport.csv` — from **MethodTimeFromMarkers**  
  Columns: `method,time,percent_of_total,blocks_contributing`  
  (Console prints totals for matched/unmatched blocks/time and unknown-source time.)

---

## Build & Run (Java 21)

> Only **GraalLogParser** needs Capstone at runtime. The other two consume CSV/JSON only.

### GraalLogParser
```bash
# compile
/usr/lib/jvm/java-21-openjdk-21.0.5.0.10-3.el9.x86_64/bin/javac \
  -cp .:capstone.jar:jna-5.5.0.jar GraalLogParser.java

# run
/usr/lib/jvm/java-21-openjdk-21.0.5.0.10-3.el9.x86_64/bin/java \
  -cp .:capstone.jar:jna-5.5.0.jar \
  -Djna.library.path=/usr/local/lib64 \
  GraalLogParser
# -> Produces ProcessedMachineCode.csv
```

### BlockSummary

/usr/lib/jvm/java-21-openjdk-21.0.5.0.10-3.el9.x86_64/bin/javac BlockSummary.java
/usr/lib/jvm/java-21-openjdk-21.0.5.0.10-3.el9.x86_64/bin/java  BlockSummary
# -> Produces Compilation83_BlockSummary.csv


### MethodTimeFromMarkers

/usr/lib/jvm/java-21-openjdk-21.0.5.0.10-3.el9.x86_64/bin/javac MethodTimeFromMarkers.java
/usr/lib/jvm/java-21-openjdk-21.0.5.0.10-3.el9.x86_64/bin/java  MethodTimeFromMarkers
# -> Produces MethodTimeReport.csv and prints debug totals