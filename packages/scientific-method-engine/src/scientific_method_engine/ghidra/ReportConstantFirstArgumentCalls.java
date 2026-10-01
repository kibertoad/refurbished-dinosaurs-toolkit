// Reports direct x86 cdecl calls whose first argument is one requested constant.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

public class ReportConstantFirstArgumentCalls extends GhidraScript {
    private static final int MAX_CALLS = 300;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 2) {
            printerr("Supply a callee virtual address and constant first argument.");
            return;
        }

        Address callee = toAddr(arguments[0]);
        long requested = Long.decode(arguments[1]);
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(callee);
        int matches = 0;
        while (references.hasNext() && matches < MAX_CALLS && !monitor.isCancelled()) {
            Reference reference = references.next();
            Instruction call = currentProgram.getListing().getInstructionAt(reference.getFromAddress());
            if (call == null || !call.getFlowType().isCall()) continue;
            Instruction firstPush = call.getPrevious();
            if (!isPushOf(firstPush, requested)) continue;

            Function function = currentProgram.getFunctionManager().getFunctionContaining(call.getAddress());
            Instruction secondPush = firstPush.getPrevious();
            println(call.getAddress()
                + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName())
                + " first=" + firstPush
                + (secondPush == null ? "" : " preceding=" + secondPush));
            matches++;
        }

        println("Matched calls: " + matches);
        if (matches == MAX_CALLS) println("Output capped at " + MAX_CALLS + " calls.");
    }

    private static boolean isPushOf(Instruction instruction, long requested) {
        if (instruction == null || !"PUSH".equals(instruction.getMnemonicString())) return false;
        for (Object object : instruction.getOpObjects(0)) {
            if (object instanceof Scalar scalar
                && scalar.getUnsignedValue() == requested) return true;
        }
        return false;
    }
}
