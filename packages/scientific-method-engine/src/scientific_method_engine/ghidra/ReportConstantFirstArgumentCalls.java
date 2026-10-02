// Reports direct x86 cdecl calls whose first argument is one requested constant.
// The first argument is the nearest PUSH before the call. Instructions between them are passed over
// only when they fall through, leave the stack pointer alone and are not a flow target, as in
// "PUSH 5; MOV ECX,ESI; CALL".
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.OperandType;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

public class ReportConstantFirstArgumentCalls extends GhidraScript {
    private static final int MAX_CALLS = 300;
    private static final int MAX_PASSED_OVER = 8;

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
        int calls = 0;
        int unknown = 0;
        boolean capped = false;
        while (references.hasNext() && !monitor.isCancelled()) {
            Reference reference = references.next();
            Instruction call = currentProgram.getListing().getInstructionAt(reference.getFromAddress());
            if (call == null || !call.getFlowType().isCall()) continue;
            calls++;
            Instruction firstPush = firstArgumentPush(call);
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
        else println("Direct calls read: " + calls + ", of which " + unknown + " have no PUSH known to supply the "
            + "first argument (see ReportFirstArgumentCallSummary).");
    }

    // The nearest PUSH before the call, or null when none is known to supply the first argument.
    private Instruction firstArgumentPush(Instruction call) {
        Register stackPointer = currentProgram.getCompilerSpec().getStackPointer().getBaseRegister();
        Instruction later = call;
        for (int passed = 0; passed <= MAX_PASSED_OVER; passed++) {
            if (isFlowTarget(later)) return null;
            Instruction cursor = later.getPrevious();
            if (cursor == null || !later.getAddress().equals(cursor.getFallThrough())) return null;
            if ("PUSH".equals(cursor.getMnemonicString())) return cursor;
            if (cursor.getFlowType().isCall() || writes(cursor, stackPointer)) return null;
            later = cursor;
        }
        return null;
    }

    private boolean isFlowTarget(Instruction instruction) {
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(instruction.getAddress());
        while (references.hasNext()) {
            if (references.next().getReferenceType().isFlow()) return true;
        }
        return false;
    }

    private static boolean writes(Instruction instruction, Register register) {
        for (Object object : instruction.getResultObjects()) {
            if (object instanceof Register written && written.getBaseRegister().equals(register)) return true;
        }
        return false;
    }

    // Only an immediate operand is a literal; PUSH [EBP+8] carries the scalar 8 as a displacement.
    private static boolean isPushOf(Instruction instruction, long requested) {
        if (instruction == null) return false;
        int type = instruction.getOperandType(0);
        if (OperandType.isDynamic(type) || OperandType.isIndirect(type) || OperandType.isAddress(type)) return false;
        for (Object object : instruction.getOpObjects(0)) {
            if (object instanceof Scalar scalar
                && scalar.getUnsignedValue() == requested) return true;
        }
        return false;
    }
}
