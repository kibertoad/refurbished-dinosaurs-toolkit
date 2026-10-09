// Reports half-open ranges from a file whose start or end falls inside an instruction or defined data.
// Arguments: a file with one range per line, `start..end` with the end exclusive, optionally followed
// by whitespace and a label that is printed with the range. Blank lines and lines starting with #
// are skipped.
// @category Restoration

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import ghidra.app.script.GhidraScript;
import ghidra.app.util.PseudoDisassembler;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.CodeUnit;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.MemoryBlock;

public class ReportRangeBoundaries extends GhidraScript {
    private static final int MAX_PRINTED = 1000;
    // The most instructions decoded in memory from one range's start while placing its end.
    private static final int MAX_DECODED = 10000;
    private static final Pattern RANGE = Pattern.compile("(\\S+?)\\.\\.(\\S+)(?:\\s+(.*))?");

    // Where a range's start falls: where an instruction or defined data starts, inside one, or in
    // undisassembled bytes or outside memory, where it is not judged.
    private enum Start { ON_BOUNDARY, CUT, UNJUDGED }

    private Listing listing;
    private PseudoDisassembler decoder;
    private int printed;

    private int ends;
    private int endsDecoded;
    private int endsDecodedFromUnjudged;
    private int endsInInstruction;
    private int endsInData;
    private int endsUnplaced;
    private int startsInInstruction;
    private int startsInData;
    private int startsUnjudged;
    private int unread;

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 1) {
            printerr("Supply the path of a file with one start..end range per line.");
            return;
        }
        List<String> lines;
        try {
            lines = Files.readAllLines(Path.of(arguments[0]));
        } catch (IOException exception) {
            printerr(arguments[0] + ": could not read the file: " + exception.getMessage());
            return;
        }
        listing = currentProgram.getListing();
        decoder = new PseudoDisassembler(currentProgram);

        int ranges = 0;
        int number = 1;
        for (; number <= lines.size(); number++) {
            if (monitor.isCancelled()) {
                break;
            }
            String line = lines.get(number - 1);
            // A file saved with a UTF-8 byte order mark keeps it on its first line.
            if (number == 1 && line.startsWith("\uFEFF")) {
                line = line.substring(1);
            }
            line = line.strip();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            Matcher matcher = RANGE.matcher(line);
            if (!matcher.matches()) {
                notRead(number, "expected start..end, optionally followed by a label");
                continue;
            }
            Address start = address(matcher.group(1));
            Address end = address(matcher.group(2));
            if (start == null || end == null) {
                notRead(number, (start == null ? matcher.group(1) : matcher.group(2))
                    + " is not an address in this program");
                continue;
            }
            if (!start.getAddressSpace().equals(end.getAddressSpace()) || start.compareTo(end) >= 0) {
                notRead(number, "the end must follow the start in the same address space");
                continue;
            }
            ranges++;
            String label = matcher.group(3) == null ? "" : " " + matcher.group(3).strip();
            String range = start + ".." + end + label;
            Start judged = checkStart(range, start);
            checkEnd(range, start, end, judged);
        }

        if (number <= lines.size()) {
            println("Cancelled before line " + number + " of " + lines.size()
                + "; the counts below cover only the lines before it.");
        }
        if (printed > MAX_PRINTED) {
            println("Printed the first " + MAX_PRINTED + " lines; the counts below cover every range read.");
        }
        println("Checked " + ranges + (ranges == 1 ? " range: " : " ranges: ") + ends
            + " ends on an instruction or data boundary (" + endsDecoded + " of them placed by decoding in memory, "
            + endsDecodedFromUnjudged + " of those from a start that was not judged), "
            + endsInInstruction + " inside an instruction, " + endsInData + " inside defined data, "
            + endsUnplaced + " unplaced; " + startsInInstruction + " starts inside an instruction, "
            + startsInData + " inside defined data, " + startsUnjudged
            + " in undisassembled bytes or outside memory and not judged; "
            + unread + (unread == 1 ? " line" : " lines") + " not read.");
    }

    // A start where no instruction or defined data starts, but inside one, cuts it. A start in
    // undisassembled bytes is not judged: nothing in the listing says where an instruction there starts.
    private Start checkStart(String range, Address start) {
        CodeUnit cut = containingUnit(start);
        if (cut == null) {
            if (listing.getInstructionAt(start) != null || isDataStart(start)) {
                return Start.ON_BOUNDARY;
            }
            startsUnjudged++;
            return Start.UNJUDGED;
        }
        if (cut instanceof Instruction) {
            startsInInstruction++;
            report("start inside instruction: " + range + ": the start is " + into(start, cut));
        } else {
            startsInData++;
            report("start inside data: " + range + ": the start is " + into(start, cut));
        }
        return Start.CUT;
    }

    private void checkEnd(String range, Address start, Address end, Start judged) {
        // An end is a boundary where an instruction or defined data starts, or where the byte
        // before it is the last byte of one. Padding after a RET is undisassembled, so an end there
        // is placed by the RET.
        if (listing.getInstructionAt(end) != null || isDataStart(end) || endsUnitBefore(end)) {
            ends++;
            return;
        }
        CodeUnit cut = containingUnit(end);
        if (cut instanceof Instruction) {
            endsInInstruction++;
            report("end inside instruction: " + range + ": the end is " + into(end, cut));
            return;
        }
        if (cut != null) {
            endsInData++;
            report("end inside data: " + range + ": the end is " + into(end, cut));
            return;
        }
        if (judged == Start.CUT) {
            unplaced(range, "the end is in undisassembled bytes, and the start is inside a code unit, so "
                + "decoding from it would not follow the program's instructions");
            return;
        }
        decodeTo(range, start, end, judged == Start.UNJUDGED);
    }

    // The end is in undisassembled bytes. Decode from the range's start in fall-through order,
    // taking each instruction from the listing where it has one and decoding the bytes in memory
    // otherwise. PseudoDisassembler writes nothing to the program, so the database is unchanged.
    // From a start that was not judged, the result holds only if an instruction starts there, so
    // the report and the counts say which ends were placed that way.
    private void decodeTo(String range, Address start, Address end, boolean fromUnjudged) {
        String unjudgedNote = fromUnjudged ? ", a start that was not judged" : "";
        Address at = start;
        for (int decoded = 0; ; decoded++) {
            if (decoded == MAX_DECODED) {
                unplaced(range, "decoding from " + start + " stopped at the limit of " + MAX_DECODED
                    + " instructions, at " + at);
                return;
            }
            Instruction instruction = listing.getInstructionAt(at);
            boolean listed = instruction != null;
            if (!listed) {
                CodeUnit unit = listing.getInstructionContaining(at);
                if (unit == null) {
                    unit = listing.getDefinedDataContaining(at);
                }
                if (unit != null) {
                    unplaced(range, "decoding from " + start + " reaches " + at + ", inside the "
                        + describe(unit) + " at " + unit.getAddress());
                    return;
                }
                MemoryBlock block = currentProgram.getMemory().getBlock(at);
                if (block == null || !block.isInitialized()) {
                    unplaced(range, "decoding from " + start + " reaches " + at + ", which holds no bytes");
                    return;
                }
                try {
                    instruction = decoder.disassemble(at);
                } catch (Exception exception) {
                    instruction = null;
                }
                if (instruction == null) {
                    unplaced(range, "decoding from " + start + " reaches " + at + ", where the bytes do not decode");
                    return;
                }
                CodeUnit overlapped = listedUnitWithin(at.next(), instruction.getMaxAddress());
                if (overlapped != null) {
                    unplaced(range, "decoding from " + start + " decodes the instruction at " + at + ", " + instruction
                        + ", whose bytes run into the " + describe(overlapped) + " at " + overlapped.getAddress());
                    return;
                }
            }
            Address last = instruction.getMaxAddress();
            if (end.compareTo(instruction.getAddress()) > 0 && end.compareTo(last) <= 0) {
                endsInInstruction++;
                report("end inside instruction: " + range + ": the end is " + into(end, instruction)
                    + (listed ? "" : ", decoded in memory from " + start + unjudgedNote));
                return;
            }
            Address next = last.next();
            if (end.equals(next)) {
                ends++;
                endsDecoded++;
                if (fromUnjudged) {
                    endsDecodedFromUnjudged++;
                }
                return;
            }
            Address fallThrough = instruction.getFallThrough();
            if (fallThrough == null) {
                unplaced(range, "decoding from " + start + " stops at the instruction at " + instruction.getAddress()
                    + ", " + instruction + ", which does not fall through");
                return;
            }
            if (!fallThrough.getAddressSpace().equals(end.getAddressSpace()) || fallThrough.compareTo(end) >= 0
                    || fallThrough.compareTo(at) <= 0) {
                unplaced(range, "decoding from " + start + " continues from the instruction at "
                    + instruction.getAddress() + " to " + fallThrough + ", past the end");
                return;
            }
            at = fallThrough;
        }
    }

    // The instruction or defined data that holds the address without starting at it.
    private CodeUnit containingUnit(Address address) {
        Instruction instruction = listing.getInstructionContaining(address);
        if (instruction != null) {
            return instruction.getAddress().equals(address) ? null : instruction;
        }
        Data data = listing.getDefinedDataContaining(address);
        return data == null || data.getAddress().equals(address) ? null : data;
    }

    // The first listed instruction or defined data that starts from the first address through the
    // last, or null. A decoded instruction over such a unit contradicts the listing.
    private CodeUnit listedUnitWithin(Address first, Address last) {
        if (first == null || first.compareTo(last) > 0) {
            return null;
        }
        Instruction instruction = listing.getInstructionAt(first);
        if (instruction == null) {
            instruction = listing.getInstructionAfter(first);
        }
        Data data = listing.getDefinedDataAt(first);
        if (data == null) {
            data = listing.getDefinedDataAfter(first);
        }
        CodeUnit unit = instruction;
        if (data != null && (unit == null || data.getAddress().compareTo(unit.getAddress()) < 0)) {
            unit = data;
        }
        return unit == null || !unit.getAddress().getAddressSpace().equals(last.getAddressSpace())
            || unit.getAddress().compareTo(last) > 0 ? null : unit;
    }

    private boolean isDataStart(Address address) {
        return listing.getDefinedDataAt(address) != null;
    }

    // Whether the byte before the address is the last byte of an instruction or defined data.
    private boolean endsUnitBefore(Address address) {
        Address before = address.previous();
        if (before == null || !before.getAddressSpace().equals(address.getAddressSpace())) {
            return false;
        }
        Instruction instruction = listing.getInstructionContaining(before);
        if (instruction != null) {
            return instruction.getMaxAddress().equals(before);
        }
        Data data = listing.getDefinedDataContaining(before);
        return data != null && data.getMaxAddress().equals(before);
    }

    // Where the address falls in the unit, and the two boundaries a range could end at instead.
    private static String into(Address address, CodeUnit unit) {
        long offset = address.subtract(unit.getAddress());
        Address after = unit.getMaxAddress().next();
        return offset + " of " + unit.getLength() + " bytes into the " + describe(unit) + " at " + unit.getAddress()
            + (unit instanceof Instruction ? ", " + unit : "") + "; the nearest boundaries are " + unit.getAddress()
            + (after == null ? " and the end of its address space" : " and " + after);
    }

    private static String describe(CodeUnit unit) {
        return unit instanceof Data data ? "defined data (" + data.getDataType().getName() + ")" : "instruction";
    }

    private Address address(String text) {
        try {
            return toAddr(text);
        } catch (RuntimeException exception) {
            return null;
        }
    }

    private void unplaced(String range, String reason) {
        endsUnplaced++;
        report("end unplaced: " + range + ": " + reason + "; the end is not checked");
    }

    private void notRead(int number, String reason) {
        unread++;
        report("line " + number + " not read: " + reason);
    }

    private void report(String line) {
        printed++;
        if (printed <= MAX_PRINTED) {
            println(line);
        }
    }
}
