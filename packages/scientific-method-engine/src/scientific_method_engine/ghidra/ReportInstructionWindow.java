// Prints a bounded instruction window that begins at an instruction start.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Instruction;

import scientificmethod.InstructionStart;

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
        if (address == null) {
            printerr(arguments[0] + ": not an address in this program");
            return;
        }
        int count;
        try {
            count = Integer.parseInt(arguments[1]);
        } catch (NumberFormatException exception) {
            count = 0;
        }
        if (count < 1 || count > MAX_INSTRUCTIONS) {
            printerr("Instruction count must be between 1 and " + MAX_INSTRUCTIONS + ".");
            return;
        }

        // The window starts only where an instruction starts. Starting at the next instruction
        // instead would print a window that looks like the requested one but is not.
        String missing = InstructionStart.missingStart(currentProgram, address);
        if (missing != null) {
            printerr(missing + " No window printed.");
            return;
        }

        println("===== up to " + count + " instructions from " + address + " =====");
        Instruction instruction = currentProgram.getListing().getInstructionAt(address);
        int printed = 0;
        while (instruction != null && printed < count) {
            println(instruction.getAddress() + ": " + instruction);
            printed++;
            Instruction next = instruction.getNext();
            if (printed < count && next != null) {
                // next() is null when the instruction ends its address space, so the gap is
                // measured from the instruction's last byte.
                Address last = instruction.getMaxAddress();
                Address end = last.next();
                if (end == null) {
                    println("gap: no instruction after " + last + " up to " + next.getAddress());
                } else if (!next.getAddress().equals(end)) {
                    println("gap: no instruction from " + end + " up to " + next.getAddress());
                }
            }
            instruction = next;
        }
        if (printed < count) {
            println("The listing ends after " + printed + " of " + count + " instructions.");
        } else {
            println("Printed " + printed + " instructions.");
        }
    }
}
