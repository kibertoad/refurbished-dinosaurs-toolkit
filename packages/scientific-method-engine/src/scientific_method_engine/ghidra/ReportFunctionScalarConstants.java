// Reports instruction references to selected scalar values within one function: immediates and
// memory-operand displacements, absolute addresses such as [0x41c000] and index scales.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.scalar.Scalar;

import java.util.HashSet;
import java.util.Set;

import scientificmethod.OperandConstants;

public class ReportFunctionScalarConstants extends GhidraScript {
    private static final int MAX_MATCHES = 200;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length < 2) {
            printerr("Supply a function address followed by one or more scalar values.");
            return;
        }

        Address address = toAddr(arguments[0]);
        Function function = currentProgram.getFunctionManager().getFunctionContaining(address);
        if (function == null) {
            printerr(arguments[0] + ": no containing function");
            return;
        }

        Set<Long> requested = new HashSet<>();
        for (int index = 1; index < arguments.length; index++)
            requested.add(Long.decode(arguments[index]));

        int matches = 0;
        // The whole body, so a chunk before the entry or past another function's code is read too.
        InstructionIterator instructions = currentProgram.getListing().getInstructions(function.getBody(), true);
        while (instructions.hasNext()) {
            monitor.checkCancelled();
            Instruction instruction = instructions.next();
            for (int operand = 0; operand < instruction.getNumOperands(); operand++) {
                int operandType = instruction.getOperandType(operand);
                for (Object object : instruction.getOpObjects(operand)) {
                    Scalar scalar = OperandConstants.value(object, operandType);
                    if (scalar == null || !requested.contains(scalar.getUnsignedValue())) continue;
                    println(scalar.getUnsignedValue() + " at " + instruction.getAddress()
                        + ": " + instruction);
                    if (++matches >= MAX_MATCHES) {
                        println("... output capped at " + MAX_MATCHES + " matches");
                        return;
                    }
                }
            }
        }
        if (matches == 0) println("No requested scalar constants matched.");
    }
}
