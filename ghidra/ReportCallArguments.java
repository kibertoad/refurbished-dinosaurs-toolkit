// Reports the three nearest pushed arguments at every direct call to a function.
// @category Rechaos

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.util.ArrayList;
import java.util.List;

public class ReportCallArguments extends GhidraScript {
    private static final int ARGUMENT_COUNT = 3;
    private static final int MAX_PRECEDING_INSTRUCTIONS = 20;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 1) {
            printerr("Supply one callee virtual address.");
            return;
        }

        Address callee = toAddr(arguments[0]);
        ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(callee);
        while (references.hasNext() && !monitor.isCancelled()) {
            Reference reference = references.next();
            Instruction call = currentProgram.getListing().getInstructionAt(reference.getFromAddress());
            if (call == null || !call.getFlowType().isCall()) continue;

            Function function = currentProgram.getFunctionManager().getFunctionContaining(call.getAddress());
            List<Instruction> pushes = nearestPushes(call, function);
            println(call.getAddress()
                + (function == null ? "" : " in " + function.getEntryPoint() + " " + function.getName())
                + " args=" + format(pushes));
        }
    }

    private List<Instruction> nearestPushes(Instruction call, Function function) {
        List<Instruction> pushes = new ArrayList<>();
        Instruction cursor = call;
        for (int count = 0;
             count < MAX_PRECEDING_INSTRUCTIONS && pushes.size() < ARGUMENT_COUNT;
             count++) {
            cursor = cursor.getPrevious();
            if (cursor == null || (function != null
                && !function.getBody().contains(cursor.getAddress()))) break;
            if (cursor.getFlowType().isCall()) break;
            if ("PUSH".equals(cursor.getMnemonicString())) pushes.add(cursor);
        }
        return pushes;
    }

    private String format(List<Instruction> pushes) {
        StringBuilder result = new StringBuilder("[");
        for (int index = 0; index < pushes.size(); index++) {
            if (index > 0) result.append(", ");
            Instruction push = pushes.get(index);
            result.append(push.getAddress()).append(": ").append(push);
        }
        return result.append(']').toString();
    }
}
