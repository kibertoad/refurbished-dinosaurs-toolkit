// Finds a file-offset byte pattern anywhere in Ghidra loaded memory.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.ReferenceManager;

import java.io.File;
import java.nio.file.Files;

public class ReportFilePatternInMemory extends GhidraScript {
    private static final int MAX_MATCHES = 8;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length < 1) {
            printerr("Supply file offset in hex and optional pattern length.");
            return;
        }

        File executable = new File(currentProgram.getExecutablePath());
        byte[] fileBytes = Files.readAllBytes(executable.toPath());
        long fileOffset = Long.decode(arguments[0]);
        int length = arguments.length > 1 ? Integer.decode(arguments[1]) : 8;
        if (fileOffset < 0 || fileOffset + length > fileBytes.length) {
            printerr("File offset out of range.");
            return;
        }

        byte[] pattern = new byte[length];
        System.arraycopy(fileBytes, (int) fileOffset, pattern, 0, length);
        println("===== searching for file offset " + arguments[0] + " pattern =====");
        println(toHex(pattern));

        Memory memory = currentProgram.getMemory();
        Listing listing = currentProgram.getListing();
        ReferenceManager referenceManager = currentProgram.getReferenceManager();
        Address cursor = memory.getMinAddress();
        int matches = 0;
        while (cursor != null && matches < MAX_MATCHES && !monitor.isCancelled()) {
            Address found = memory.findBytes(cursor, pattern, null, true, monitor);
            if (found == null) break;

            Function function = listing.getFunctionContaining(found);
            Instruction instruction = listing.getInstructionContaining(found);
            println("Match " + (matches + 1) + ": " + found
                + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName())
                + (instruction == null ? "" : " :: " + instruction));

            ReferenceIterator references = referenceManager.getReferencesTo(found);
            int refCount = 0;
            while (references.hasNext() && refCount < 10) {
                Reference reference = references.next();
                Function fromFunction = listing.getFunctionContaining(reference.getFromAddress());
                println("  ref from " + reference.getFromAddress()
                    + (fromFunction == null ? "" : " in " + fromFunction.getEntryPoint() + " " + fromFunction.getName())
                    + " " + reference.getReferenceType());
                refCount++;
            }
            if (refCount == 0) {
                println("  no incoming references");
            }

            cursor = found.add(1);
            matches++;
        }

        if (matches == 0) {
            println("Pattern not found in loaded memory.");
        }
    }

    private static String toHex(byte[] bytes) {
        StringBuilder builder = new StringBuilder();
        for (byte value : bytes) {
            builder.append(String.format("%02X ", value));
        }
        return builder.toString().trim();
    }
}
