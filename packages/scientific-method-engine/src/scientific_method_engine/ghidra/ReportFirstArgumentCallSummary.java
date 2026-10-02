// Summarizes immediate x86 cdecl first arguments at every direct call to one function.
// The first argument is the nearest PUSH before the call. Instructions between them are passed over
// only when they fall through, leave the stack pointer alone and are not a flow target, as in
// "PUSH 5; MOV ECX,ESI; CALL". Calls without such a literal PUSH are listed with the reason.
// @category Restoration

import java.util.Map;
import java.util.TreeMap;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.OperandType;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

public class ReportFirstArgumentCallSummary extends GhidraScript {
    private static final int MAX_PASSED_OVER = 8;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 1) {
            printerr("Supply one callee virtual address.");
            return;
        }

        Address callee = toAddr(arguments[0]);
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(callee);
        Map<Long, Integer> literals = new TreeMap<>();
        int calls = 0;
        int nonLiteral = 0;
        while (references.hasNext() && !monitor.isCancelled()) {
            Reference reference = references.next();
            Instruction call = currentProgram.getListing().getInstructionAt(reference.getFromAddress());
            if (call == null || !call.getFlowType().isCall()) continue;
            calls++;

            StringBuilder reason = new StringBuilder();
            Instruction push = firstArgumentPush(call, reason);
            Long literal = pushedLiteral(push);
            if (literal != null) {
                literals.merge(literal, 1, Integer::sum);
                continue;
            }

            nonLiteral++;
            Function function = currentProgram.getFunctionManager().getFunctionContaining(call.getAddress());
            println("Non-literal at " + call.getAddress()
                + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName())
                + " first=" + (push == null ? "<none> (" + reason + ")" : push.toString())
                + " preceding=" + (call.getPrevious() == null ? "<none>" : call.getPrevious()));
        }

        println("Direct calls: " + calls);
        for (Map.Entry<Long, Integer> entry : literals.entrySet())
            println("Literal " + entry.getKey() + ": " + entry.getValue());
        println("Non-literal: " + nonLiteral);
    }

    // The nearest PUSH before the call, or null with the reason none is known to supply the first argument.
    private Instruction firstArgumentPush(Instruction call, StringBuilder reason) {
        Register stackPointer = currentProgram.getCompilerSpec().getStackPointer().getBaseRegister();
        Instruction later = call;
        for (int passed = 0; passed <= MAX_PASSED_OVER; passed++) {
            if (isFlowTarget(later)) {
                reason.append("a jump or call reaches ").append(later.getAddress());
                return null;
            }
            Instruction cursor = later.getPrevious();
            if (cursor == null || !later.getAddress().equals(cursor.getFallThrough())) {
                reason.append("no instruction falls through to ").append(later.getAddress());
                return null;
            }
            if ("PUSH".equals(cursor.getMnemonicString())) return cursor;
            if (cursor.getFlowType().isCall() || writes(cursor, stackPointer)) {
                reason.append(cursor.getAddress()).append(" ").append(cursor).append(" changes the stack");
                return null;
            }
            later = cursor;
        }
        reason.append("no PUSH within ").append(MAX_PASSED_OVER).append(" instructions");
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
    private static Long pushedLiteral(Instruction instruction) {
        if (instruction == null) return null;
        int type = instruction.getOperandType(0);
        if (OperandType.isDynamic(type) || OperandType.isIndirect(type) || OperandType.isAddress(type)) return null;
        for (Object object : instruction.getOpObjects(0)) {
            if (object instanceof Scalar scalar) return scalar.getUnsignedValue();
        }
        return null;
    }
}
