// Reports bounded direct-call paths between two explicitly selected functions.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

public class ReportCallPaths extends GhidraScript {
    private static final int MAX_DEPTH = 12;
    private static final int MAX_VISITED_EDGES = 2000;
    private static final int MAX_PATHS = 100;

    private Address target;
    private int depthLimit;
    private int visitedEdges;
    private int reportedPaths;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 3) {
            printerr("Supply start address, target address, and maximum depth.");
            return;
        }

        Function start = functionAt(arguments[0]);
        Function targetFunction = functionAt(arguments[1]);
        depthLimit = Integer.parseInt(arguments[2]);
        if (start == null || targetFunction == null) return;
        if (depthLimit < 1 || depthLimit > MAX_DEPTH) {
            printerr("Maximum depth must be between 1 and " + MAX_DEPTH + ".");
            return;
        }

        target = targetFunction.getEntryPoint();
        List<Function> path = new ArrayList<>();
        path.add(start);
        Set<Address> active = new HashSet<>();
        active.add(start.getEntryPoint());
        search(start, path, active, 0);
        if (reportedPaths == 0) println("No bounded direct-call path found.");
        if (visitedEdges >= MAX_VISITED_EDGES)
            println("Stopped after the " + MAX_VISITED_EDGES + "-edge safety limit.");
        if (reportedPaths >= MAX_PATHS)
            println("Stopped after the " + MAX_PATHS + "-path safety limit.");
    }

    private Function functionAt(String value) {
        Address address = toAddr(value);
        Function function = currentProgram.getFunctionManager().getFunctionContaining(address);
        if (function == null) printerr(value + ": no containing function");
        return function;
    }

    private void search(Function current, List<Function> path, Set<Address> active, int depth) {
        if (monitor.isCancelled() || depth >= depthLimit
            || visitedEdges >= MAX_VISITED_EDGES || reportedPaths >= MAX_PATHS) return;

        Instruction instruction = currentProgram.getListing()
            .getInstructionAt(current.getEntryPoint());
        while (instruction != null && current.getBody().contains(instruction.getAddress())) {
            if (instruction.getFlowType().isCall()) {
                for (Address destination : instruction.getFlows()) {
                    visitedEdges++;
                    Function callee = currentProgram.getFunctionManager()
                        .getFunctionContaining(destination);
                    if (callee == null) continue;
                    Address entry = callee.getEntryPoint();
                    if (entry.equals(target)) {
                        printPath(path, instruction.getAddress(), callee);
                    }
                    else if (!active.contains(entry)) {
                        path.add(callee);
                        active.add(entry);
                        search(callee, path, active, depth + 1);
                        active.remove(entry);
                        path.remove(path.size() - 1);
                    }
                    if (visitedEdges >= MAX_VISITED_EDGES || reportedPaths >= MAX_PATHS) return;
                }
            }
            instruction = instruction.getNext();
        }
    }

    private void printPath(List<Function> path, Address callSite, Function callee) {
        StringBuilder result = new StringBuilder();
        for (Function function : path) {
            if (result.length() > 0) result.append(" -> ");
            result.append(function.getEntryPoint());
        }
        result.append(" -[").append(callSite).append("]-> ")
            .append(callee.getEntryPoint());
        println(result.toString());
        reportedPaths++;
    }
}
