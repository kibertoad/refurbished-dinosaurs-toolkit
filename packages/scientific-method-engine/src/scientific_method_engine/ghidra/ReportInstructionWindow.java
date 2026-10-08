// Prints a bounded instruction window that begins at an instruction start.
// @category Restoration

import java.util.ArrayList;
import java.util.List;

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
        // Each run of printed instructions with no gap between them, closed at a gap and at the
        // window's last instruction.
        List<String> spans = new ArrayList<>();
        Address spanStart = null;
        int spanInstructions = 0;
        // The instructions in the current span whose listed length the listing overrides.
        List<String> overrides = new ArrayList<>();
        while (instruction != null && printed < count) {
            if (spanStart == null) {
                spanStart = instruction.getAddress();
            }
            println(instruction.getAddress() + ": " + instruction);
            printed++;
            spanInstructions++;
            if (instruction.isLengthOverridden()) {
                overrides.add(instruction.getAddress() + " (listed as " + instruction.getLength() + " of the "
                    + instruction.getParsedLength() + " bytes it decodes)");
            }
            Instruction next = instruction.getNext();
            // Address.next() is null when the instruction ends its address space, so a gap is
            // measured from the instruction's last byte, and such a span has no exclusive end.
            Address last = instruction.getMaxAddress();
            Address end = last.next();
            boolean adjacent = end != null && next != null && next.getAddress().equals(end);
            if (printed == count || !adjacent) {
                spans.add(span(spanStart, last, end, spanInstructions, overrides, adjacent));
                spanStart = null;
                spanInstructions = 0;
                overrides.clear();
                if (printed < count && next != null) {
                    if (end == null) {
                        println("gap: no instruction after " + last + " up to " + next.getAddress());
                    } else {
                        println("gap: no instruction from " + end + " up to " + next.getAddress());
                    }
                }
            }
            instruction = next;
        }
        for (String line : spans) {
            println(line);
        }
        if (printed < count) {
            println("The listing ends after " + printed + " of " + count + " instructions.");
        } else {
            println("Printed " + printed + " instructions.");
        }
    }

    // A span as a half-open range with its last byte, so a range copied from it ends after the
    // final byte of its last instruction. Past the end of an address space no address can end it.
    // A span the window's count closed says when the next instruction starts at its end, since
    // the run goes on past the window. An instruction whose length the listing overrides decodes
    // bytes past where the next instruction or the span ends, so the span names it.
    private static String span(Address first, Address last, Address end, int instructions,
            List<String> overrides, boolean continues) {
        long bytes = last.subtract(first) + 1;
        String range = end == null ? first + " to the end of its address space" : first + ".." + end;
        StringBuilder line = new StringBuilder("span: " + range + " (" + bytes + (bytes == 1 ? " byte" : " bytes") + ", last byte " + last
            + ", " + instructions + (instructions == 1 ? " instruction)" : " instructions)"));
        if (end == null) {
            line.append("; no address follows its last byte, so it has no exclusive end");
        }
        if (continues) {
            line.append("; the window stops here and the next instruction starts at ").append(end);
        }
        for (String override : overrides) {
            line.append("; the listing overrides the length of the instruction at ").append(override);
        }
        return line.toString();
    }
}
