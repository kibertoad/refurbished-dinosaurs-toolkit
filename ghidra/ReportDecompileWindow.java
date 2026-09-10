// Prints an explicitly bounded line window from one focused decompilation.
// @category Rechaos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class ReportDecompileWindow extends GhidraScript {
    private static final int MAX_LINES = 160;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 3) {
            printerr("Supply a virtual address, one-based start line, and line count.");
            return;
        }

        Address address = toAddr(arguments[0]);
        int requestedStart = Integer.parseInt(arguments[1]);
        int requestedCount = Integer.parseInt(arguments[2]);
        if (requestedStart < 1 || requestedCount < 1 || requestedCount > MAX_LINES) {
            printerr("Start must be positive and count must be between 1 and " + MAX_LINES + ".");
            return;
        }

        Function function = currentProgram.getFunctionManager().getFunctionContaining(address);
        if (function == null) {
            printerr(arguments[0] + ": no containing function");
            return;
        }

        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try {
            DecompileResults result = decompiler.decompileFunction(function, 60, monitor);
            if (!result.decompileCompleted()) {
                printerr("Decompiler failed: " + result.getErrorMessage());
                return;
            }

            String[] lines = result.getDecompiledFunction().getC().split("\\R");
            int start = requestedStart - 1;
            if (start >= lines.length) {
                printerr("Start line " + requestedStart + " exceeds " + lines.length + " lines.");
                return;
            }
            int end = Math.min(lines.length, start + requestedCount);
            println("===== " + function.getEntryPoint() + " " + function.getName()
                + " lines " + (start + 1) + "-" + end + " of " + lines.length + " =====");
            for (int index = start; index < end; index++) {
                println((index + 1) + ": " + lines[index]);
            }
        }
        finally {
            decompiler.dispose();
        }
    }
}
