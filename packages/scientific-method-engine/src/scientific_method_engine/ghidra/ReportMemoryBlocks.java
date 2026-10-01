// Reports a bounded selection of memory blocks; never bytes or instructions.
// @category Restoration
import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.MemoryBlock;

public class ReportMemoryBlocks extends GhidraScript {
    private static final int MAX_BLOCKS = 512;
    private static final int MAX_LISTED_MATCHES = 16;
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        MemoryBlock[] blocks = currentProgram.getMemory().getBlocks();
        int start = 0, count = blocks.length;
        String scope = "whole map", requested = "";
        if (args.length == 3 && args[0].equals("page")) {
            try { start = Integer.parseInt(args[1]); count = Integer.parseInt(args[2]); }
            catch (NumberFormatException e) { printerr("Page start/count must be integers."); return; }
            if (start < 0 || start > blocks.length || count < 1 || count > MAX_BLOCKS) {
                printerr("Page start must be within 0.." + blocks.length + "; count within 1.." + MAX_BLOCKS + "."); return;
            }
            requested = " requested=" + count;
            count = Math.min(count, blocks.length - start);
            scope = "page; indices follow the current program's block order";
        } else if (args.length == 2 && args[0].equals("name")) {
            start = -1;
            int matches = 0;
            StringBuilder indices = new StringBuilder();
            for (int i = 0; i < blocks.length; i++) {
                if (!blocks[i].getName().equals(args[1])) continue;
                if (matches++ == 0) start = i;
                if (matches <= MAX_LISTED_MATCHES) indices.append(matches == 1 ? "" : ",").append(i);
            }
            if (matches == 0) { printerr("No block with that exact name."); return; }
            if (matches > 1) {
                printerr("Ambiguous block name: " + matches + " blocks at indices " + indices +
                    (matches > MAX_LISTED_MATCHES ? ",..." : "") + "; select a page instead."); return;
            }
            count = 1; scope = "exact block name";
        } else if (args.length != 0) {
            printerr("Usage: ReportMemoryBlocks.java [page <start-index> <count> | name <exact-name>]"); return;
        }
        if (count > MAX_BLOCKS) {
            printerr("Program has " + blocks.length + " blocks; maximum is " + MAX_BLOCKS + ". Select page or name."); return;
        }
        println("total=" + blocks.length + " start=" + start + " emitted=" + count +
            " partial=" + (count != blocks.length) + requested + " scope=" + scope);
        for (int i = start; i < start + count; i++) {
            MemoryBlock b = blocks[i];
            println(i + " " + b.getName() + " " + b.getStart() + "-" + b.getEnd() + " size=" + b.getSize());
        }
    }
}
