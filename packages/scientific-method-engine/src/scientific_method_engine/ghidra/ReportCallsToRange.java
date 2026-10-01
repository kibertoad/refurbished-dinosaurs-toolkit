// Reports call/jump sites whose resolved flow target lies in a segment range.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Listing;

public class ReportCallsToRange extends GhidraScript {
    private static final int MAX_MATCHES = 50;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length < 2) {
            printerr("Supply start address and end address (inclusive), e.g. 1028:d820 1028:dab7");
            return;
        }

        Address start = toAddr(arguments[0]);
        Address end = toAddr(arguments[1]);
        if (end.compareTo(start) < 0) {
            printerr("End address must be >= start address.");
            return;
        }

        println("===== flow targets in " + start + ".." + end + " =====");
        Listing listing = currentProgram.getListing();
        InstructionIterator instructions = listing.getInstructions(true);
        int matches = 0;
        while (instructions.hasNext() && matches < MAX_MATCHES && !monitor.isCancelled()) {
            Instruction instruction = instructions.next();
            if (!instruction.getFlowType().isCall() && !instruction.getFlowType().isJump()) {
                continue;
            }

            Address[] flows = instruction.getFlows();
            if (flows == null) {
                continue;
            }

            for (Address target : flows) {
                if (target.compareTo(start) < 0 || target.compareTo(end) > 0) {
                    continue;
                }

                Function fromFunction = listing.getFunctionContaining(instruction.getAddress());
                Function toFunction = listing.getFunctionContaining(target);
                println(instruction.getAddress()
                    + (fromFunction == null ? "" : " in " + fromFunction.getEntryPoint() + " " + fromFunction.getName())
                    + " -> " + target
                    + (toFunction == null ? "" : " (" + toFunction.getEntryPoint() + " " + toFunction.getName() + ")")
                    + " :: " + instruction);
                matches++;
                break;
            }
        }

        if (matches == 0) {
            println("No resolved call/jump targets in range.");
        } else if (matches == MAX_MATCHES) {
            println("Output capped at " + MAX_MATCHES + " matches.");
        }
    }
}
