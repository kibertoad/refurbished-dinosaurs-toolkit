// Summarizes immediate x86 cdecl first arguments at every direct call to one function.
// Calls without an immediately preceding literal PUSH are listed for follow-up.
// @category Restoration

import java.util.Map;
import java.util.TreeMap;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

public class ReportFirstArgumentCallSummary extends GhidraScript {
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

            Instruction push = call.getPrevious();
            Long literal = pushedLiteral(push);
            if (literal != null) {
                literals.merge(literal, 1, Integer::sum);
                continue;
            }

            nonLiteral++;
            Function function = currentProgram.getFunctionManager().getFunctionContaining(call.getAddress());
            println("Non-literal at " + call.getAddress()
                + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName())
                + " first=" + (push == null ? "<none>" : push));
        }

        println("Direct calls: " + calls);
        for (Map.Entry<Long, Integer> entry : literals.entrySet())
            println("Literal " + entry.getKey() + ": " + entry.getValue());
        println("Non-literal: " + nonLiteral);
    }

    private static Long pushedLiteral(Instruction instruction) {
        if (instruction == null || !"PUSH".equals(instruction.getMnemonicString())) return null;
        for (Object object : instruction.getOpObjects(0)) {
            if (object instanceof Scalar scalar) return scalar.getUnsignedValue();
        }
        return null;
    }
}
