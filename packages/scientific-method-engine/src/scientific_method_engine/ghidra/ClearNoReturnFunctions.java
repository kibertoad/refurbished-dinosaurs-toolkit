// Clears incorrect no-return markings at explicitly supplied function addresses.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class ClearNoReturnFunctions extends GhidraScript {
    @Override
    protected void run() throws Exception {
        for (String argument : getScriptArgs()) {
            Address address = toAddr(argument);
            Function function = getFunctionContaining(address);
            if (function == null) {
                printerr("No function contains " + address);
                continue;
            }
            function.setNoReturn(false);
            println("Cleared no-return on " + function.getName() + " at " + function.getEntryPoint());
        }
    }
}
