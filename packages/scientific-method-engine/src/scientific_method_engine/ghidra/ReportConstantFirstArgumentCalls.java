// Reports direct x86 cdecl calls whose first argument is one requested constant.
// The first argument is the nearest PUSH before the call. Instructions between them are passed over
// only when they fall through, are not a flow target and write neither the stack pointer nor
// memory addressed through it or through a register copied from it after the PUSH (MOV [ESP],EAX;
// LEA EAX,[ESP] then MOV [EAX],ECX), as in "PUSH 5; MOV ECX,ESI; CALL".
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.OperandType;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import scientificmethod.FirstArgumentLookBack;

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
        FirstArgumentLookBack lookBack = new FirstArgumentLookBack(currentProgram);
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(callee);
        int matches = 0;
        int calls = 0;
        int unknown = 0;
        boolean capped = false;
        while (references.hasNext()) {
            monitor.checkCancelled();
            Reference reference = references.next();
            Instruction call = currentProgram.getListing().getInstructionAt(reference.getFromAddress());
            if (call == null || !call.getFlowType().isCall()) continue;
            calls++;
            Instruction firstPush = lookBack.firstArgumentPush(call);
            if (firstPush == null) unknown++;
            if (!isPushOf(firstPush, requested)) continue;
            if (matches >= MAX_CALLS) {
                capped = true;
                break;
            }

            Function function = currentProgram.getFunctionManager().getFunctionContaining(call.getAddress());
            Instruction secondPush = firstPush.getPrevious();
            println(call.getAddress()
                + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName())
                + " first=" + firstPush
                + (secondPush == null ? "" : " preceding=" + secondPush));
            matches++;
        }

        println("Matched calls: " + matches);
        if (capped) println("Output capped at " + MAX_CALLS + " calls; the scan did not finish.");
        else println("Calls read: " + calls + ", of which " + unknown + " have no PUSH known to supply the "
            + "first argument (see ReportFirstArgumentCallSummary).");
    }

    // Only an immediate operand is a literal; PUSH [EBP+8] carries the scalar 8 as a displacement.
    // Ghidra types an immediate operand SCALAR, adding ADDRESS when it points into the program
    // (PUSH 0x41c000). A memory operand is never SCALAR: an absolute one such as PUSH [0x41c000] is
    // ADDRESS with its address as the scalar object.
    private static boolean isPushOf(Instruction instruction, long requested) {
        if (instruction == null || !OperandType.isScalar(instruction.getOperandType(0))) return false;
        for (Object object : instruction.getOpObjects(0)) {
            if (object instanceof Scalar scalar
                && scalar.getUnsignedValue() == requested) return true;
        }
        return false;
    }
}
