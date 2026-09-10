// Reports navigation metadata only; run from Ghidra's headless analyzer.
// @category Rechaos

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
    private static final String[] CANDIDATE_NAMES = {
        "rand", "srand", "random", "randomize", "time", "gettickcount",
        "queryperformancecounter", "timegettime", "getsystemtime"
    };

    @Override
    protected void run() throws Exception {
        println("Randomness/timing candidate references for " + currentProgram.getName());
        SymbolIterator symbols = currentProgram.getSymbolTable().getAllSymbols(true);
        while (symbols.hasNext() && !monitor.isCancelled()) {
            Symbol symbol = symbols.next();
            if (!isCandidate(symbol.getName())) {
                continue;
            }

            Set<String> callers = new TreeSet<>();
            ReferenceIterator references = currentProgram.getReferenceManager()
                .getReferencesTo(symbol.getAddress());
            while (references.hasNext()) {
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
