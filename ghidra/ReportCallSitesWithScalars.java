// Reports bounded call sites whose preceding argument setup contains requested scalars.
// @category Rechaos

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.util.HashSet;
import java.util.Set;

public class ReportCallSitesWithScalars extends GhidraScript {
    private static final int PRECEDING_INSTRUCTIONS = 12;
    private static final int MAX_MATCHES = 100;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length < 2) {
            printerr("Supply a callee address followed by one or more scalar values.");
            return;
        }

        Address callee = toAddr(arguments[0]);
        Set<Long> requested = new HashSet<>();
        for (int index = 1; index < arguments.length; index++) {
            requested.add(Long.decode(arguments[index]));
        }

        int matches = 0;
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(callee);
        while (references.hasNext() && matches < MAX_MATCHES && !monitor.isCancelled()) {
            Reference reference = references.next();
            Instruction call = currentProgram.getListing()
                .getInstructionAt(reference.getFromAddress());
            if (call == null || !call.getFlowType().isCall()) continue;

            Function function = currentProgram.getFunctionManager()
                .getFunctionContaining(call.getAddress());
            Instruction cursor = call;
            boolean matched = false;
            for (int count = 0; count < PRECEDING_INSTRUCTIONS; count++) {
                cursor = cursor.getPrevious();
                if (cursor == null || (function != null
                    && !function.getBody().contains(cursor.getAddress()))) break;
                if (cursor.getFlowType().isCall()) break;
                if (containsRequestedScalar(cursor, requested)) matched = true;
            }
            if (!matched) continue;

            matches++;
            println("===== call " + call.getAddress()
                + (function == null ? "" : " in " + function.getEntryPoint()
                    + " " + function.getName()) + " =====");
            cursor = call;
            Instruction[] before = new Instruction[PRECEDING_INSTRUCTIONS];
            int populated = 0;
            for (; populated < PRECEDING_INSTRUCTIONS; populated++) {
                cursor = cursor.getPrevious();
                if (cursor == null || (function != null
                    && !function.getBody().contains(cursor.getAddress()))) break;
                if (cursor.getFlowType().isCall()) break;
                before[populated] = cursor;
            }
            for (int index = populated - 1; index >= 0; index--) {
                println("  " + before[index].getAddress() + "  " + before[index]);
            }
            println("> " + call.getAddress() + "  " + call);
        }

        if (matches == 0) println("No matching call sites found.");
        else if (matches == MAX_MATCHES) println("Output capped at " + MAX_MATCHES + " calls.");
    }

    private static boolean containsRequestedScalar(Instruction instruction, Set<Long> requested) {
        for (int operand = 0; operand < instruction.getNumOperands(); operand++) {
            for (Object object : instruction.getOpObjects(operand)) {
                if (object instanceof Scalar scalar
                    && requested.contains(scalar.getUnsignedValue())) return true;
            }
        }
        return false;
    }
}
