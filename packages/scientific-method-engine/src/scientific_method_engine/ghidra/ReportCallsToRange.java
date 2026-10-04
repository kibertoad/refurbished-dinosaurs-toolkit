// Reports call/jump sites whose resolved flow target lies in a segment range.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.symbol.FlowType;

public class ReportCallsToRange extends GhidraScript {
    private static final int MAX_MATCHES = 50;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        String mode = arguments.length == 3 ? arguments[2] : "all";
        if (arguments.length < 2 || arguments.length > 3
            || !(mode.equals("all") || mode.equals("calls") || mode.equals("jumps"))) {
            printerr("Supply start address, end address (inclusive) and an optional kind (all, calls or jumps), "
                + "e.g. 1028:d820 1028:dab7 calls");
            return;
        }

        Address start = toAddr(arguments[0]);
        Address end = toAddr(arguments[1]);
        if (end.compareTo(start) < 0) {
            printerr("End address must be >= start address.");
            return;
        }

        println("===== flow targets in " + start + ".." + end + " (" + mode + ") =====");
        Listing listing = currentProgram.getListing();
        InstructionIterator instructions = listing.getInstructions(true);
        int calls = 0;
        int jumps = 0;
        boolean capped = false;
        while (instructions.hasNext()) {
            monitor.checkCancelled();
            Instruction instruction = instructions.next();
            FlowType flow = instruction.getFlowType();
            boolean call = flow.isCall();
            if (!call && !flow.isJump()) continue;
            if (call ? mode.equals("jumps") : mode.equals("calls")) continue;

            Address[] flows = instruction.getFlows();
            if (flows == null) continue;

            for (Address target : flows) {
                if (target.compareTo(start) < 0 || target.compareTo(end) > 0) continue;
                if (calls + jumps >= MAX_MATCHES) {
                    capped = true;
                    break;
                }

                Function fromFunction = listing.getFunctionContaining(instruction.getAddress());
                Function toFunction = listing.getFunctionContaining(target);
                println(instruction.getAddress()
                    + (fromFunction == null ? "" : " in " + fromFunction.getEntryPoint() + " " + fromFunction.getName())
                    + " -> " + target
                    + (toFunction == null ? "" : " (" + toFunction.getEntryPoint() + " " + toFunction.getName() + ")")
                    + " :: " + instruction + (call ? " [call]" : " [jump]"));
                if (call) calls++;
                else jumps++;
                break;
            }
            if (capped) break;
        }

        if (capped) {
            println("Output capped at " + MAX_MATCHES + " sites (" + calls + " calls, " + jumps
                + " jumps); the scan did not finish. Narrow the range or pass calls or jumps.");
        } else if (calls + jumps == 0) {
            println("No resolved " + (mode.equals("all") ? "call/jump" : mode.substring(0, mode.length() - 1))
                + " targets in range.");
        } else {
            println("Matched " + calls + " calls and " + jumps + " jumps; the scan covered every instruction.");
        }
    }
}
