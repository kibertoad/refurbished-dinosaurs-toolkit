// Prints focused decompiler output for explicitly supplied virtual addresses.
// @category Rechaos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.util.Set;
import java.util.TreeSet;

public class ReportFunctionSummary extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length == 0) {
            printerr("Supply one or more virtual addresses, for example 0x00478cd0.");
            return;
        }

        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try {
            for (String argument : arguments) {
                Address address = toAddr(argument);
                Function function = currentProgram.getFunctionManager().getFunctionContaining(address);
                if (function == null) {
                    println(argument + ": no containing function");
                    continue;
                }

                println("===== " + function.getEntryPoint() + " " + function.getName() + " =====");
                Set<String> callers = new TreeSet<>();
                ReferenceIterator references = currentProgram.getReferenceManager()
                    .getReferencesTo(function.getEntryPoint());
                while (references.hasNext()) {
                    Reference reference = references.next();
                    Function caller = currentProgram.getFunctionManager()
                        .getFunctionContaining(reference.getFromAddress());
                    callers.add(caller == null
                        ? reference.getFromAddress().toString()
                        : caller.getEntryPoint() + " " + caller.getName()
                            + " at " + reference.getFromAddress());
                }
                println("Incoming references:");
                for (String caller : callers) {
                    println("  " + caller);
                }
                DecompileResults result = decompiler.decompileFunction(function, 30, monitor);
                if (!result.decompileCompleted()) {
                    println("Decompiler failed: " + result.getErrorMessage());
                    continue;
                }
                println(result.getDecompiledFunction().getC());
            }
        }
        finally {
            decompiler.dispose();
        }
    }
}
