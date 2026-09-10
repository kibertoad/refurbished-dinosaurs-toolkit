// Reports bounded instruction references to explicitly supplied scalar values.
// @category Rechaos

import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.scalar.Scalar;

import java.util.HashSet;
import java.util.Set;

public class ReportScalarConstants extends GhidraScript {
    private static final int MAX_MATCHES = 300;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length == 0) {
            printerr("Supply one or more scalar values (decimal or 0x-prefixed). ");
            return;
        }

        Set<Long> requested = new HashSet<>();
        for (String argument : arguments) requested.add(Long.decode(argument));
        int matches = 0;
        InstructionIterator instructions = currentProgram.getListing().getInstructions(true);
        while (instructions.hasNext() && !monitor.isCancelled()) {
            Instruction instruction = instructions.next();
            for (int operand = 0; operand < instruction.getNumOperands(); operand++) {
                for (Object object : instruction.getOpObjects(operand)) {
                    if (!(object instanceof Scalar scalar)
                        || !requested.contains(scalar.getUnsignedValue())) continue;
                    Function function = currentProgram.getFunctionManager()
                        .getFunctionContaining(instruction.getAddress());
                    println(scalar.getUnsignedValue() + " at " + instruction.getAddress()
                        + (function == null ? "" : " in " + function.getEntryPoint()
                            + " " + function.getName()));
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
