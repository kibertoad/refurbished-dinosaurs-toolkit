// Reports direct x86 cdecl calls whose first argument is one requested constant.
// The first argument is the nearest PUSH before the call. Instructions between them are passed over
// only when they fall through, are not a flow target and write neither the stack pointer nor
// memory addressed through it or through a register copied from it after the PUSH (MOV [ESP],EAX;
// LEA EAX,[ESP] then MOV [EAX],ECX), as in "PUSH 5; MOV ECX,ESI; CALL".
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.OperandType;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.Varnode;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

public class ReportConstantFirstArgumentCalls extends GhidraScript {
    private static final int MAX_CALLS = 300;
    private static final int MAX_PASSED_OVER = 8;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 2) {
            printerr("Supply a callee virtual address and constant first argument.");
            return;
        }

        Address callee = toAddr(arguments[0]);
        long requested = Long.decode(arguments[1]);
        Register stackPointer = currentProgram.getCompilerSpec().getStackPointer().getBaseRegister();
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(callee);
        int matches = 0;
        int calls = 0;
        int unknown = 0;
        boolean capped = false;
        while (references.hasNext()) {
            monitor.checkCancelled();
            Reference reference = references.next();
            Instruction call = currentProgram.getListing().getInstructionAt(reference.getFromAddress());
            if (call == null || !call.getFlowType().isCall()) continue;
            calls++;
            Instruction firstPush = firstArgumentPush(call, stackPointer);
            if (firstPush == null) unknown++;
            if (!isPushOf(firstPush, requested)) continue;
            if (matches >= MAX_CALLS) {
                capped = true;
                break;
            }

            Function function = currentProgram.getFunctionManager().getFunctionContaining(call.getAddress());
            Instruction secondPush = firstPush.getPrevious();
            println(call.getAddress()
                + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName())
                + " first=" + firstPush
                + (secondPush == null ? "" : " preceding=" + secondPush));
            matches++;
        }

        println("Matched calls: " + matches);
        if (capped) println("Output capped at " + MAX_CALLS + " calls; the scan did not finish.");
        else println("Direct calls read: " + calls + ", of which " + unknown + " have no PUSH known to supply the "
            + "first argument (see ReportFirstArgumentCallSummary).");
    }

    // The nearest PUSH before the call, or null when none is known to supply the first argument.
    private Instruction firstArgumentPush(Instruction call, Register stackPointer) {
        List<Instruction> passedOver = new ArrayList<>();
        Instruction later = call;
        for (int passed = 0; passed <= MAX_PASSED_OVER; passed++) {
            if (isFlowTarget(later)) return null;
            Instruction cursor = later.getPrevious();
            if (cursor == null || !later.getAddress().equals(cursor.getFallThrough())) return null;
            if ("PUSH".equals(cursor.getMnemonicString())) {
                return storeThroughStack(passedOver, stackPointer) == null ? cursor : null;
            }
            if (cursor.getFlowType().isCall() || writes(cursor, stackPointer)) return null;
            passedOver.add(cursor);
            later = cursor;
        }
        return null;
    }

    // A function entry counts too: a callback or table entry reaches it without a reference Ghidra recorded.
    private boolean isFlowTarget(Instruction instruction) {
        if (currentProgram.getFunctionManager().getFunctionAt(instruction.getAddress()) != null) return true;
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(instruction.getAddress());
        while (references.hasNext()) {
            if (references.next().getReferenceType().isFlow()) return true;
        }
        return false;
    }

    private static boolean writes(Instruction instruction, Register register) {
        for (Object object : instruction.getResultObjects()) {
            if (object instanceof Register written && written.getBaseRegister().equals(register)) return true;
        }
        return false;
    }

    // The first of the passed-over instructions (listed nearest the call first) that stores to an address
    // computed from the stack pointer, such as MOV [ESP],EAX, or through a register an earlier one copied
    // it into (LEA EAX,[ESP]; MOV [EAX],ECX). Either can overwrite the pushed argument. Ghidra lists no
    // result object for a memory destination, so the p-code is read instead, in execution order.
    private Instruction storeThroughStack(List<Instruction> passedOver, Register stackPointer) {
        Set<Varnode> fromStack = new HashSet<>();
        for (int index = passedOver.size() - 1; index >= 0; index--) {
            Instruction instruction = passedOver.get(index);
            for (PcodeOp op : instruction.getPcode()) {
                if (op.getOpcode() == PcodeOp.STORE) {
                    if (isFromStack(op.getInput(1), fromStack, stackPointer)) return instruction;
                    continue;
                }
                Varnode output = op.getOutput();
                if (output == null) continue;
                boolean derived = false;
                for (Varnode input : op.getInputs()) derived |= isFromStack(input, fromStack, stackPointer);
                if (derived) fromStack.add(output);
                else fromStack.remove(output);
            }
            fromStack.removeIf(Varnode::isUnique); // p-code temporaries do not outlive their instruction
        }
        return null;
    }

    private boolean isFromStack(Varnode varnode, Set<Varnode> fromStack, Register stackPointer) {
        if (fromStack.contains(varnode)) return true;
        if (!varnode.isRegister()) return false;
        Register register = currentProgram.getRegister(varnode.getAddress(), varnode.getSize());
        return register != null && register.getBaseRegister().equals(stackPointer);
    }

    // Only an immediate operand is a literal; PUSH [EBP+8] carries the scalar 8 as a displacement.
    // Ghidra also marks an immediate that points into the program as an address (PUSH 0x41c000),
    // so only a dynamic or indirect operand counts as memory.
    private static boolean isPushOf(Instruction instruction, long requested) {
        if (instruction == null) return false;
        int type = instruction.getOperandType(0);
        if (OperandType.isDynamic(type) || OperandType.isIndirect(type)) return false;
        for (Object object : instruction.getOpObjects(0)) {
            if (object instanceof Scalar scalar
                && scalar.getUnsignedValue() == requested) return true;
        }
        return false;
    }
}
