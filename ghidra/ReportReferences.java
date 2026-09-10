// Prints references to explicitly supplied virtual addresses.
// @category Rechaos

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

public class ReportReferences extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length == 0) {
            printerr("Supply one or more virtual addresses.");
            return;
        }

        for (String argument : arguments) {
            Address address = toAddr(argument);
            println("===== references to " + address + " =====");
            ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(address);
            while (references.hasNext()) {
                Reference reference = references.next();
                Function function = currentProgram.getFunctionManager()
                    .getFunctionContaining(reference.getFromAddress());
                println("  " + reference.getFromAddress()
                    + " " + reference.getReferenceType()
                    + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName()));
            }
        }
    }
}
