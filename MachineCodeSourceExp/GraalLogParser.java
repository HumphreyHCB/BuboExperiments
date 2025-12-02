package MachineCodeSourceExp;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.regex.*;

import capstone.Capstone;

public class GraalLogParser {

    // ==== Fixed paths you asked for ====
    public static void main(String[] args) {
        Path in  = Paths.get("QueensRawMachineCodeDump.txt");
        Path out = Paths.get("ProcessedMachineCode.csv");
        new GraalLogParser().run(in, out);
    }

    // ==== Patterns ====
    private static final Pattern COMPILATION_RE =
            Pattern.compile("^\\s*CompilationId\\s*:(.*)$");

    private static final Pattern BLOCK_RE =
            Pattern.compile("^\\s*Block ID\\s*:(\\d+)\\s*$");

    // Example:
    // Emitted code for class jdk.graal.compiler.lir.amd64.AMD64Move$MoveFromRegOp : 48 89 6c 24 10  source: null
    // Emitted code for class ... : <hex bytes>  source: <text or null>
    private static final Pattern EMITTED_RE =
            Pattern.compile("^\\s*Emitted code for class\\s+(.+?)\\s*:\\s*([0-9A-Fa-f]{2}(?:\\s+[0-9A-Fa-f]{2})*)\\s+source:\\s*(.*)\\s*$");

    // ==== Disassembler ====
    private interface Disassembler extends AutoCloseable {
        String disasm(byte[] code) throws Exception;
        @Override default void close() {}
    }

    private static final class CapstoneDisassembler implements Disassembler {
        private final Capstone cs = new Capstone(Capstone.CS_ARCH_X86, Capstone.CS_MODE_64);

        @Override
        public String disasm(byte[] code) {
            if (code == null || code.length == 0) return "";
            Capstone.CsInsn[] insns = cs.disasm(code, 0);
            if (insns == null || insns.length == 0) return "";
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < insns.length; i++) {
                if (i > 0) sb.append("; ");
                String mnem = insns[i].mnemonic == null ? "" : insns[i].mnemonic;
                String ops  = (insns[i].opStr == null || insns[i].opStr.isEmpty()) ? "" : " " + insns[i].opStr;
                sb.append(mnem).append(ops);
            }
            return sb.toString();
        }

        @Override public void close() {
            try { cs.close(); } catch (Exception ignore) {}
        }
    }

    private void run(Path in, Path out) {
        String currentCompilation = null;
        String currentBlockId = null;

        try (Disassembler ds = new CapstoneDisassembler();
             BufferedReader br = Files.newBufferedReader(in, StandardCharsets.UTF_8);
             BufferedWriter bw = Files.newBufferedWriter(out, StandardCharsets.UTF_8,
                     StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE)) {

            // CSV header
            bw.write("compilation_id,block_id,lir_class,bytes,has_source,source,disasm");
            bw.newLine();

            String line;
            while ((line = br.readLine()) != null) {
                // Match in structural order
                var mComp = COMPILATION_RE.matcher(line);
                if (mComp.matches()) {
                    currentCompilation = mComp.group(1).trim();
                    continue;
                }

                var mBlock = BLOCK_RE.matcher(line);
                if (mBlock.matches()) {
                    currentBlockId = mBlock.group(1);
                    continue;
                }

                var mEmit = EMITTED_RE.matcher(line);
                if (mEmit.matches()) {
                    if (currentCompilation == null || currentBlockId == null) {
                        // orphaned 'Emitted code' → ignore as noise
                        continue;
                    }

                    String lirClass   = mEmit.group(1).trim();
                    String bytesText  = normalizeSpaces(mEmit.group(2).trim());
                    String sourceText = mEmit.group(3).trim();
                    boolean hasSource = !sourceText.equalsIgnoreCase("null");

                    String disasm;
                    try {
                        disasm = ds.disasm(hexToBytes(bytesText));
                    } catch (Throwable t) {
                        disasm = "(disasm error: " + t.getMessage() + ")";
                    }

                    writeCsvRow(bw, currentCompilation, currentBlockId, lirClass,
                                bytesText, hasSource, sourceText, disasm);
                    continue;
                }

                // everything else = noise
            }

            System.out.println("Parsed OK -> " + out.toAbsolutePath());

        } catch (IOException e) {
            System.err.println("I/O error: " + e.getMessage());
            System.exit(2);
        }
    }

    private static void writeCsvRow(BufferedWriter bw,
                                    String compilationId,
                                    String blockId,
                                    String lirClass,
                                    String bytes,
                                    boolean hasSource,
                                    String source,
                                    String disasm) throws IOException {
        bw.write(csv(compilationId)); bw.write(',');
        bw.write(csv(blockId));       bw.write(',');
        bw.write(csv(lirClass));      bw.write(',');
        bw.write(csv(bytes));         bw.write(',');
        bw.write(hasSource ? "true" : "false"); bw.write(',');
        bw.write(csv(source));        bw.write(',');
        bw.write(csv(disasm));
        bw.newLine();
    }

    private static String csv(String s) {
        if (s == null) return "\"\"";
        String escaped = s.replace("\"", "\"\"");
        return "\"" + escaped + "\"";
    }

    private static String normalizeSpaces(String s) {
        return s.trim().replaceAll("\\s+", " ");
    }

    private static byte[] hexToBytes(String hexWithSpaces) {
        String[] parts = hexWithSpaces.split("\\s+");
        byte[] out = new byte[parts.length];
        for (int i = 0; i < parts.length; i++) {
            out[i] = (byte) Integer.parseInt(parts[i], 16);
        }
        return out;
    }
}
