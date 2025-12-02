package MachineCodeSourceExp;
import capstone.Capstone;

public class CapstoneDisasmDemo {
    public static void main(String[] args) {
        // Hex bytes to disassemble, from CLI or a default sample
        String hex = (args.length > 0) ? args[0] : "48 89 6c 24 10 48 83 c4 18 c3";
        byte[] code = parseHex(hex);

        // x86-64
        Capstone cs = new Capstone(Capstone.CS_ARCH_X86, Capstone.CS_MODE_64);
        Capstone.CsInsn[] insns = cs.disasm(code, 0);
        if (insns == null || insns.length == 0) {
            System.out.println("(no instructions)");
            return;
        }
        for (Capstone.CsInsn i : insns) {
            String opStr = (i.opStr == null || i.opStr.isEmpty()) ? "" : " " + i.opStr;
            System.out.println(String.format("0x%x:\t%s%s", i.address, i.mnemonic, opStr));
        }
        cs.close();
    }

    private static byte[] parseHex(String s) {
        String[] parts = s.trim().split("\\s+");
        byte[] out = new byte[parts.length];
        for (int i = 0; i < parts.length; i++) {
            out[i] = (byte) Integer.parseInt(parts[i], 16);
        }
        return out;
    }
}
