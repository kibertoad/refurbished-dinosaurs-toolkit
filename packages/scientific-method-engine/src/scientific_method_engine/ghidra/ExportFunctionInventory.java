// Exports only function starts and body sizes for the committed coverage inventory.
// @category Restoration

import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

public class ExportFunctionInventory extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 1) {
            throw new IllegalArgumentException("Supply one output TSV path.");
        }

        Path output = Path.of(arguments[0]).toAbsolutePath().normalize();
        if (Files.exists(output)) {
            throw new IllegalArgumentException("Inventory output already exists: " + output);
        }
        Files.createDirectories(output.getParent());

        long count = 0;
        Path temporary = Files.createTempFile(output.getParent(), "inventory-", ".partial");
        try {
        try (BufferedWriter writer = Files.newBufferedWriter(temporary, StandardCharsets.UTF_8)) {
            writer.write("start\tsize\n");
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                if (monitor.isCancelled()) {
                    throw new InterruptedException("Function inventory export cancelled.");
                }
                Function function = functions.next();
                writer.write(function.getEntryPoint().toString());
                writer.write('\t');
                writer.write(Long.toString(function.getBody().getNumAddresses()));
                writer.write('\n');
                count++;
            }
        }
        Files.move(temporary, output); // No replacement, and no final file until traversal completes.
        }
        finally { Files.deleteIfExists(temporary); }
        println("Exported " + count + " function starts and body sizes to " + output);
    }
}
