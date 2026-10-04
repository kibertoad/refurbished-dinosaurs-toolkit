// Prints focused decompiler output for explicitly supplied virtual addresses.
// Each function also gets its body ranges and every call whose next instruction lies outside the
// body, the usual sign that a wrong no-return flag cut the function short. The run ends with the
// addresses that had no function or failed to decompile.
// @category Restoration

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressRange;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.util.exception.CancelledException;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

public class ReportFunctionSummary extends GhidraScript {
    private static final int MAX_CUT_CALLS = 20;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length == 0) {
            printerr("Supply one or more virtual addresses, for example 0x00478cd0.");
            return;
        }

        List<String> missing = new ArrayList<>();
        List<String> failed = new ArrayList<>();
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try {
            for (String argument : arguments) {
                Address address = toAddr(argument);
                Function function = address == null ? null
                    : currentProgram.getFunctionManager().getFunctionContaining(address);
                if (function == null) {
                    printerr(argument + ": no containing function");
                    missing.add(argument);
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
                reportBody(function);
                DecompileResults result = decompiler.decompileFunction(function, 30, monitor);
                if (!result.decompileCompleted()) {
                    printerr("Decompiler failed: " + result.getErrorMessage());
                    failed.add(argument);
                    continue;
                }
                println(result.getDecompiledFunction().getC());
            }
        }
        finally {
            decompiler.dispose();
        }
        println("Summarized " + (arguments.length - missing.size() - failed.size()) + " of " + arguments.length
            + " addresses; no function at [" + String.join(", ", missing) + "]; decompiler failed at ["
            + String.join(", ", failed) + "]");
    }

    // The body Ghidra assigned, and each call without a fall-through whose next address is outside that body.
    private void reportBody(Function function) throws CancelledException {
        List<String> ranges = new ArrayList<>();
        for (AddressRange range : function.getBody()) ranges.add(range.getMinAddress() + ".." + range.getMaxAddress());
        println("Body: " + String.join(", ", ranges));
        int cut = 0;
        InstructionIterator instructions = currentProgram.getListing().getInstructions(function.getBody(), true);
        while (instructions.hasNext()) {
            monitor.checkCancelled();
            Instruction instruction = instructions.next();
            // Ghidra gives a call to a no-return function no fall-through. A tail jump that analysis
            // turned into a call has none either, so the instruction's own flow must be a call.
            if (!instruction.getFlowType().isCall()
                || !instruction.getPrototype().getFlowType(instruction.getInstructionContext()).isCall()) continue;
            Address next = instruction.getMaxAddress().next();
            if (next == null || function.getBody().contains(next) || instruction.getFallThrough() != null) continue;
            if (cut++ == MAX_CUT_CALLS) {
                println("  ... more calls without a continuation; listing capped at " + MAX_CUT_CALLS);
                break;
            }
            Instruction following = currentProgram.getListing().getInstructionAt(next);
            println("  call without a continuation in the body: " + instruction.getAddress() + " " + instruction
                + "; the bytes at " + next + " are "
                + (following == null ? "not disassembled" : "an instruction outside the body"));
        }
        if (cut > 0) {
            println("  The body may end early at these calls. Check the callees' no-return flags "
                + "(ClearNoReturnFunctions, RepairReturningCallers) before reading the decompilation as complete.");
        }
    }
}
