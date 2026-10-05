package scientificmethod;

import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.listing.Program;
import ghidra.program.model.mem.MemoryBlock;

/**
 * The exact-start lookup that ReportInstructionWindow and ExportBoundedFlow share. A script that
 * reads from an instruction start refuses an address where none starts and says what is there
 * instead, so a mistyped or misaligned start never reads as an empty result.
 */
public final class InstructionStart {
    private InstructionStart() {
    }

    /**
     * Null when an instruction starts at the address. Otherwise a sentence that names what holds
     * the address (no memory block, the instruction or defined data that contains it, or
     * undisassembled bytes) and the next instruction start after it, if there is one.
     */
    public static String missingStart(Program program, Address address) {
        Listing listing = program.getListing();
        if (listing.getInstructionAt(address) != null) {
            return null;
        }

        String holder;
        MemoryBlock block = program.getMemory().getBlock(address);
        Instruction containing = listing.getInstructionContaining(address);
        Data data = listing.getDefinedDataContaining(address);
        if (block == null) {
            holder = "it is in no memory block";
        } else if (containing != null) {
            holder = "it is inside the " + containing.getLength() + "-byte instruction at "
                + containing.getAddress();
        } else if (data != null) {
            holder = "it is inside defined data at " + data.getAddress();
        } else {
            holder = "it is undisassembled in memory block " + block.getName();
        }

        Instruction next = listing.getInstructionAfter(address);
        return "No instruction starts at " + address + ": " + holder + ". "
            + (next == null ? "No instruction follows it."
                : "The next instruction starts at " + next.getAddress() + ".");
    }
}
