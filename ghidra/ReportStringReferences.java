// Finds bounded references to explicitly requested string fragments.
// @category Rechaos

import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import ghidra.program.model.listing.Function;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.util.Locale;

public class ReportStringReferences extends GhidraScript {
    private static final int MAX_MATCHES = 100;
    private static final int MAX_REFERENCES_PER_MATCH = 100;
    private static final int MAX_POINTERS_PER_MATCH = 100;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length == 0) {
            printerr("Supply one or more literal string fragments.");
            return;
        }

        var needles = new String[arguments.length];
        for (var index = 0; index < arguments.length; index++)
            needles[index] = arguments[index].toUpperCase(Locale.ROOT);

        var matches = 0;
        DataIterator data = currentProgram.getListing().getDefinedData(true);
        while (data.hasNext() && matches < MAX_MATCHES && !monitor.isCancelled()) {
            Data candidate = data.next();
            Object value = candidate.getValue();
            if (!(value instanceof String text)) continue;
            String upper = text.toUpperCase(Locale.ROOT);
            var requested = false;
            for (String needle : needles) {
                if (!upper.contains(needle)) continue;
                requested = true;
                break;
            }
            if (!requested) continue;

            matches++;
            println("===== " + candidate.getAddress() + " \"" + text + "\" =====");
            ReferenceIterator references = currentProgram.getReferenceManager()
                .getReferencesTo(candidate.getAddress());
            var emitted = 0;
            while (references.hasNext() && emitted < MAX_REFERENCES_PER_MATCH) {
                Reference reference = references.next();
                Function function = currentProgram.getFunctionManager()
                    .getFunctionContaining(reference.getFromAddress());
                println("  " + reference.getFromAddress() + " " + reference.getReferenceType()
                    + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName()));
                emitted++;
            }
            if (references.hasNext())
                println("  ... references capped at " + MAX_REFERENCES_PER_MATCH);
            reportRawPointers(candidate);
        }

        if (matches == 0) println("No requested strings matched.");
        else if (matches == MAX_MATCHES) println("Output capped at " + MAX_MATCHES + " matching strings.");
    }

    private void reportRawPointers(Data candidate) throws Exception {
        long offset = candidate.getAddress().getOffset();
        byte[] pointer = new byte[] {
            (byte) offset,
            (byte) (offset >>> 8),
            (byte) (offset >>> 16),
            (byte) (offset >>> 24)
        };
        Memory memory = currentProgram.getMemory();
        var emitted = 0;
        for (MemoryBlock block : memory.getBlocks()) {
            var cursor = block.getStart();
            while (cursor.compareTo(block.getEnd()) <= 0 && emitted < MAX_POINTERS_PER_MATCH) {
                var found = memory.findBytes(cursor, pointer, null, true, monitor);
                if (found == null || !block.contains(found)) break;
                println("  raw pointer at " + found + " in " + block.getName());
                ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(found);
                while (references.hasNext() && emitted < MAX_POINTERS_PER_MATCH) {
                    Reference reference = references.next();
                    Function function = currentProgram.getFunctionManager()
                        .getFunctionContaining(reference.getFromAddress());
                    println("    used at " + reference.getFromAddress()
                        + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName()));
                    emitted++;
                }
                emitted++;
                cursor = found.add(1);
            }
            if (emitted >= MAX_POINTERS_PER_MATCH) break;
        }
        if (emitted >= MAX_POINTERS_PER_MATCH)
            println("  ... raw pointer output capped at " + MAX_POINTERS_PER_MATCH);
    }
}
