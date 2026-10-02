// Prints an explicitly bounded line window from one focused decompilation.
// @category Restoration

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
        if (requestedStart < 1 || requestedCount < 1) {
            printerr("Start and count must be positive.");
            return;
        }
        // A function's decompiled length is unknown before the run, so a larger count is cut to the cap
        // and the output says where the next window starts.
        if (requestedCount > MAX_LINES) {
            println("Line count " + requestedCount + " cut to the cap of " + MAX_LINES + ".");
            requestedCount = MAX_LINES;
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
            if (end < lines.length) {
                println("===== " + (lines.length - end) + " more lines; the next window starts at line " + (end + 1)
                    + " =====");
            }
        }
        finally {
            decompiler.dispose();
        }
    }
}
