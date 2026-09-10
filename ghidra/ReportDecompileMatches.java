// Prints bounded context around requested text in one focused decompilation.
// @category Rechaos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

import java.util.LinkedHashSet;
import java.util.Set;

public class ReportDecompileMatches extends GhidraScript {
    private static final int CONTEXT_LINES = 2;
    private static final int MAX_LINES = 240;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length < 2) {
            printerr("Supply a virtual address followed by one or more literal text patterns.");
            return;
        }

        Address address = toAddr(arguments[0]);
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
            Set<Integer> selected = new LinkedHashSet<>();
            for (int index = 0; index < lines.length; index++) {
                for (int pattern = 1; pattern < arguments.length; pattern++) {
                    if (lines[index].contains(arguments[pattern])) {
                        for (int context = Math.max(0, index - CONTEXT_LINES);
                             context <= Math.min(lines.length - 1, index + CONTEXT_LINES);
                             context++) selected.add(context);
                    }
                }
            }

            println("===== " + function.getEntryPoint() + " " + function.getName() + " =====");
            int emitted = 0;
            int previous = -2;
            for (int index : selected) {
                if (emitted++ >= MAX_LINES) {
                    println("... output capped at " + MAX_LINES + " lines");
                    break;
                }
                if (index > previous + 1) println("...");
                println((index + 1) + ": " + lines[index]);
                previous = index;
            }
            if (selected.isEmpty()) println("No requested patterns matched.");
        }
        finally {
            decompiler.dispose();
        }
    }
}
