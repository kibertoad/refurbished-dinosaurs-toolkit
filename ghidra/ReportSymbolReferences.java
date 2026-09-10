// Reports bounded references to explicitly requested symbol-name fragments.
// @category Rechaos

import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolIterator;

import java.util.Locale;

public class ReportSymbolReferences extends GhidraScript {
    private static final int MAX_MATCHES = 100;
    private static final int MAX_REFERENCES_PER_MATCH = 100;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length == 0) {
            printerr("Supply one or more literal symbol-name fragments.");
            return;
        }

        String[] needles = new String[arguments.length];
        for (int index = 0; index < arguments.length; index++) {
            if (arguments[index].isBlank()) {
                printerr("Symbol-name fragments must not be blank.");
                return;
            }
            needles[index] = arguments[index].toUpperCase(Locale.ROOT);
        }

        int matches = 0;
        SymbolIterator symbols = currentProgram.getSymbolTable().getAllSymbols(true);
        while (symbols.hasNext() && matches < MAX_MATCHES && !monitor.isCancelled()) {
            Symbol symbol = symbols.next();
            String name = symbol.getName(true);
            if (!matchesAny(name.toUpperCase(Locale.ROOT), needles)) continue;

            matches++;
            println("===== " + symbol.getAddress() + " " + name + " =====");
            ReferenceIterator references = currentProgram.getReferenceManager()
                .getReferencesTo(symbol.getAddress());
            int emitted = 0;
            while (references.hasNext() && emitted < MAX_REFERENCES_PER_MATCH) {
                Reference reference = references.next();
                Function function = currentProgram.getFunctionManager()
                    .getFunctionContaining(reference.getFromAddress());
                println("  " + reference.getFromAddress() + " " + reference.getReferenceType()
                    + (function == null ? "" : " in " + function.getEntryPoint()
                        + " " + function.getName()));
                emitted++;
            }
            if (references.hasNext()) {
                println("  ... references capped at " + MAX_REFERENCES_PER_MATCH);
            }
        }

        if (matches == 0) println("No requested symbols matched.");
        else if (matches == MAX_MATCHES) {
            println("Output capped at " + MAX_MATCHES + " matching symbols.");
        }
    }

    private static boolean matchesAny(String name, String[] needles) {
        for (String needle : needles) {
            if (name.contains(needle)) return true;
        }
        return false;
    }
}
