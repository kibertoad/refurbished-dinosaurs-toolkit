// Reports bounded references to explicitly supplied virtual addresses.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

public class ReportReferences extends GhidraScript {
    private static final int MAX_REFERENCES = 200;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length == 0) {
            printerr("Supply one or more virtual addresses.");
            return;
        }

        for (String argument : arguments) {
            Address target = toAddr(argument);
            println("===== references to " + target + " =====");
            ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(target);
            int count = 0;
            while (references.hasNext() && count < MAX_REFERENCES && !monitor.isCancelled()) {
                Reference reference = references.next();
                Instruction instruction = currentProgram.getListing().getInstructionContaining(reference.getFromAddress());
                Function function = currentProgram.getFunctionManager().getFunctionContaining(reference.getFromAddress());
                println(reference.getFromAddress()
                    + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName())
                    + " " + reference.getReferenceType()
                    + (instruction == null ? "" : ": " + instruction));
                count++;
            }

            if (count == 0) println("No references found.");
            else if (count == MAX_REFERENCES) println("Output capped at " + MAX_REFERENCES + " references.");
        }
    }
}
