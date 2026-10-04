// Summarizes immediate x86 cdecl first arguments at every direct call to one function.
// The first argument is the nearest PUSH before the call. Instructions between them are passed over
// only when they fall through, are not a flow target and write neither the stack pointer nor
// memory addressed through it or through a register copied from it after the PUSH (MOV [ESP],EAX;
// LEA EAX,[ESP] then MOV [EAX],ECX), as in "PUSH 5; MOV ECX,ESI; CALL". Calls without such a
// literal PUSH are listed with the reason.
// @category Restoration

import java.util.Map;
import java.util.TreeMap;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.OperandType;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import scientificmethod.FirstArgumentLookBack;

public class ReportFirstArgumentCallSummary extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 1) {
            printerr("Supply one callee virtual address.");
            return;
        }

        Address callee = toAddr(arguments[0]);
        FirstArgumentLookBack lookBack = new FirstArgumentLookBack(currentProgram);
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(callee);
        Map<Long, Integer> literals = new TreeMap<>();
        int calls = 0;
        int nonLiteral = 0;
        while (references.hasNext()) {
            monitor.checkCancelled();
            Reference reference = references.next();
            Instruction call = currentProgram.getListing().getInstructionAt(reference.getFromAddress());
            if (call == null || !call.getFlowType().isCall()) continue;
            calls++;

            StringBuilder reason = new StringBuilder();
            Instruction push = lookBack.firstArgumentPush(call, reason);
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

    // Only an immediate operand is a literal; PUSH [EBP+8] carries the scalar 8 as a displacement.
    // Ghidra types an immediate operand SCALAR, adding ADDRESS when it points into the program
    // (PUSH 0x41c000). A memory operand is never SCALAR: an absolute one such as PUSH [0x41c000] is
    // ADDRESS with its address as the scalar object.
    private static Long pushedLiteral(Instruction instruction) {
        if (instruction == null || !OperandType.isScalar(instruction.getOperandType(0))) return null;
        for (Object object : instruction.getOpObjects(0)) {
            if (object instanceof Scalar scalar) return scalar.getUnsignedValue();
        }
        return null;
    }
}
