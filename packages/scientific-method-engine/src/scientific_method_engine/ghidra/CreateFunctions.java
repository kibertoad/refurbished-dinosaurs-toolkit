// Creates functions at explicitly supplied indirect-call targets.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class CreateFunctions extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length == 0) {
            printerr("Pass one or more function entry addresses.");
            return;
        }
        for (String argument : arguments) {
            Address address = toAddr(argument);
            Function function = getFunctionAt(address);
            if (function == null) function = createFunction(address, null);
            if (function == null) {
                printerr("Could not create function at " + address);
                continue;
            }
            println("FUNCTION " + function.getName() + " " + function.getEntryPoint());
        }
    }
}
