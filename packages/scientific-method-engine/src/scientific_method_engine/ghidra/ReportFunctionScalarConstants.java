// Reports instruction references to selected scalar values within one function.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;

import java.util.HashSet;
import java.util.Set;

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
        Instruction instruction = currentProgram.getListing()
            .getInstructionAt(function.getEntryPoint());
        while (instruction != null && function.getBody().contains(instruction.getAddress())
            && !monitor.isCancelled()) {
            for (int operand = 0; operand < instruction.getNumOperands(); operand++) {
                for (Object object : instruction.getOpObjects(operand)) {
                    if (!(object instanceof Scalar scalar)
                        || !requested.contains(scalar.getUnsignedValue())) continue;
                    println(scalar.getUnsignedValue() + " at " + instruction.getAddress()
                        + ": " + instruction);
                    if (++matches >= MAX_MATCHES) {
                        println("... output capped at " + MAX_MATCHES + " matches");
                        return;
                    }
                }
            }
            instruction = instruction.getNext();
        }
        if (matches == 0) println("No requested scalar constants matched.");
    }
}
