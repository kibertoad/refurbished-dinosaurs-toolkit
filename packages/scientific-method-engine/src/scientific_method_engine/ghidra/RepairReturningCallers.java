// Repairs explicitly verified returning calls and recomputes their caller body.
// @category Restoration

import java.util.ArrayList;
import java.util.List;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.FlowOverride;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;

public class RepairReturningCallers extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 3) {
            throw new IllegalArgumentException("Pass callee entry, caller entry, then verified call addresses.");
        }
        Address calleeAddress = toAddr(args[0]);
        Address callerAddress = toAddr(args[1]);
        Function callee = getFunctionAt(calleeAddress);
        Function caller = getFunctionAt(callerAddress);
        if (callee == null || caller == null) {
            throw new IllegalArgumentException("Callee and caller must be existing function entries.");
        }
        List<Instruction> calls = new ArrayList<>();
        for (int index = 2; index < args.length; index++) {
            Address callAddress = toAddr(args[index]);
            Instruction call = getInstructionAt(callAddress);
            if (call == null || !call.getMnemonicString().equalsIgnoreCase("CALL") ||
                    callAddress.compareTo(callerAddress) < 0) {
                throw new IllegalArgumentException("Not a verified caller CALL: " + callAddress);
            }
            Address[] targets = call.getDefaultFlows();
            if (targets.length != 1 || !targets[0].equals(calleeAddress)) {
                throw new IllegalArgumentException("CALL does not target supplied callee: " + callAddress);
            }
            Function owner = getFunctionContaining(callAddress);
            if (owner != null && !owner.equals(caller)) {
                throw new IllegalArgumentException("CALL belongs to another function: " + callAddress);
            }
            FlowOverride override = call.getFlowOverride();
            if (override != FlowOverride.NONE && override != FlowOverride.CALL_RETURN) {
                throw new IllegalArgumentException("Unexpected flow override at " + callAddress + ": " + override);
            }
            Address expectedFallThrough = callAddress.add(call.getLength());
            if (call.isFallThroughOverridden() && call.getFallThrough() != null &&
                    !call.getFallThrough().equals(expectedFallThrough)) {
                throw new IllegalArgumentException("Unexpected custom fall-through at " + callAddress);
            }
            calls.add(call);
        }
        long previousSize = caller.getBody().getNumAddresses();
        callee.setNoReturn(false);
        for (Instruction call : calls) {
            println("REPAIR " + call.getAddress() + " override=" + call.getFlowOverride() +
                " fallThrough=" + call.getFallThrough());
            call.setFlowOverride(FlowOverride.NONE);
            call.clearFallThroughOverride();
            Address next = call.getAddress().add(call.getLength());
            if (getInstructionAt(next) == null && !disassemble(next)) {
                throw new IllegalStateException("Could not disassemble caller continuation at " + next);
            }
        }
        CreateFunctionCmd.fixupFunctionBody(currentProgram, caller, monitor);
        println("CALLER " + caller.getEntryPoint() + " bodyBytes=" + previousSize +
            " -> " + caller.getBody().getNumAddresses() + " end=" + caller.getBody().getMaxAddress());
        for (Instruction call : calls) {
            if (call.getFallThrough() == null ||
                    !caller.getBody().contains(call.getAddress().add(call.getLength()))) {
                throw new IllegalStateException("Caller continuation remains missing after repair: " + call.getAddress());
            }
        }
    }
}
