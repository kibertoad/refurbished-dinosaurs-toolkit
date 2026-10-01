// Prints a bounded instruction window beginning at a virtual address.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Instruction;

public class ReportInstructionWindow extends GhidraScript {
    private static final int MAX_INSTRUCTIONS = 200;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 2) {
            printerr("Supply a virtual address and instruction count.");
            return;
        }

        Address address = toAddr(arguments[0]);
        int count = Integer.parseInt(arguments[1]);
        if (count < 1 || count > MAX_INSTRUCTIONS) {
            printerr("Instruction count must be between 1 and " + MAX_INSTRUCTIONS + ".");
            return;
        }

        Instruction instruction = currentProgram.getListing().getInstructionAt(address);
        if (instruction == null) {
            instruction = currentProgram.getListing().getInstructionAfter(address);
        }

        for (int index = 0; instruction != null && index < count; index++) {
            println(instruction.getAddress() + ": " + instruction);
            instruction = instruction.getNext();
        }
    }
}
