// Merges a verified orphan function fragment reached by its parent's fall-through.
// @category Restoration

import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

public class MergeFallThroughFragment extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 2) {
            throw new IllegalArgumentException("Pass parent and orphan fragment function entries.");
        }
        Address parentEntry = toAddr(args[0]);
        Address fragmentEntry = toAddr(args[1]);
        Function parent = getFunctionAt(parentEntry);
        Function fragment = getFunctionAt(fragmentEntry);
        if (parent == null || fragment == null || parent.equals(fragment)) {
            throw new IllegalArgumentException("Pass two different existing function entries.");
        }
        Instruction previous = currentProgram.getListing().getInstructionBefore(fragmentEntry);
        Address expectedNext = fragmentEntry;
        int bridgeInstructions = 0;
        while (previous != null && expectedNext.equals(previous.getFallThrough())) {
            Function owner = getFunctionContaining(previous.getAddress());
            if (parent.equals(owner)) break;
            if (owner != null || previous.getFlowType().isCall() ||
                    previous.getFlowType().isJump() || previous.getFlowType().isTerminal() ||
                    ++bridgeInstructions > 64) {
                throw new IllegalArgumentException("Fragment bridge rejected at " + previous.getAddress() + " owner=" + (owner == null ? "none" : owner.getEntryPoint()) + " flow=" + previous.getFlowType());
            }
            expectedNext = previous.getAddress();
            previous = currentProgram.getListing().getInstructionBefore(expectedNext);
        }
        if (previous == null || !expectedNext.equals(previous.getFallThrough()) ||
                !parent.getBody().contains(previous.getAddress())) {
            throw new IllegalArgumentException("Fragment is not reached by its parent's adjacent fall-through.");
        }
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(fragmentEntry);
        while (references.hasNext()) {
            Reference reference = references.next();
            if (!parent.getBody().contains(reference.getFromAddress()) ||
                    reference.getReferenceType().isCall() || reference.getReferenceType().isData()) {
                throw new IllegalArgumentException("Fragment entry has an independent caller/data reference: " + reference);
            }
        }
        long previousSize = parent.getBody().getNumAddresses();
        println("MERGE fragment=" + fragmentEntry + " parent=" + parentEntry +
            " preceding=" + previous.getAddress() + " bridgeInstructions=" + bridgeInstructions + " fragmentBytes=" + fragment.getBody().getNumAddresses());
        currentProgram.getFunctionManager().removeFunction(fragmentEntry);
        CreateFunctionCmd.fixupFunctionBody(currentProgram, parent, monitor);
        if (!parent.getBody().contains(fragmentEntry)) {
            throw new IllegalStateException("Recomputed parent body does not include the fragment entry.");
        }
        println("PARENT " + parentEntry + " bodyBytes=" + previousSize +
            " -> " + parent.getBody().getNumAddresses() + " end=" + parent.getBody().getMaxAddress());
    }
}
