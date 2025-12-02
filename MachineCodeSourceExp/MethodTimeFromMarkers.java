package MachineCodeSourceExp;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.regex.*;

public class MethodTimeFromMarkers {

    private static final String BLOCK_SUMMARY_CSV = "Queens.placeQueen(int).csv";
    private static final String MARKER_JSON        = "MarkerPhaseInfo.json";
    private static final String OUTPUT_CSV_ORIG    = "MethodTimeReport.csv";
    private static final String OUTPUT_CSV_REASS   = "MethodTimeReport_BestGuess.csv";

    private static final int COL_BLOCK_ID           = 0;
    private static final int COL_TOTAL_INSTR        = 1;
    private static final int COL_DISASM             = 2;
    private static final int COL_DISTINCT_SOURCES   = 3;
    private static final int COL_HAS_SOURCE_RATIO   = 4;
    private static final int COL_SOURCE_COUNTS_JSON = 5;

    private static final class BlockMix {
        final String blockId;
        final long totalInstr;
        final LinkedHashMap<String, Long> perSourceInstr; // keep order
        BlockMix(String blockId, long totalInstr, LinkedHashMap<String, Long> perSourceInstr) {
            this.blockId = blockId; this.totalInstr = totalInstr; this.perSourceInstr = perSourceInstr;
        }
    }

    private static final class Marker {
        final String graalId;
        final double baseCpuTime;
        Marker(String graalId, double baseCpuTime) {
            this.graalId = graalId; this.baseCpuTime = baseCpuTime;
        }
    }

    public static void main(String[] args) {
        try {
            Map<String, BlockMix> blocks = loadBlockSummary(Paths.get(BLOCK_SUMMARY_CSV));
            List<Marker> markers = loadMarkers(Paths.get(MARKER_JSON));

            int totalMarkers = markers.size();
            double totalMarkerTime = markers.stream().mapToDouble(m -> m.baseCpuTime).sum();

            // --- ORIGINAL ATTRIBUTION ---
            Result orig = attribute(markers, blocks, /*reassignNull*/ false);
            writeReport(OUTPUT_CSV_ORIG, orig.methodTime, orig.methodToBlocks, totalMarkerTime);

            // --- REASSIGNED ATTRIBUTION (null -> next non-null in block) ---
            Result reass = attribute(markers, blocks, /*reassignNull*/ true);
            writeReport(OUTPUT_CSV_REASS, reass.methodTime, reass.methodToBlocks, totalMarkerTime);

            // --- Console summary / deltas ---
            System.out.println("=== MethodTimeFromMarkers ===");
            System.out.println("Markers (total): " + totalMarkers);
            System.out.println("Total BaseCpuTime (s): " + fmt3(totalMarkerTime));
            System.out.println();
            System.out.println("Original matching:");
            System.out.println("  Matched blocks:   " + orig.matched + "   time(s): " + fmt3(orig.matchedTime));
            System.out.println("  Unmatched blocks: " + orig.unmatched + "   time(s): " + fmt3(orig.unmatchedTime) + "   <-- unaccounted");
            System.out.println("  Unknown-source ('null') time within matched (s): " + fmt3(orig.unknownSourceTime));

            System.out.println();
            System.out.println("Reassigned (null -> next source in block):");
            System.out.println("  Matched blocks:   " + reass.matched + "   time(s): " + fmt3(reass.matchedTime));
            System.out.println("  Unmatched blocks: " + reass.unmatched + "   time(s): " + fmt3(reass.unmatchedTime) + "   <-- unaccounted");
            System.out.println("  Unknown-source ('null') time within matched (s): " + fmt3(reass.unknownSourceTime));

            double origAttributed   = orig.matchedTime - orig.unknownSourceTime;
            double reassAttributed  = reass.matchedTime - reass.unknownSourceTime;
            double gain             = reassAttributed - origAttributed;

            System.out.println();
            System.out.println("Attribution delta (non-null time):");
            System.out.println("  Original attributed (s):   " + fmt3(origAttributed));
            System.out.println("  Reassigned attributed (s): " + fmt3(reassAttributed));
            System.out.println("  Gain from reassignment (s): " + fmt3(gain));
            System.out.println();
            System.out.println("Output CSVs:");
            System.out.println("  - " + Paths.get(OUTPUT_CSV_ORIG).toAbsolutePath());
            System.out.println("  - " + Paths.get(OUTPUT_CSV_REASS).toAbsolutePath());

        } catch (IOException e) {
            System.err.println("I/O error: " + e.getMessage());
            System.exit(2);
        }
    }

    private static final class Result {
        int matched = 0, unmatched = 0;
        double matchedTime = 0.0, unmatchedTime = 0.0;
        double unknownSourceTime = 0.0;

        Map<String, Double> methodTime = new LinkedHashMap<>();
        Map<String, Set<String>> methodToBlocks = new HashMap<>();
    }

    /** Attribute marker time to methods using per-block instruction shares. Optionally reassign 'null' to next non-null source (block order). */
    private static Result attribute(List<Marker> markers, Map<String, BlockMix> blocks, boolean reassignNull) {
        Result r = new Result();
        for (Marker m : markers) {
            BlockMix mix = blocks.get(m.graalId);
            if (mix == null || mix.totalInstr <= 0 || mix.perSourceInstr.isEmpty()) {
                r.unmatched++;
                r.unmatchedTime += m.baseCpuTime;
                continue;
            }
            r.matched++;
            r.matchedTime += m.baseCpuTime;

            long total = mix.totalInstr;

            // Build per-source counts for this pass (may reassign null)
            LinkedHashMap<String, Long> counts = new LinkedHashMap<>(mix.perSourceInstr);
            if (reassignNull && counts.containsKey("null")) {
                reassignNullForward(counts); // modifies counts in-place
            }

            for (Map.Entry<String, Long> e : counts.entrySet()) {
                String src = e.getKey();
                long cnt = e.getValue() == null ? 0 : e.getValue();
                if (cnt <= 0) continue;

                double share = (double) cnt / (double) total;
                double t = m.baseCpuTime * share;

                if ("null".equals(src)) {
                    r.unknownSourceTime += t; // remaining unattributed time
                    continue;
                }

                String method = methodKeyFromSource(src);
                r.methodTime.merge(method, t, Double::sum);
                r.methodToBlocks.computeIfAbsent(method, k -> new HashSet<>()).add(m.graalId);
            }
        }
        return r;
    }

    /** Reassign all 'null' count to the next non-null source in iteration order (if any). */
    private static void reassignNullForward(LinkedHashMap<String, Long> counts) {
        long nullCnt = counts.getOrDefault("null", 0L);
        if (nullCnt <= 0) return;

        boolean seenNull = false;
        for (Map.Entry<String, Long> e : counts.entrySet()) {
            String key = e.getKey();
            if (!seenNull) {
                if ("null".equals(key)) {
                    seenNull = true;
                }
                continue;
            }
            // first entry after "null"
            if (!"null".equals(key)) {
                counts.put("null", 0L);
                counts.put(key, e.getValue() == null ? nullCnt : e.getValue() + nullCnt);
                return;
            }
        }
        // if we reach here: null was last (or only) -> keep as null
    }

    private static void writeReport(String outPath,
                                    Map<String, Double> methodTime,
                                    Map<String, Set<String>> methodToBlocks,
                                    double totalMarkerTime) throws IOException {

        Map<String, Integer> methodBlocks = new LinkedHashMap<>();
        for (Map.Entry<String, Set<String>> e : methodToBlocks.entrySet()) {
            methodBlocks.put(e.getKey(), e.getValue().size());
        }

        try (BufferedWriter bw = Files.newBufferedWriter(Paths.get(outPath), StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE)) {

            bw.write("method,time,percent_of_total,blocks_contributing");
            bw.newLine();

            List<Map.Entry<String, Double>> rows = new ArrayList<>(methodTime.entrySet());
            rows.sort((a,b) -> Double.compare(b.getValue(), a.getValue()));

            for (Map.Entry<String, Double> e : rows) {
                String method = e.getKey();
                double t = e.getValue();
                double pct = (totalMarkerTime == 0.0) ? 0.0 : (t * 100.0 / totalMarkerTime);
                int blocksContrib = methodBlocks.getOrDefault(method, 0);

                writeCsvRow(bw,
                        method,
                        fmt3(t),
                        fmt2(pct),
                        Integer.toString(blocksContrib));
            }
        }
    }

    // ---- Loaders ----
    private static Map<String, BlockMix> loadBlockSummary(Path csv) throws IOException {
        Map<String, BlockMix> map = new LinkedHashMap<>();
        try (BufferedReader br = Files.newBufferedReader(csv, StandardCharsets.UTF_8)) {
            String header = br.readLine();
            if (header == null) return map;
            String line;
            while ((line = br.readLine()) != null) {
                List<String> cols = parseCsvLine(line);
                if (cols.size() < 6) continue;

                String blockId = cols.get(COL_BLOCK_ID);
                long totalInstr = 0;
                try { totalInstr = Long.parseLong(cols.get(COL_TOTAL_INSTR)); } catch (Exception ignore) {}

                String json = cols.get(COL_SOURCE_COUNTS_JSON);
                LinkedHashMap<String, Long> perSource = parseSimpleJsonMapQuoteAware(json);

                if (blockId != null && !blockId.isEmpty()) {
                    map.put(blockId, new BlockMix(blockId, totalInstr, perSource));
                }
            }
        }
        return map;
    }

    private static List<Marker> loadMarkers(Path json) throws IOException {
        String text = Files.readString(json, StandardCharsets.UTF_8);
        List<Marker> out = new ArrayList<>();
        Matcher mObj = Pattern.compile("\\{[^{}]*\\}").matcher(text);
        Pattern pId   = Pattern.compile("\"GraalID\"\\s*:\\s*\"?(\\d+)\"?");
        Pattern pTime = Pattern.compile("\"BaseCpuTime\"\\s*:\\s*\"?(\\d+(?:\\.\\d+)?)\"?");
        while (mObj.find()) {
            String obj = mObj.group();
            Matcher mi = pId.matcher(obj);
            Matcher mt = pTime.matcher(obj);
            if (mi.find() && mt.find()) {
                String id = mi.group(1);
                double t  = Double.parseDouble(mt.group(1));
                out.add(new Marker(id, t));
            }
        }
        return out;
    }

    // --- JSON map parser (order-preserving, quote-aware) ---
    private static LinkedHashMap<String, Long> parseSimpleJsonMapQuoteAware(String s) {
        LinkedHashMap<String, Long> map = new LinkedHashMap<>();
        if (s == null) return map;
        s = s.trim();
        if (s.length() < 2 || s.charAt(0) != '{' || s.charAt(s.length()-1) != '}') return map;

        List<String> pairs = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        boolean inQuotes = false;
        for (int i = 1; i < s.length()-1; i++) {
            char c = s.charAt(i);
            if (c == '"' && (i == 1 || s.charAt(i-1) != '\\')) {
                inQuotes = !inQuotes; cur.append(c);
            } else if (c == ',' && !inQuotes) {
                pairs.add(cur.toString()); cur.setLength(0);
            } else {
                cur.append(c);
            }
        }
        pairs.add(cur.toString());

        for (String p : pairs) {
            if (p.isBlank()) continue;

            int sep = -1; inQuotes = false;
            for (int i = 0; i < p.length(); i++) {
                char c = p.charAt(i);
                if (c == '"' && (i == 0 || p.charAt(i-1) != '\\')) inQuotes = !inQuotes;
                else if (c == ':' && !inQuotes) { sep = i; break; }
            }
            if (sep < 0) continue;

            String k = p.substring(0, sep).trim();
            String v = p.substring(sep + 1).trim();

            String key = unquote(k);
            try {
                long n = Long.parseLong(v);
                map.put(key, n);
            } catch (NumberFormatException ignore) { /* skip */ }
        }
        return map;
    }

    private static String unquote(String s) {
        s = s.trim();
        if (s.startsWith("\"") && s.endsWith("\"") && s.length() >= 2) {
            s = s.substring(1, s.length()-1).replace("\\\"", "\"").replace("\\\\", "\\");
        }
        return s;
    }

    // --- Utility formatting / CSV ---
    private static String methodKeyFromSource(String src) {
        if (src == null || src.isBlank()) return "(unknown)";
        String s = src.trim();
        if (s.startsWith("at ")) s = s.substring(3).trim();
        int paren = s.indexOf('(');
        if (paren > 0) s = s.substring(0, paren);
        int sp = s.indexOf(' ');
        if (sp > 0) s = s.substring(0, sp);
        return s.isEmpty() ? "(unknown)" : s;
    }

    private static String fmt3(double d) { return String.format(Locale.ROOT, "%.3f", d); }
    private static String fmt2(double d) { return String.format(Locale.ROOT, "%.2f", d); }

    private static List<String> parseCsvLine(String line) {
        List<String> out = new ArrayList<>(8);
        StringBuilder cur = new StringBuilder();
        boolean inQuotes = false;
        for (int i = 0; i < line.length(); i++) {
            char ch = line.charAt(i);
            if (inQuotes) {
                if (ch == '"') {
                    if (i+1 < line.length() && line.charAt(i+1) == '"') { cur.append('"'); i++; }
                    else inQuotes = false;
                } else cur.append(ch);
            } else {
                if (ch == '"') inQuotes = true;
                else if (ch == ',') { out.add(cur.toString()); cur.setLength(0); }
                else cur.append(ch);
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
            String escaped = (f == null ? "" : f).replace("\"", "\"\"");
            bw.write('"'); bw.write(escaped); bw.write('"');
        }
        bw.newLine();
    }
}
