// Exports instruction metadata for one entry. Output belongs in ignored analysis/original/.
// @category Restoration
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Function;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import scientificmethod.InstructionStart;

public class ExportBoundedFlow extends GhidraScript {
    @Override protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 3) throw new IllegalArgumentException("Supply entry, instruction limit (1..10000), ignored analysis/original/output.json");
        Address entry = toAddr(args[0]);
        if (entry == null) throw new IllegalArgumentException(args[0] + ": not an address in this program");
        String missingEntry = InstructionStart.missingStart(currentProgram, entry);
        if (missingEntry != null) throw new IllegalArgumentException(missingEntry + " Nothing exported.");
        int limit = Integer.parseInt(args[1]);
        if (limit < 1 || limit > 10000) throw new IllegalArgumentException("Limit must be 1..10000");
        Path output = Path.of(args[2]).toAbsolutePath().normalize();
        if (!output.toString().replace('\\', '/').contains("/analysis/original/")) throw new IllegalArgumentException("Flow reports stay under ignored analysis/original/");
        Set<Address> visited = new HashSet<>();
        Deque<Address> pending = new ArrayDeque<>();
        List<String> rows = new ArrayList<>();
        Set<Long> entries = new TreeSet<>();
        // Flow targets where no instruction starts (inside an instruction, data or undisassembled
        // bytes) are listed, so the export never reads as covering them.
        Set<Long> noInstruction = new TreeSet<>();
        boolean limitReached = false;
        entries.add(entry.getOffset()); pending.push(entry);
        while (!pending.isEmpty()) {
            if (monitor.isCancelled()) throw new InterruptedException("Cancelled");
            Address at = pending.pop();
            if (visited.contains(at)) continue;
            if (visited.size() >= limit) { limitReached = true; break; }
            visited.add(at);
            Function owner = currentProgram.getFunctionManager().getFunctionContaining(at);
            Function exact = currentProgram.getFunctionManager().getFunctionAt(at);
            if (!at.equals(entry) && exact != null) { entries.add(at.getOffset()); continue; }
            Instruction ins = currentProgram.getListing().getInstructionAt(at);
            if (ins == null) { noInstruction.add(at.getOffset()); continue; }
            List<Long> next = new ArrayList<>(), calls = new ArrayList<>();
            var type = ins.getFlowType();
            Address fall = ins.getFallThrough();
            if (fall != null) { next.add(fall.getOffset()); pending.push(fall); }
            for (Address target : ins.getFlows()) {
                if (type.isCall()) calls.add(target.getOffset());
                else { next.add(target.getOffset()); pending.push(target); }
            }
            String kind = type.isComputed() ? "indirect" : type.isCall() ? "call" : type.isTerminal() ? "terminal" : type.isJump() ? "branch" : "ordinary";
            // A terminal mnemonic alone is insufficient to identify interrupt/OS semantics.
            String mnemonic = ins.getMnemonicString().toUpperCase(Locale.ROOT);
            if (mnemonic.startsWith("RET")) kind = "return";
            boolean hardware = mnemonic.matches("(?:IN|OUT)(?:S[BDW]?)?(?:\\.REP\\w*)?|INT(?:[13O])?|HLT");
            rows.add("{\"start\":" + at.getOffset() + ",\"size\":" + ins.getLength()
                + ",\"kind\":\"" + kind + "\",\"next\":" + next + ",\"calls\":" + calls
                + ",\"externalEffects\":" + (hardware ? "[\"instruction requires hardware/OS review\"]" : "[]")
                + ",\"owner\":" + (owner == null ? "null" : owner.getEntryPoint().getOffset()) + "}");
        }
        Files.createDirectories(output.getParent());
        String text = "{\"entries\":" + entries + ",\"limitReached\":" + limitReached
            + ",\"noInstruction\":" + noInstruction + ",\"instructions\":[" + String.join(",", rows) + "]}";
        Files.writeString(output, text, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW);
        println("Exported " + rows.size() + " instruction metadata records"
            + (limitReached ? "; the walk stopped at the limit of " + limit + " with flow left unread" : "")
            + (noInstruction.isEmpty() ? "" : "; " + noInstruction.size() + " flow targets start no instruction")
            + ". Ghidra addresses are view-specific; retain the import mapping separately.");
    }
}
