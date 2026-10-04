// Reports direct x86 cdecl calls whose first argument is one requested constant.
// The first argument is the nearest PUSH before the call. Instructions between them are passed over
// only when they fall through, are not a flow target and write neither the stack pointer nor
// memory addressed through it (MOV [ESP],EAX), as in
// "PUSH 5; MOV ECX,ESI; CALL".
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

import java.util.HashSet;
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
            Instruction firstPush = firstArgumentPush(call);
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
    private Instruction firstArgumentPush(Instruction call) {
        Register stackPointer = currentProgram.getCompilerSpec().getStackPointer().getBaseRegister();
        Instruction later = call;
        for (int passed = 0; passed <= MAX_PASSED_OVER; passed++) {
            if (isFlowTarget(later)) return null;
            Instruction cursor = later.getPrevious();
            if (cursor == null || !later.getAddress().equals(cursor.getFallThrough())) return null;
            if ("PUSH".equals(cursor.getMnemonicString())) return cursor;
            if (cursor.getFlowType().isCall() || writes(cursor, stackPointer)
                || storesThroughStack(cursor, stackPointer)) return null;
            later = cursor;
        }
        return null;
    }

    private boolean isFlowTarget(Instruction instruction) {
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

    // True when the instruction stores to an address computed from the stack pointer, such as
    // MOV [ESP],EAX, which can overwrite the pushed argument. Ghidra lists no result object for a
    // memory destination, so the instruction's p-code is read instead.
    private boolean storesThroughStack(Instruction instruction, Register stackPointer) {
        Set<Varnode> fromStack = new HashSet<>();
        for (PcodeOp op : instruction.getPcode()) {
            if (op.getOpcode() == PcodeOp.STORE) {
                if (isFromStack(op.getInput(1), fromStack, stackPointer)) return true;
                continue;
            }
            Varnode output = op.getOutput();
            if (output == null) continue;
            boolean derived = false;
            for (Varnode input : op.getInputs()) derived |= isFromStack(input, fromStack, stackPointer);
            if (derived) fromStack.add(output);
            else fromStack.remove(output);
        }
        return false;
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
