// Disassembles and recovers functions at a version-specific list of cited entries.
// Arguments: a file of 8-digit hex addresses, one per line or in a CSV column, and the optional
// zero-based column. Addresses outside executable memory blocks are counted and skipped.
// @category Restoration

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.mem.MemoryBlock;

public class RecoverCitedFunctions extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length < 1 || arguments.length > 2) {
            throw new IllegalArgumentException(
                "Expected an address/CSV file and optional zero-based CSV column.");
        }
        int column = arguments.length == 2 ? Integer.parseInt(arguments[1]) : 0;
        if (column < 0) {
            throw new IllegalArgumentException("The CSV column is zero-based and cannot be negative.");
        }

        int recovered = 0;
        int alreadyDefined = 0;
        int failed = 0;
        int skipped = 0;
        try (BufferedReader reader = new BufferedReader(new FileReader(new File(arguments[0])))) {
            String line;
            while ((line = reader.readLine()) != null && !monitor.isCancelled()) {
                line = line.strip();
                if (line.isEmpty() || line.startsWith("#")) {
                    continue;
                }
                if (line.contains(",")) {
                    String[] fields = line.split(",", -1);
                    // A CSV written as "a, b" pads its fields; the padding is not part of the address.
                    if (column >= fields.length || !fields[column].strip().matches("(?i)[0-9a-f]{8}")) {
                        continue;
                    }
                    line = fields[column].strip();
                }
                if (!line.matches("(?i)[0-9a-f]{8}")) {
                    continue;
                }
                Address address = toAddr(line);
                MemoryBlock block = currentProgram.getMemory().getBlock(address);
                if (block == null || !block.isExecute()) {
                    skipped++;
                    continue;
                }
                Function function = getFunctionAt(address);
                Instruction instruction = getInstructionAt(address);

                // A previous address-only pass may have left a one-byte, empty function.
                // Remove only that unusable placeholder before asking Ghidra to disassemble.
                if (function != null && instruction == null &&
                    function.getBody().getNumAddresses() == 1) {
                    removeFunction(function);
                    function = null;
                }

                if (instruction == null && !disassemble(address)) {
                    printerr("Could not disassemble " + address);
                    failed++;
                    continue;
                }
                if (function == null) {
                    function = createFunction(address, null);
                }
                if (function == null) {
                    function = getFunctionContaining(address);
                }
                if (function == null) {
                    printerr("Could not create function " + address);
                    failed++;
                } else if (function.getEntryPoint().equals(address)) {
                    recovered++;
                } else {
                    alreadyDefined++;
                }
            }
        }
        println("Recovered entries: " + recovered +
            "; already inside functions: " + alreadyDefined + "; failures: " + failed +
            "; outside executable memory: " + skipped);
    }
}
