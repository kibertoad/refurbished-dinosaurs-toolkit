// Reports bounded instruction references to explicitly supplied scalar values, as immediates or as
// memory-operand displacements.
// @category Restoration

import ghidra.app.script.GhidraScript;
import ghidra.program.model.lang.OperandType;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.scalar.Scalar;

import java.util.HashSet;
import java.util.Set;

public class ReportScalarConstants extends GhidraScript {
    private static final int MAX_MATCHES = 300;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        // An optional first argument keeps only one operand kind: "immediate" or "memory".
        String kind = arguments.length > 0 && (arguments[0].equals("immediate") || arguments[0].equals("memory"))
            ? arguments[0] : null;
        int first = kind == null ? 0 : 1;
        if (arguments.length <= first) {
            printerr("Supply an optional operand kind (immediate or memory) and one or more scalar values "
                + "(decimal or 0x-prefixed).");
            return;
        }

        Set<Long> requested = new HashSet<>();
        for (int index = first; index < arguments.length; index++) requested.add(Long.decode(arguments[index]));
        int matches = 0;
        InstructionIterator instructions = currentProgram.getListing().getInstructions(true);
        while (instructions.hasNext()) {
            monitor.checkCancelled();
            Instruction instruction = instructions.next();
            for (int operand = 0; operand < instruction.getNumOperands(); operand++) {
                String operandKind = null;
                for (Object object : instruction.getOpObjects(operand)) {
                    if (!(object instanceof Scalar scalar)
                        || !matchesRequested(scalar, requested)) continue;
                    if (operandKind == null) operandKind = operandKind(instruction.getOperandType(operand));
                    if (kind != null && !kind.equals(operandKind)) break;
                    if (matches == MAX_MATCHES) {
                        println("Output capped at " + MAX_MATCHES + " matches; the search did not finish. "
                            + "Narrow it with an operand kind or fewer values.");
                        return;
                    }
                    Function function = currentProgram.getFunctionManager()
                        .getFunctionContaining(instruction.getAddress());
                    println(scalar.getUnsignedValue() + " at " + instruction.getAddress()
                        + (function == null ? "" : " in " + function.getEntryPoint()
                            + " " + function.getName())
                        + " :: " + operandKind + " operand " + operand + " of " + instruction);
                    matches++;
                }
            }
        }
        if (matches == 0) println("No requested scalar constants matched.");
        else println("Matched " + matches + " operands; the search covered every instruction.");
    }

    // A displacement inside a memory operand, such as [ECX + 0x44], is a memory operand. Any other
    // scalar is an immediate, including one Ghidra marks as an address because it points into the
    // program (PUSH 0x41c000 to a string).
    private static String operandKind(int type) {
        return OperandType.isDynamic(type) || OperandType.isIndirect(type) ? "memory" : "immediate";
    }

    // Arguments are decoded as signed longs while operands are reported unsigned, so a request
    // such as -1 must also be compared against the operand's signed value to match at all.
    private static boolean matchesRequested(Scalar scalar, Set<Long> requested) {
        return requested.contains(scalar.getUnsignedValue())
            || requested.contains(scalar.getSignedValue());
    }
}
