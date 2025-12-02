package MachineCodeSourceExp;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/**
 * Reads ProcessedMachineCode.csv and writes a per-block summary for
 * compilation_id == "HotSpotCompilation-83[Queens.benchmark()]"
 *
 * Output file: Compilation83_BlockSummary.csv
 *
 * For each block_id:
 *  - concatenated disassembly (all rows for that block)
 *  - total instruction count (split on ';')
 *  - distinct non-null sources
 *  - ratio of instructions with a non-null source
 *  - JSON map of source -> instruction count (includes "null" if any)
 */
public class BlockSummary {

    // ==== CONFIG ====
    private static final String INPUT  = "ProcessedMachineCode.csv";
    private static final String OUTPUT = "Queens.placeQueen(int).csv";
    private static final String TARGET_COMPILATION = "HotSpotCompilation-78[Queens.placeQueen(int)]";

    public static void main(String[] args) {
        new BlockSummary().run(Paths.get(INPUT), Paths.get(OUTPUT));
    }

    private static final int COL_COMPILATION = 0;
    private static final int COL_BLOCK       = 1;
    private static final int COL_LIR_CLASS   = 2;
    private static final int COL_BYTES       = 3;
    private static final int COL_HAS_SOURCE  = 4; // "true"/"false"
    private static final int COL_SOURCE      = 5;
    private static final int COL_DISASM      = 6;

    private static final class Agg {
        String blockId;
        StringBuilder disasm = new StringBuilder();
        long totalInstr = 0;
        long instrWithSource = 0;
        Map<String, Long> perSourceInstr = new LinkedHashMap<>();
    }

    private void run(Path in, Path out) {
        Map<String, Agg> byBlock = new LinkedHashMap<>();

        try (BufferedReader br = Files.newBufferedReader(in, StandardCharsets.UTF_8)) {
            String header = br.readLine(); // consume header
            if (header == null) {
                System.err.println("Empty input: " + in.toAbsolutePath());
                System.exit(1);
            }

            String line;
            while ((line = br.readLine()) != null) {
                List<String> cols = parseCsvLine(line);
                if (cols.size() < 7) continue; // skip malformed

                String compilation = cols.get(COL_COMPILATION);
                if (!TARGET_COMPILATION.equals(compilation)) continue;

                String blockId = cols.get(COL_BLOCK);
                String disasm  = cols.get(COL_DISASM);
                String hasSrcS = cols.get(COL_HAS_SOURCE);
                boolean hasSrc = "true".equalsIgnoreCase(hasSrcS);
                String source  = cols.get(COL_SOURCE);
                if (!hasSrc) source = "null"; // normalize

                if (blockId == null || blockId.isEmpty()) continue;

                Agg agg = byBlock.computeIfAbsent(blockId, k -> {
                    Agg a = new Agg();
                    a.blockId = k;
                    return a;
                });

                int insnsHere = countInstructions(disasm);
                if (insnsHere > 0) {
                    if (agg.disasm.length() > 0 && disasm != null && !disasm.isBlank()) {
                        agg.disasm.append("; ");
                    }
                    agg.disasm.append(disasm);

                    agg.totalInstr += insnsHere;
                    if (hasSrc) {
                        agg.instrWithSource += insnsHere;
                    }
                    // FIX: use Long::sum (or (a,b)->a+b)
                    agg.perSourceInstr.merge(source == null ? "null" : source,
                                             (long) insnsHere,
                                             Long::sum);
                }
            }

            try (BufferedWriter bw = Files.newBufferedWriter(out, StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE)) {

                bw.write("block_id,total_instructions,disasm,distinct_sources,has_source_ratio,source_counts_json");
                bw.newLine();

                for (Agg a : byBlock.values()) {
                    int distinctNonNull = 0;
                    for (String k : a.perSourceInstr.keySet()) {
                        if (!"null".equals(k)) distinctNonNull++;
                    }
                    double ratio = a.totalInstr == 0 ? 0.0 : ((double) a.instrWithSource) / a.totalInstr;

                    writeCsvRow(bw,
                            a.blockId,
                            String.valueOf(a.totalInstr),
                            a.disasm.toString(),
                            String.valueOf(distinctNonNull),
                            formatRatio(ratio),
                            toJson(a.perSourceInstr));
                }
            }

            System.out.println("Wrote -> " + out.toAbsolutePath());

        } catch (IOException e) {
            System.err.println("I/O error: " + e.getMessage());
            System.exit(2);
        }
    }

    // Count instructions by splitting disasm on ';'
    private static int countInstructions(String disasm) {
        if (disasm == null || disasm.isBlank()) return 0;
        int count = 0;
        for (String part : disasm.split(";")) {
            if (!part.trim().isEmpty()) count++;
        }
        return count;
    }

    private static String formatRatio(double r) {
        return String.format(java.util.Locale.ROOT, "%.3f", r);
    }

    private static String toJson(Map<String, Long> map) {
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        boolean first = true;
        for (Map.Entry<String, Long> e : map.entrySet()) {
            if (!first) sb.append(',');
            first = false;
            sb.append('"').append(jsonEscape(e.getKey())).append('"')
              .append(':')
              .append(e.getValue());
        }
        sb.append('}');
        return sb.toString();
    }

    private static String jsonEscape(String s) {
        if (s == null) return "null";
        StringBuilder sb = new StringBuilder(s.length() + 16);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"'  -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\b' -> sb.append("\\b");
                case '\f' -> sb.append("\\f");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                default -> {
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int)c));
                    else sb.append(c);
                }
            }
        }
        return sb.toString();
    }

    /** Minimal CSV parser for our writer’s format (quotes doubled inside quotes). */
    private static List<String> parseCsvLine(String line) {
        List<String> out = new ArrayList<>(8);
        StringBuilder cur = new StringBuilder();
        boolean inQuotes = false;

        for (int i = 0; i < line.length(); i++) {
            char ch = line.charAt(i);
            if (inQuotes) {
                if (ch == '"') {
                    if (i + 1 < line.length() && line.charAt(i + 1) == '"') {
                        cur.append('"'); i++;
                    } else {
                        inQuotes = false;
                    }
                } else {
                    cur.append(ch);
                }
            } else {
                if (ch == '"') {
                    inQuotes = true;
                } else if (ch == ',') {
                    out.add(cur.toString());
                    cur.setLength(0);
                } else {
                    cur.append(ch);
                }
            }
        }
        out.add(cur.toString());
        return out;
    }

    private static void writeCsvRow(BufferedWriter bw, String... fields) throws IOException {
        boolean first = true;
        for (String f : fields) {
            if (!first) bw.write(',');
            first = false;
            bw.write(csv(f));
        }
        bw.newLine();
    }

    private static String csv(String s) {
        if (s == null) return "\"\"";
        String escaped = s.replace("\"", "\"\"");
        return "\"" + escaped + "\"";
    }
}
