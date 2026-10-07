package scientificmethod;

import ghidra.program.model.address.Address;
import ghidra.program.model.lang.OperandType;
import ghidra.program.model.scalar.Scalar;

/**
 * The operand constant reading that ReportScalarConstants and ReportFunctionScalarConstants share, so
 * both match the same operands. It lives in a package directory beside the scripts so that Ghidra
 * compiles it with them and the Script Manager does not list it as a script.
 */
public final class OperandConstants {
    private OperandConstants() {
    }

    /**
     * The constant that one of an operand's objects encodes, or null when it encodes none. A
     * displacement, scale or immediate is a Scalar, and so is each part of a far direct target such
     * as CALLF 0x12:0x12345678. An absolute memory operand such as [0x41c000] holds its address as an
     * Address. A near direct branch target is an Address too, but in a CODE operand: Ghidra computes
     * it from a relative displacement, so it never matches.
     */
    public static Scalar value(Object object, int operandType) {
        if (object instanceof Scalar scalar) return scalar;
        if (object instanceof Address address && !OperandType.isCodeReference(operandType))
            return new Scalar(address.getSize(), address.getOffset(), false);
        return null;
    }
}
