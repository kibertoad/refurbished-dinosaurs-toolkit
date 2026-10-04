// Reports bounded call sites whose preceding argument setup contains requested immediates.
// The setup is the run of instructions that falls through to the call, up to 12 instructions. It
// ends after an instruction a jump or call reaches, before one that does not fall through, and before
// an earlier call. Only immediate operands match; a memory operand's displacement, such as the 8 in
// PUSH [EBP+8], is no argument value.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.OperandType;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
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
        int calls = 0;
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(callee);
        while (references.hasNext()) {
            monitor.checkCancelled();
            Reference reference = references.next();
            Instruction call = currentProgram.getListing()
                .getInstructionAt(reference.getFromAddress());
            if (call == null || !call.getFlowType().isCall()) continue;
            calls++;

            List<Instruction> setup = argumentSetup(call);
            boolean matched = false;
            for (Instruction instruction : setup) matched |= containsRequestedImmediate(instruction, requested);
            if (!matched) continue;
            if (matches == MAX_MATCHES) {
                println("Output capped at " + MAX_MATCHES + " calls; the scan did not finish.");
                return;
            }

            matches++;
            Function function = currentProgram.getFunctionManager()
                .getFunctionContaining(call.getAddress());
            println("===== call " + call.getAddress()
                + (function == null ? "" : " in " + function.getEntryPoint()
                    + " " + function.getName()) + " =====");
            for (int index = setup.size() - 1; index >= 0; index--) {
                println("  " + setup.get(index).getAddress() + "  " + setup.get(index));
            }
            println("> " + call.getAddress() + "  " + call);
        }

        if (matches == 0) println("No matching call sites among " + calls + " calls.");
        else println("Matched " + matches + " of " + calls + " calls; the scan covered every call Ghidra "
            + "references to the callee.");
    }

    // The instructions before the call, nearest first, that run on every path reaching the call.
    private List<Instruction> argumentSetup(Instruction call) {
        List<Instruction> setup = new ArrayList<>();
        Instruction later = call;
        while (setup.size() < PRECEDING_INSTRUCTIONS && !isFlowTarget(later)) {
            Instruction cursor = later.getPrevious();
            if (cursor == null || !later.getAddress().equals(cursor.getFallThrough())
                || cursor.getFlowType().isCall()) break;
            setup.add(cursor);
            later = cursor;
        }
        return setup;
    }

    private boolean isFlowTarget(Instruction instruction) {
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(instruction.getAddress());
        while (references.hasNext()) {
            if (references.next().getReferenceType().isFlow()) return true;
        }
        return false;
    }

    // Ghidra also marks an immediate that points into the program as an address (PUSH 0x41c000), so
    // only a dynamic or indirect operand counts as memory.
    private static boolean containsRequestedImmediate(Instruction instruction, Set<Long> requested) {
        for (int operand = 0; operand < instruction.getNumOperands(); operand++) {
            int type = instruction.getOperandType(operand);
            if (OperandType.isDynamic(type) || OperandType.isIndirect(type)) continue;
            for (Object object : instruction.getOpObjects(operand)) {
                if (object instanceof Scalar scalar && matchesRequested(scalar, requested)) {
                    return true;
                }
            }
        }
        return false;
    }

    // Arguments are decoded as signed longs while operands are reported unsigned, so a request
    // such as -1 must also be compared against the operand's signed value to match at all.
    private static boolean matchesRequested(Scalar scalar, Set<Long> requested) {
        return requested.contains(scalar.getUnsignedValue())
            || requested.contains(scalar.getSignedValue());
    }
}
