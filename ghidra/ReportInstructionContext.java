// Prints bounded instruction context around explicitly supplied addresses.
// @category Rechaos

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;

import java.util.ArrayList;
import java.util.List;

public class ReportInstructionContext extends GhidraScript {
    private static final int CONTEXT_INSTRUCTIONS = 8;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length == 0) {
            printerr("Supply one or more instruction addresses.");
            return;
        }

        Listing listing = currentProgram.getListing();
        for (String argument : arguments) {
            Address address = toAddr(argument);
            Instruction focus = listing.getInstructionContaining(address);
            if (focus == null) {
                printerr(argument + ": no containing instruction");
                continue;
            }

            Function function = currentProgram.getFunctionManager()
                .getFunctionContaining(focus.getAddress());
            println("===== " + focus.getAddress()
                + (function == null ? "" : " in " + function.getEntryPoint()
                    + " " + function.getName()) + " =====");

            List<Instruction> before = new ArrayList<>();
            Instruction cursor = focus;
            for (int count = 0; count < CONTEXT_INSTRUCTIONS; count++) {
                cursor = cursor.getPrevious();
                if (cursor == null || (function != null
                    && !function.getBody().contains(cursor.getAddress()))) break;
                before.add(0, cursor);
            }
            for (Instruction instruction : before) printInstruction(instruction, false);
            printInstruction(focus, true);

            cursor = focus;
            for (int count = 0; count < CONTEXT_INSTRUCTIONS; count++) {
                cursor = cursor.getNext();
                if (cursor == null || (function != null
                    && !function.getBody().contains(cursor.getAddress()))) break;
                printInstruction(cursor, false);
            }
        }
    }

    private void printInstruction(Instruction instruction, boolean focus) {
        println((focus ? "> " : "  ") + instruction.getAddress() + "  "
            + instruction.toString());
    }
}
