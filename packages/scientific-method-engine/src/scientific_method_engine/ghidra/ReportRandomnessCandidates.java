// Lists references to symbols named like C runtime or Windows random and timing functions (rand,
// srand, time, GetTickCount, QueryPerformanceCounter and similar), with their calling functions.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolIterator;

import java.util.Locale;
import java.util.Set;
import java.util.TreeSet;

public class ReportRandomnessCandidates extends GhidraScript {
    private static final int MAX_MATCHES = 100;
    private static final int MAX_CALLERS_PER_MATCH = 100;
    private static final String[] CANDIDATE_NAMES = {
        "rand", "srand", "random", "randomize", "time", "gettickcount",
        "queryperformancecounter", "timegettime", "getsystemtime"
    };

    @Override
    protected void run() throws Exception {
        println("Randomness/timing candidate references for " + currentProgram.getName());
        int matches = 0;
        SymbolIterator symbols = currentProgram.getSymbolTable().getAllSymbols(true);
        while (symbols.hasNext() && matches < MAX_MATCHES && !monitor.isCancelled()) {
            Symbol symbol = symbols.next();
            if (!isCandidate(symbol.getName())) {
                continue;
            }

            matches++;
            Set<String> callers = new TreeSet<>();
            ReferenceIterator references = currentProgram.getReferenceManager()
                .getReferencesTo(symbol.getAddress());
            while (references.hasNext() && callers.size() < MAX_CALLERS_PER_MATCH
                && !monitor.isCancelled()) {
                Reference reference = references.next();
                Function caller = currentProgram.getFunctionManager()
                    .getFunctionContaining(reference.getFromAddress());
                callers.add(caller == null
                    ? reference.getFromAddress().toString()
                    : caller.getEntryPoint() + " " + caller.getName());
            }

            println(symbol.getAddress() + " " + symbol.getName(true));
            for (String caller : callers) {
                println("  caller " + caller);
            }
            if (references.hasNext()) {
                println("  ... callers capped at " + MAX_CALLERS_PER_MATCH);
            }
        }

        if (matches == 0) println("No randomness or timing candidates matched.");
        else if (matches == MAX_MATCHES && symbols.hasNext()) {
            println("Output capped at " + MAX_MATCHES + " candidate symbols; later symbols were not scanned.");
        }
    }

    private static boolean isCandidate(String name) {
        String lower = name.toLowerCase(Locale.ROOT);
        for (String candidate : CANDIDATE_NAMES) {
            if (lower.equals(candidate) || lower.endsWith("::" + candidate)
                || lower.contains("_" + candidate)) {
                return true;
            }
        }
        return false;
    }
}
