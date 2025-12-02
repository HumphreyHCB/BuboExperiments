package MachineCodeSourceExp;
// --- only the changed/complete file for clarity ---
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.regex.*;

/**
 * Output CSV columns:
 *   VTuneBlockID,VtuneLineNumber,GraalBlockID,GraalLineNumber,VtuneASM,GraalASM,source,Time
 *
 * Alignment:
 *   1) Parse MarkerPhaseInfo.json leniently to map VTune block -> Graal block.
 *   2) Split Graal summary rows where ASM is "multi-instruction" (semicolon-separated)
 *      into per-instruction lines.
 *   3) For each mapped pair, align instructions by index (position).
 */
public class MakeAlignedRows {

    private static final String DEFAULT_TIMED   = "BlockInstructionTimes.csv";
    private static final String DEFAULT_SUMMARY = "Queens.placeQueen(int).csv";
    private static final String DEFAULT_MARKERS = "MarkerPhaseInfo.json";
    private static final String DEFAULT_OUT     = "AlignedRows.csv";

    // Timed CSV expected columns (flexible):
    private static final String[] T_BLOCK = {"block_id","BlockId","block","Block"};
    private static final String[] T_TEXT  = {"line_text","asm","disasm","instruction"};
    private static final String[] T_TIME  = {"line_time","time","time_sec"};

    // Summary CSV expected columns (flexible):
    private static final String[] S_BLOCK = {"block_id","BlockId","block","Block"};
    private static final String[] S_ASM   = {"asm","disasm","line_text","instruction"};
    private static final String[] S_SRC   = {"source","src"};

    private static final class TimedLine {
        String asm;
        Double time;
    }
    private static final class SummaryLine {
        String asm;
        String source;
    }

    public static void main(String[] args) {
        Path timedCsv   = Paths.get(args.length > 0 ? args[0] : DEFAULT_TIMED);
        Path summaryCsv = Paths.get(args.length > 1 ? args[1] : DEFAULT_SUMMARY);
        Path markerJson = Paths.get(args.length > 2 ? args[2] : DEFAULT_MARKERS);
        Path outCsv     = Paths.get(args.length > 3 ? args[3] : DEFAULT_OUT);

        try {
            Map<String, List<TimedLine>>   timedByVtune = loadTimed(timedCsv);
            Map<String, List<SummaryLine>> sumByGraal   = loadSummaryExploded(summaryCsv);

            Map<String,String> vtuneToGraal = parseMarkerMapLenient(markerJson);
            if (!vtuneToGraal.isEmpty()) {
                long vtLike = vtuneToGraal.keySet().stream().filter(MakeAlignedRows::isLikelyId).count();
                long grLike = vtuneToGraal.values().stream().filter(MakeAlignedRows::isLikelyId).count();
                if (vtLike < grLike) { // looks inverted → invert
                    Map<String,String> inv = new LinkedHashMap<>();
                    for (var e : vtuneToGraal.entrySet()) inv.put(e.getValue(), e.getKey());
                    vtuneToGraal = inv;
                }
            }

            System.out.println("=== Align (VTune ↔ Graal, with summary ASM exploded) ===");
            System.out.println("VTune blocks in timed CSV : " + timedByVtune.size());
            System.out.println("Graal blocks in summary   : " + sumByGraal.size());
            System.out.println("Marker mappings available  : " + vtuneToGraal.size());

            try (BufferedWriter bw = Files.newBufferedWriter(outCsv, StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE)) {
                writeRow(bw, "VTuneBlockID","VtuneLineNumber","GraalBlockID","GraalLineNumber","VtuneASM","GraalASM","source","Time");

                for (var e : timedByVtune.entrySet()) {
                    String vtKey = normalizeBlockId(e.getKey());
                    List<TimedLine> vtLines = e.getValue();

                    String grKey = vtuneToGraal.getOrDefault(vtKey, "");
                    List<SummaryLine> grLines = sumByGraal.getOrDefault(grKey, Collections.emptyList());

                    int max = Math.max(vtLines.size(), grLines.size());
                    for (int i = 0; i < max; i++) {
                        TimedLine   vt = (i < vtLines.size()) ? vtLines.get(i) : null;
                        SummaryLine gr = (i < grLines.size()) ? grLines.get(i) : null;

                        writeRow(bw,
                                vtKey,
                                vt != null ? Integer.toString(i) : "",
                                grKey,
                                gr != null ? Integer.toString(i) : "",
                                vt != null ? nz(vt.asm) : "",
                                gr != null ? nz(gr.asm) : "",
                                gr != null ? nz(gr.source) : "",
                                (vt != null && vt.time != null) ? fmt3(vt.time) : ""
                        );
                    }
                }
            }

            System.out.println("Wrote -> " + outCsv.toAbsolutePath());

        } catch (IOException ex) {
            System.err.println("I/O error: " + ex.getMessage());
            System.exit(2);
        }
    }

    // ---------- Marker parsing (lenient) ----------
    private static Map<String,String> parseMarkerMapLenient(Path jsonPath) throws IOException {
        Map<String,String> map = new LinkedHashMap<>();
        if (!Files.exists(jsonPath)) return map;

        String txt = Files.readString(jsonPath, StandardCharsets.UTF_8);
        int i = 0, n = txt.length();
        while (i < n) {
            int start = txt.indexOf('{', i);
            if (start < 0) break;
            int depth = 0, j = start;
            for (; j < n; j++) {
                char ch = txt.charAt(j);
                if (ch == '{') depth++;
                else if (ch == '}') { depth--; if (depth == 0) { j++; break; } }
            }
            if (depth != 0) break;
            String obj = txt.substring(start, j);

            List<String> vtCandidates = new ArrayList<>();
            List<String> grCandidates = new ArrayList<>();

            Matcher kv = Pattern.compile("\"([^\"]+)\"\\s*:\\s*(\"([^\"]*)\"|(-?\\d+))").matcher(obj);
            while (kv.find()) {
                String key = kv.group(1).toLowerCase(Locale.ROOT);
                String raw = kv.group(3) != null ? kv.group(3) : kv.group(4);
                if (raw == null) continue;
                String id = extractFirstInt(raw);
                if (id == null) continue;

                if (key.contains("vtune")) vtCandidates.add(id);
                else if (key.contains("graal") || (key.contains("hotspot") && key.contains("block")) || (key.contains("block") && !key.contains("size"))) {
                    grCandidates.add(id);
                }
            }
            if (!vtCandidates.isEmpty() && !grCandidates.isEmpty()) {
                map.put(vtCandidates.get(0), grCandidates.get(0));
            }
            i = j;
        }
        return map;
    }

    private static String extractFirstInt(String s) {
        Matcher m = Pattern.compile("(\\d+)").matcher(s);
        return m.find() ? m.group(1) : null;
    }
    private static String normalizeBlockId(String s) {
        if (s == null) return "";
        String id = extractFirstInt(s);
        return id != null ? id : s.trim();
    }
    private static boolean isLikelyId(String s) { return s != null && s.matches("\\d+"); }

    // ---------- Loaders ----------
    private static Map<String, List<TimedLine>> loadTimed(Path csv) throws IOException {
        Map<String, List<TimedLine>> byBlock = new LinkedHashMap<>();
        try (BufferedReader br = Files.newBufferedReader(csv, StandardCharsets.UTF_8)) {
            String header = br.readLine();
            if (header == null) return byBlock;
            List<String> h = parseCsvLine(header);

            int iBlock = findCol(h, T_BLOCK);
            int iAsm   = findCol(h, T_TEXT);
            int iTime  = findCol(h, T_TIME);
            if (iBlock < 0 || iAsm < 0) {
                throw new IOException("Timed CSV missing required columns: block_id and asm/disasm/line_text");
            }

            String line;
            while ((line = br.readLine()) != null) {
                List<String> c = parseCsvLine(line);
                if (c.isEmpty()) continue;

                String block = get(c, iBlock);
                String asm   = get(c, iAsm);
                String time  = get(c, iTime);
                if (block == null || asm == null) continue;

                String vtKey = normalizeBlockId(block);

                TimedLine tl = new TimedLine();
                tl.asm  = asm.trim();
                tl.time = parseMaybeDouble(time);

                byBlock.computeIfAbsent(vtKey, k -> new ArrayList<>()).add(tl);
            }
        }
        return byBlock;
    }

    // NEW: explode semicolon-separated ASM into individual instructions per block
    private static Map<String, List<SummaryLine>> loadSummaryExploded(Path csv) throws IOException {
        Map<String, List<SummaryLine>> byBlock = new LinkedHashMap<>();
        try (BufferedReader br = Files.newBufferedReader(csv, StandardCharsets.UTF_8)) {
            String header = br.readLine();
            if (header == null) return byBlock;
            List<String> h = parseCsvLine(header);

            int iBlock = findCol(h, S_BLOCK);
            int iAsm   = findCol(h, S_ASM);
            int iSrc   = findCol(h, S_SRC);
            if (iBlock < 0 || iAsm < 0) {
                throw new IOException("Summary CSV missing required columns: block_id and asm/disasm/line_text");
            }

            String line;
            while ((line = br.readLine()) != null) {
                List<String> c = parseCsvLine(line);
                if (c.isEmpty()) continue;

                String block = get(c, iBlock);
                String asm   = get(c, iAsm);
                String src   = (iSrc >= 0 ? get(c, iSrc) : null);
                if (block == null || asm == null) continue;

                String grKey = normalizeBlockId(block);
                List<String> pieces = splitAsmToInstructions(asm);

                for (String piece : pieces) {
                    SummaryLine sl = new SummaryLine();
                    sl.asm    = piece;
                    sl.source = nz(src);
                    byBlock.computeIfAbsent(grKey, k -> new ArrayList<>()).add(sl);
                }
            }
        }
        return byBlock;
    }

    // Robust splitting: split on ';' and trim, keep non-empty parts
    private static List<String> splitAsmToInstructions(String asm) {
        List<String> out = new ArrayList<>();
        if (asm == null) return out;
        // Normalize whitespace around semicolons to make splitting more predictable
        String norm = asm.replaceAll("\\s*;\\s*", ";");
        for (String p : norm.split(";")) {
            String s = p.trim();
            if (!s.isEmpty()) out.add(s);
        }
        return out;
    }

    // ---------- CSV utils ----------
    private static List<String> parseCsvLine(String line) {
        List<String> out = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        boolean inQuotes = false;
        for (int i = 0; i < line.length(); i++) {
            char ch = line.charAt(i);
            if (inQuotes) {
                if (ch == '"') {
                    if (i + 1 < line.length() && line.charAt(i + 1) == '"') { cur.append('"'); i++; }
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

    private static void writeRow(BufferedWriter bw, String... fields) throws IOException {
        boolean first = true;
        for (String f : fields) {
            if (!first) bw.write(',');
            first = false;
            String v = (f == null ? "" : f).replace("\"", "\"\"");
            bw.write('"'); bw.write(v); bw.write('"');
        }
        bw.newLine();
    }

    private static int findCol(List<String> headers, String[] candidates) {
        Map<String,Integer> map = new HashMap<>();
        for (int i = 0; i < headers.size(); i++) {
            map.put(headers.get(i).trim().toLowerCase(Locale.ROOT), i);
        }
        for (String c : candidates) {
            Integer idx = map.get(c.toLowerCase(Locale.ROOT));
            if (idx != null) return idx;
        }
        return -1;
    }

    private static String get(List<String> list, int idx) {
        if (idx < 0 || idx >= list.size()) return null;
        return list.get(idx);
    }

    private static Double parseMaybeDouble(String s) {
        if (s == null || s.isBlank()) return null;
        try { return Double.parseDouble(s.trim()); } catch (Exception e) { return null; }
    }

    private static String nz(String s) { return s == null ? "" : s; }
    private static String fmt3(double d) { return String.format(Locale.ROOT, "%.3f", d); }
}
