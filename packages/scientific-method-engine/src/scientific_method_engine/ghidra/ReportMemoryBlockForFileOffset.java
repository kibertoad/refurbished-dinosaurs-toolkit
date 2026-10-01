// Locates a file-offset byte pattern in Ghidra memory and prints nearby instructions.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryAccessException;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;

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

            Address found = memory.findBytes(memory.getMinAddress(), pattern, null, true, monitor);
            if (found == null) {
                println("Pattern not found in loaded memory.");
                continue;
            }

            println("Mapped address: " + found);
            Instruction instruction = listing.getInstructionContaining(found);
            if (instruction == null) {
                println("No instruction at mapped address.");
                continue;
            }

            println("===== " + instruction.getAddress()
                + (listing.getFunctionContaining(instruction.getAddress()) == null
                    ? ""
                    : " in " + listing.getFunctionContaining(instruction.getAddress()).getEntryPoint()
                        + " " + listing.getFunctionContaining(instruction.getAddress()).getName())
                + " =====");
            Instruction cursor = instruction;
            for (int count = 0; count < FOLLOW_INSTRUCTIONS; count++) {
                if (cursor == null) break;
                println(cursor.getAddress() + "  " + cursor);
                cursor = cursor.getNext();
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
