// Summarizes immediate x86 cdecl first arguments at every direct call to one function.
// The first argument is the nearest PUSH before the call. Instructions between them are passed over
// only when they fall through, are not a flow target and write neither the stack pointer nor
// memory addressed through it (MOV [ESP],EAX), as in
// "PUSH 5; MOV ECX,ESI; CALL". Calls without such a literal PUSH are listed with the reason.
// @category Restoration

import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

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

public class ReportFirstArgumentCallSummary extends GhidraScript {
    private static final int MAX_PASSED_OVER = 8;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 1) {
            printerr("Supply one callee virtual address.");
            return;
        }

        Address callee = toAddr(arguments[0]);
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(callee);
        Map<Long, Integer> literals = new TreeMap<>();
        int calls = 0;
        int nonLiteral = 0;
        while (references.hasNext()) {
            monitor.checkCancelled();
            Reference reference = references.next();
            Instruction call = currentProgram.getListing().getInstructionAt(reference.getFromAddress());
            if (call == null || !call.getFlowType().isCall()) continue;
            calls++;

            StringBuilder reason = new StringBuilder();
            Instruction push = firstArgumentPush(call, reason);
            Long literal = pushedLiteral(push);
            if (literal != null) {
                literals.merge(literal, 1, Integer::sum);
                continue;
            }

            nonLiteral++;
            Function function = currentProgram.getFunctionManager().getFunctionContaining(call.getAddress());
            println("Non-literal at " + call.getAddress()
                + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName())
                + " first=" + (push == null ? "<none> (" + reason + ")" : push.toString())
                + " preceding=" + (call.getPrevious() == null ? "<none>" : call.getPrevious()));
        }

        println("Direct calls: " + calls);
        for (Map.Entry<Long, Integer> entry : literals.entrySet())
            println("Literal " + entry.getKey() + ": " + entry.getValue());
        println("Non-literal: " + nonLiteral);
    }

    // The nearest PUSH before the call, or null with the reason none is known to supply the first argument.
    private Instruction firstArgumentPush(Instruction call, StringBuilder reason) {
        Register stackPointer = currentProgram.getCompilerSpec().getStackPointer().getBaseRegister();
        Instruction later = call;
        for (int passed = 0; passed <= MAX_PASSED_OVER; passed++) {
            if (isFlowTarget(later)) {
                reason.append("a jump or call reaches ").append(later.getAddress());
                return null;
            }
            Instruction cursor = later.getPrevious();
            if (cursor == null || !later.getAddress().equals(cursor.getFallThrough())) {
                reason.append("no instruction falls through to ").append(later.getAddress());
                return null;
            }
            if ("PUSH".equals(cursor.getMnemonicString())) return cursor;
            if (cursor.getFlowType().isCall() || writes(cursor, stackPointer)) {
                reason.append(cursor.getAddress()).append(" ").append(cursor).append(" changes the stack");
                return null;
            }
            if (storesThroughStack(cursor, stackPointer)) {
                reason.append(cursor.getAddress()).append(" ").append(cursor).append(" writes stack memory");
                return null;
            }
            later = cursor;
        }
        reason.append("no PUSH within ").append(MAX_PASSED_OVER).append(" instructions");
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
    private static Long pushedLiteral(Instruction instruction) {
        if (instruction == null) return null;
        int type = instruction.getOperandType(0);
        if (OperandType.isDynamic(type) || OperandType.isIndirect(type)) return null;
        for (Object object : instruction.getOpObjects(0)) {
            if (object instanceof Scalar scalar) return scalar.getUnsignedValue();
        }
        return null;
    }
}
