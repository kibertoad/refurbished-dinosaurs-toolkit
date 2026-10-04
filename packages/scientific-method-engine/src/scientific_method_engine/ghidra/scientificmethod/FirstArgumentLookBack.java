package scientificmethod;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Program;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.Varnode;
import ghidra.program.model.symbol.ReferenceIterator;

/**
 * The x86 cdecl first-argument look-back that ReportConstantFirstArgumentCalls and
 * ReportFirstArgumentCallSummary share, so both apply one rule. It lives in a package directory
 * beside the scripts, as Ghidra's own classrecovery helpers do, so Ghidra compiles it with them and
 * the Script Manager does not list it as a script.
 */
public final class FirstArgumentLookBack {
    /** The most instructions passed over between the first argument's PUSH and the call. */
    public static final int MAX_PASSED_OVER = 8;

    private final Program program;
    private final Register stackPointer;

    /** A look-back over one program, using its compiler's stack pointer. */
    public FirstArgumentLookBack(Program program) {
        this.program = program;
        this.stackPointer = program.getCompilerSpec().getStackPointer().getBaseRegister();
    }

    /**
     * The nearest PUSH before the call, or null with the reason appended to {@code reason} when none
     * is known to supply the first argument. Instructions between them are passed over only when they
     * fall through, are not a flow target and write neither the stack pointer nor memory addressed
     * through it or through a register copied from it after the PUSH.
     */
    public Instruction firstArgumentPush(Instruction call, StringBuilder reason) {
        List<Instruction> passedOver = new ArrayList<>();
        Instruction later = call;
        for (int passed = 0; passed <= MAX_PASSED_OVER; passed++) {
            if (isFlowTarget(program, later)) {
                reason.append(later.getAddress()).append(" is a function entry or a jump or call target");
                return null;
            }
            Instruction cursor = later.getPrevious();
            if (cursor == null || !later.getAddress().equals(cursor.getFallThrough())) {
                reason.append("no instruction falls through to ").append(later.getAddress());
                return null;
            }
            if ("PUSH".equals(cursor.getMnemonicString())) {
                Instruction store = storeThroughStack(passedOver);
                if (store == null) return cursor;
                reason.append(store.getAddress()).append(" ").append(store).append(" writes stack memory");
                return null;
            }
            if (cursor.getFlowType().isCall() || writes(cursor, stackPointer)) {
                reason.append(cursor.getAddress()).append(" ").append(cursor).append(" changes the stack");
                return null;
            }
            passedOver.add(cursor);
            later = cursor;
        }
        reason.append("no PUSH among the ").append(MAX_PASSED_OVER + 1).append(" instructions before the call");
        return null;
    }

    /**
     * Whether a jump or call reaches the instruction. A function entry counts too: a callback or table
     * entry reaches it without a reference Ghidra recorded.
     */
    public static boolean isFlowTarget(Program program, Instruction instruction) {
        if (program.getFunctionManager().getFunctionAt(instruction.getAddress()) != null) return true;
        ReferenceIterator references = program.getReferenceManager().getReferencesTo(instruction.getAddress());
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
    private Instruction storeThroughStack(List<Instruction> passedOver) {
        Set<Varnode> fromStack = new HashSet<>();
        for (int index = passedOver.size() - 1; index >= 0; index--) {
            Instruction instruction = passedOver.get(index);
            for (PcodeOp op : instruction.getPcode()) {
                if (op.getOpcode() == PcodeOp.STORE) {
                    if (isFromStack(op.getInput(1), fromStack)) return instruction;
                    continue;
                }
                Varnode output = op.getOutput();
                if (output == null) continue;
                // A LOAD's address may come from the stack pointer, but the value it reads does not.
                boolean derived = false;
                if (op.getOpcode() != PcodeOp.LOAD) {
                    for (Varnode input : op.getInputs()) derived |= isFromStack(input, fromStack);
                }
                if (derived) fromStack.add(output);
                else fromStack.remove(output);
            }
            fromStack.removeIf(Varnode::isUnique); // p-code temporaries do not outlive their instruction
        }
        return null;
    }

    private boolean isFromStack(Varnode varnode, Set<Varnode> fromStack) {
        if (fromStack.contains(varnode)) return true;
        if (!varnode.isRegister()) return false;
        Register register = program.getRegister(varnode.getAddress(), varnode.getSize());
        return register != null && register.getBaseRegister().equals(stackPointer);
    }
}
