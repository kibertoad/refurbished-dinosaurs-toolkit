// Prints a bounded byte range at an explicitly supplied virtual address.
// @category Rechaos

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;

public class ReportDataBytes extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 2) {
            printerr("Supply a virtual address and byte count, for example 0x00494818 24.");
            return;
        }

        Address address = toAddr(arguments[0]);
        int count = Integer.decode(arguments[1]);
        if (count < 1 || count > 256) {
            printerr("Byte count must be between 1 and 256.");
            return;
        }

        byte[] bytes = new byte[count];
        currentProgram.getMemory().getBytes(address, bytes);
        println("===== " + address + " (" + count + " bytes) =====");
        for (int offset = 0; offset < bytes.length; offset += 16) {
            StringBuilder line = new StringBuilder();
            line.append(address.add(offset)).append(":");
            int end = Math.min(offset + 16, bytes.length);
            for (int index = offset; index < end; index++) {
                line.append(String.format(" %02x", bytes[index] & 0xff));
            }
            println(line.toString());
        }
    }
}
