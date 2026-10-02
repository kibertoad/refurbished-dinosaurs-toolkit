// Exports the call edges Ghidra recovers below given entries, as JSON for the engine's callees cross-check.
// @category Restoration

import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import ghidra.app.script.GhidraScript;
import ghidra.program.database.mem.AddressSourceInfo;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.symbol.FlowType;

public class ExportCallEdges extends GhidraScript {
    private static final int MAX_FUNCTIONS = 128;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length < 3) {
            throw new IllegalArgumentException(
                "Supply an output JSON path, a function limit and at least one entry address.");
        }
        Path output = Path.of(arguments[0]).toAbsolutePath().normalize();
        if (Files.exists(output)) {
            throw new IllegalArgumentException("Call edge output already exists: " + output);
        }
        int functionLimit = Integer.parseInt(arguments[1]);
        if (functionLimit < 1 || functionLimit > MAX_FUNCTIONS) {
            throw new IllegalArgumentException("Function limit must be between 1 and " + MAX_FUNCTIONS + ".");
        }

        FunctionManager manager = currentProgram.getFunctionManager();
        List<String> missing = new ArrayList<>();
        Set<Function> queued = new LinkedHashSet<>();
        ArrayDeque<Function> queue = new ArrayDeque<>();
        for (int i = 2; i < arguments.length; i++) {
            Address address = toAddr(arguments[i]);
            Function function = address == null ? null : manager.getFunctionAt(address);
            if (function == null) missing.add(arguments[i]);
            else if (queued.add(function)) queue.add(function);
        }

        // Breadth first from the entries, so the function limit keeps the shallowest functions.
        StringBuilder functions = new StringBuilder();
        List<String> unread = new ArrayList<>();
        int exported = 0;
        while (!queue.isEmpty()) {
            if (monitor.isCancelled()) throw new InterruptedException("Call edge export cancelled.");
            Function function = queue.poll();
            if (exported >= functionLimit) {
                unread.add(quote(function.getEntryPoint().toString()));
                continue;
            }
            List<String> edges = new ArrayList<>();
            InstructionIterator instructions = currentProgram.getListing()
                .getInstructions(function.getBody(), true);
            while (instructions.hasNext()) {
                Instruction instruction = instructions.next();
                FlowType flow = instruction.getFlowType();
                Address[] destinations = instruction.getFlows();
                if (flow.isCall() && destinations.length == 0) {
                    edges.add(edge(instruction, null, flow));
                }
                for (Address destination : destinations) {
                    Function callee = manager.getFunctionAt(destination);
                    // A jump counts when it enters another function at its entry: a tail transfer.
                    if (!flow.isCall() && (callee == null || callee.equals(function))) continue;
                    edges.add(edge(instruction, destination, flow));
                    // An external function has no body to read; its edge keeps the external address.
                    if (callee != null && !callee.isExternal() && queued.add(callee)) queue.add(callee);
                }
            }
            if (functions.length() > 0) functions.append(",\n");
            functions.append("    {\"entry\": ").append(offset(function.getEntryPoint()))
                .append(", \"address\": ").append(quote(function.getEntryPoint().toString()))
                .append(", \"edges\": [").append(String.join(", ", edges)).append("]}");
            exported++;
        }

        Files.createDirectories(output.getParent());
        Path temporary = Files.createTempFile(output.getParent(), "call-edges-", ".partial");
        try {
            try (BufferedWriter writer = Files.newBufferedWriter(temporary, StandardCharsets.UTF_8)) {
                writer.write("{\n  \"format\": \"scientific-method-ghidra-call-edges\",\n  \"version\": 1,\n");
                writer.write("  \"sha256\": " + quote(currentProgram.getExecutableSHA256()) + ",\n");
                writer.write("  \"functionLimit\": " + functionLimit + ",\n");
                writer.write("  \"missingEntries\": [" + quoteAll(missing) + "],\n");
                writer.write("  \"unreadFunctions\": [" + String.join(", ", unread) + "],\n");
                writer.write("  \"functions\": [\n" + functions + "\n  ]\n}\n");
            }
            Files.move(temporary, output); // No replacement, and no final file until the walk completes.
        }
        finally { Files.deleteIfExists(temporary); }
        println("Exported the call edges of " + exported + " functions to " + output);
    }

    private String edge(Instruction instruction, Address target, FlowType flow) {
        return "{\"site\": " + offset(instruction.getAddress())
            + ", \"siteAddress\": " + quote(instruction.getAddress().toString())
            + ", \"target\": " + (target == null ? "null" : offset(target))
            + ", \"targetAddress\": " + (target == null ? "null" : quote(target.toString()))
            + ", \"flow\": " + quote(flow.toString()) + "}";
    }

    // The file offset the engine uses for an address, or null when the address has no file bytes.
    private String offset(Address address) {
        AddressSourceInfo info = currentProgram.getMemory().getAddressSourceInfo(address);
        if (info == null || info.getFileOffset() < 0) return "null";
        return Long.toString(info.getFileOffset());
    }

    private static String quoteAll(List<String> values) {
        List<String> quoted = new ArrayList<>();
        for (String value : values) quoted.add(quote(value));
        return String.join(", ", quoted);
    }

    private static String quote(String value) {
        if (value == null) return "null";
        StringBuilder result = new StringBuilder("\"");
        for (char c : value.toCharArray()) {
            if (c == '"' || c == '\\') result.append('\\').append(c);
            else if (c < 0x20) result.append(String.format("\\u%04x", (int) c));
            else result.append(c);
        }
        return result.append('"').toString();
    }
}
