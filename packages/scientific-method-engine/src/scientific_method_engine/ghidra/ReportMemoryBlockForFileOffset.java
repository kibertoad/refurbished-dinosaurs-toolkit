// Maps file offsets to loaded addresses and their memory blocks, and prints nearby instructions.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;

import java.io.File;
import java.nio.file.Files;
import java.util.List;

public class ReportMemoryBlockForFileOffset extends GhidraScript {
    private static final int PATTERN_BYTES = 12;
    private static final int FOLLOW_INSTRUCTIONS = 8;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length == 0) {
            printerr("Supply one or more file offsets in hex, for example 76640.");
            return;
        }

        File executable = new File(currentProgram.getExecutablePath());
        if (!executable.isFile()) {
            printerr("Executable path unavailable: " + currentProgram.getExecutablePath());
            return;
        }

        byte[] fileBytes = Files.readAllBytes(executable.toPath());
        Memory memory = currentProgram.getMemory();
        Listing listing = currentProgram.getListing();

        for (String argument : arguments) {
            long fileOffset = parseHex(argument);
            if (fileOffset < 0 || fileOffset + PATTERN_BYTES > fileBytes.length) {
                printerr(argument + ": file offset out of range.");
                continue;
            }

            byte[] pattern = new byte[PATTERN_BYTES];
            System.arraycopy(fileBytes, (int) fileOffset, pattern, 0, PATTERN_BYTES);
            println("===== file offset " + argument + " =====");
            println("Pattern: " + toHex(pattern));

            // The loader's own file-byte mapping is exact. Without one (some loaders keep no
            // FileBytes), fall back to the first place the bytes occur, and say when that is ambiguous.
            List<Address> mapped = memory.locateAddressesForFileOffset(fileOffset);
            if (mapped.isEmpty()) {
                Address found = memory.findBytes(memory.getMinAddress(), pattern, null, true, monitor);
                if (found == null) {
                    println("Pattern not found in loaded memory.");
                    continue;
                }
                Address after = found.next();
                if (after != null && memory.findBytes(after, pattern, null, true, monitor) != null) {
                    println("Pattern occurs more than once; the first match may not be this offset.");
                }
                mapped = List.of(found);
            }

            for (Address address : mapped) {
                MemoryBlock block = memory.getBlock(address);
                println("Mapped address: " + address
                    + (block == null ? "" : " in block " + block.getName()));
                Instruction instruction = listing.getInstructionContaining(address);
                if (instruction == null) {
                    println("No instruction at mapped address.");
                    continue;
                }

                Function function = listing.getFunctionContaining(instruction.getAddress());
                println("===== " + instruction.getAddress()
                    + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName())
                    + " =====");
                Instruction cursor = instruction;
                for (int count = 0; count < FOLLOW_INSTRUCTIONS; count++) {
                    if (cursor == null) break;
                    println(cursor.getAddress() + "  " + cursor);
                    cursor = cursor.getNext();
                }
            }
        }
    }

    private static long parseHex(String argument) {
        String normalized = argument.trim();
        if (normalized.startsWith("0x") || normalized.startsWith("0X")) {
            return Long.decode(normalized);
        }
        return Long.parseLong(normalized, 16);
    }

    private static String toHex(byte[] bytes) {
        StringBuilder builder = new StringBuilder();
        for (byte value : bytes) {
            builder.append(String.format("%02X ", value));
        }
        return builder.toString().trim();
    }
}
