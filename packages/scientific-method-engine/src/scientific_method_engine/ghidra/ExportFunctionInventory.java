// Exports every function's start, size and body ranges in the Standard's notation, with provenance and regions files.
// @category Restoration

import java.io.BufferedWriter;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import generic.jar.ResourceFile;
import ghidra.app.script.GhidraScript;
import ghidra.framework.Application;
import ghidra.program.database.mem.FileBytes;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressRange;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.address.SegmentedAddress;
import ghidra.program.model.address.SegmentedAddressSpace;
import ghidra.program.model.listing.CodeUnit;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.mem.MemoryBlockSourceInfo;
import scientificmethod.Xxh3;

public class ExportFunctionInventory extends GhidraScript {
    private static final long MAX_FILE = 256L * 1024 * 1024;
    private static final int MAX_PROBLEM_LINES = 1000;

    private enum Notation { SEGMENTED, FLAT32, FLAT64 }

    /**
     * A placed stretch of a body: a half-open range of linear addresses or of file offsets, with the
     * segment its start is written in (0 outside the segmented notation).
     */
    private record Piece(boolean offset, long start, long end, String startText, long segment) {
    }

    private Notation notation;
    /** Whether the Standard lets this program's overlay code be written by file offset: only an MZ file. */
    private boolean fileOffsets;
    private FileBytes imported;
    private final List<String> problems = new ArrayList<>();

    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 2) {
            throw new IllegalArgumentException(
                "Supply the output inventory path, ending in .tsv, and the identifier of the database snapshot it reads.");
        }
        Path output = Path.of(arguments[0]).toAbsolutePath().normalize();
        String name = output.getFileName().toString();
        if (!name.endsWith(".tsv") || name.endsWith(".provenance.tsv") || name.endsWith(".regions.tsv")) {
            throw new IllegalArgumentException("The inventory path must end in .tsv and not be a side file: " + output);
        }
        String stem = name.substring(0, name.length() - 4);
        Path provenance = output.resolveSibling(stem + ".provenance.tsv");
        Path regions = output.resolveSibling(stem + ".regions.tsv");
        String snapshot = arguments[1];
        if (snapshot.isEmpty() || snapshot.length() > 200 || snapshot.chars().anyMatch(Character::isISOControl)) {
            throw new IllegalArgumentException("The snapshot identifier must be 1 to 200 characters with no tab or line break.");
        }
        if (Files.exists(output)) {
            throw new IllegalArgumentException("Output already exists: " + output);
        }
        for (Path side : List.of(provenance, regions)) {
            if (Files.exists(side)) {
                // The side files move into place before the inventory, so one without its inventory is left
                // by an export that stopped between the moves. It is not deleted here, since it may be the
                // user's own.
                throw new IllegalArgumentException("Output already exists: " + side + ". With no inventory beside"
                    + " it, an earlier export stopped before moving the inventory into place; remove it and export again.");
            }
        }

        notation = notation();
        Map<String, String> source = provenance(snapshot);

        // Every row is built before anything is written, so a function that cannot be placed refuses
        // the whole export and no inventory with a smaller denominator appears.
        Memory memory = currentProgram.getMemory();
        List<String> rows = new ArrayList<>();
        Map<String, Address> starts = new LinkedHashMap<>();
        AddressSet bodies = new AddressSet();
        List<Address> entries = new ArrayList<>();
        long bytes = 0;
        long withRanges = 0;
        FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
        while (functions.hasNext()) {
            if (monitor.isCancelled()) {
                throw new InterruptedException("Function inventory export cancelled.");
            }
            Function function = functions.next();
            Address entry = function.getEntryPoint();
            bodies.add(function.getBody());
            entries.add(entry);
            String owner = "Function at " + entry + ": body";
            List<Piece> pieces = place(memory, owner, function.getBody(), true);
            Piece first = place(memory, owner, new AddressSet(entry), false).stream().findFirst().orElse(null);
            if (first == null || pieces.isEmpty()) {
                continue; // The body's problems include the byte at the start, or every byte of the body.
            }
            // Ghidra keeps a body in one address space, and a space is an overlay or not as a whole, so
            // every piece is placed the way the start is. A 64-bit address is compared unsigned.
            pieces.sort((a, b) -> Long.compareUnsigned(a.start(), b.start()));
            List<Piece> body = new ArrayList<>();
            for (Piece piece : pieces) {
                Piece last = body.isEmpty() ? null : body.get(body.size() - 1);
                if (last != null && Long.compareUnsigned(piece.start(), last.end()) < 0) {
                    problems.add("Function at " + entry + ": body bytes at " + piece.startText()
                        + " are placed where another part of the body already is, as when an overlay block views"
                        + " the same file bytes twice.");
                } else if (last != null && last.end() == piece.start()) {
                    body.set(body.size() - 1,
                        new Piece(last.offset(), last.start(), piece.end(), last.startText(), last.segment()));
                } else {
                    body.add(piece);
                }
            }
            long size = body.stream().mapToLong(p -> p.end() - p.start()).sum();
            Address earlier = starts.putIfAbsent(first.startText(), entry);
            if (earlier != null) {
                problems.add("Function at " + entry + " starts at " + first.startText()
                    + ", which the function at " + earlier + " already starts at.");
            }
            String ranges = "";
            if (body.size() > 1 || body.get(0).start() != first.start()) {
                List<String> written = new ArrayList<>();
                for (Piece piece : body) {
                    written.add(piece.startText() + ".." + endText(piece));
                }
                ranges = String.join(" ", written);
                withRanges++;
            }
            rows.add(first.startText() + "\t" + size + "\t" + ranges);
            bytes += size;
        }

        List<String> regionRows = regionRows(memory, bodies, entries);

        if (!problems.isEmpty()) {
            for (String line : problems.subList(0, Math.min(problems.size(), MAX_PROBLEM_LINES))) {
                printerr(line);
            }
            if (problems.size() > MAX_PROBLEM_LINES) {
                printerr("Output stopped after " + MAX_PROBLEM_LINES + " of " + problems.size() + " problems.");
            }
            throw new IllegalStateException((problems.size() == 1 ? "1 problem keeps" : problems.size() + " problems keep")
                + " the inventory and its regions from being written in the Standard's notation. Nothing was written.");
        }

        Files.createDirectories(output.getParent());
        Path inventoryPart = null;
        Path provenancePart = null;
        Path regionsPart = null;
        try {
            inventoryPart = Files.createTempFile(output.getParent(), "inventory-", ".partial");
            provenancePart = Files.createTempFile(output.getParent(), "provenance-", ".partial");
            regionsPart = Files.createTempFile(output.getParent(), "regions-", ".partial");
            try (BufferedWriter writer = Files.newBufferedWriter(inventoryPart, StandardCharsets.UTF_8)) {
                writer.write("start\tsize\tranges\n");
                for (String row : rows) {
                    writer.write(row);
                    writer.write('\n');
                }
            }
            try (BufferedWriter writer = Files.newBufferedWriter(provenancePart, StandardCharsets.UTF_8)) {
                for (Map.Entry<String, String> line : source.entrySet()) {
                    writer.write(line.getKey() + "\t" + line.getValue() + "\n");
                }
            }
            try (BufferedWriter writer = Files.newBufferedWriter(regionsPart, StandardCharsets.UTF_8)) {
                writer.write(String.join("\t", "kind", "start", "size", "instructions", "instructions_outside", "data",
                    "data_outside", "undefined", "undefined_outside") + "\n");
                for (String row : regionRows) {
                    writer.write(row);
                    writer.write('\n');
                }
            }
            // No replacement. The inventory moves last, so a run that stops before it leaves no
            // inventory, and side files moved without their inventory are removed.
            Files.move(provenancePart, provenance);
            boolean regionsMoved = false;
            try {
                Files.move(regionsPart, regions);
                regionsMoved = true;
                Files.move(inventoryPart, output);
            } catch (Exception e) {
                // A regions file that was there when its move failed is not this run's.
                if (regionsMoved) {
                    Files.deleteIfExists(regions);
                }
                Files.deleteIfExists(provenance);
                throw e;
            }
        } finally {
            if (inventoryPart != null) {
                Files.deleteIfExists(inventoryPart);
            }
            if (provenancePart != null) {
                Files.deleteIfExists(provenancePart);
            }
            if (regionsPart != null) {
                Files.deleteIfExists(regionsPart);
            }
        }
        println("Exported " + rows.size() + " functions with " + bytes + " body bytes, " + withRanges
            + " of them with a ranges column, to " + output + ", their provenance to " + provenance + ", and "
            + regionRows.size() + " region and anomaly rows to " + regions);
    }

    /** The Standard's address notation for this program, or an exception naming why it has none here. */
    private Notation notation() {
        AddressSpace space = currentProgram.getAddressFactory().getDefaultAddressSpace();
        String format = currentProgram.getExecutableFormat();
        if (space instanceof SegmentedAddressSpace) {
            if (format != null && format.contains("(NE)")) {
                throw new IllegalStateException("This is an NE program. Ghidra places NE segments at paragraphs of"
                    + " its own choosing, and this script does not convert them to the Standard's NE segments.");
            }
            fileOffsets = format != null && format.contains("(MZ)");
            return Notation.SEGMENTED;
        }
        if (space.getSize() == 32) {
            return Notation.FLAT32;
        }
        if (space.getSize() == 64 && format != null && format.contains("(ELF)")) {
            return Notation.FLAT64;
        }
        throw new IllegalStateException("The Standard has no notation for a " + space.getSize()
            + "-bit address space in a " + format + " program.");
    }

    /**
     * The provenance lines: the imported file's xxh3 and SHA-256, the analyzer and its version, the
     * snapshot the researcher names, and the SHA-256 of this script and of its Xxh3 helper. The file's bytes are those the program
     * holds, checked against the SHA-256 Ghidra recorded when it imported the file.
     */
    private Map<String, String> provenance(String snapshot) throws Exception {
        String recorded = currentProgram.getExecutableSHA256();
        if (recorded == null || recorded.isEmpty()) {
            throw new IllegalStateException("The program has no recorded SHA-256 of the file it was imported from.");
        }
        byte[] file = null;
        for (FileBytes candidate : currentProgram.getMemory().getAllFileBytes()) {
            if (candidate.getSize() > MAX_FILE) {
                continue;
            }
            byte[] content = new byte[(int) candidate.getSize()];
            if (candidate.getOriginalBytes(0, content) == content.length
                && sha256(content).equalsIgnoreCase(recorded)) {
                imported = candidate;
                file = content;
                break;
            }
        }
        if (file == null) {
            throw new IllegalStateException("No file bytes the program holds, of at most 256 MiB, match the SHA-256 "
                + recorded + " recorded for the imported file, so the file cannot be identified.");
        }
        ResourceFile script = getSourceFile();
        if (script == null || !script.exists()) {
            throw new IllegalStateException("This script cannot read its own source to record its revision.");
        }
        byte[] scriptBytes = readAll(script);
        // The xxh3 value comes from the helper, so its revision is recorded as well.
        ResourceFile helper = new ResourceFile(new ResourceFile(script.getParentFile(), "scientificmethod"), "Xxh3.java");
        if (!helper.exists()) {
            throw new IllegalStateException("This script cannot read the source of its helper " + helper
                + " to record its revision.");
        }
        byte[] helperBytes = readAll(helper);
        Map<String, String> lines = new LinkedHashMap<>();
        lines.put("xxh3", Xxh3.hexDigest(file));
        lines.put("sha256", sha256(file));
        lines.put("tool", "Ghidra");
        lines.put("tool_version", Application.getApplicationVersion());
        lines.put("snapshot", snapshot);
        lines.put("script", "ExportFunctionInventory");
        lines.put("script_sha256", sha256(scriptBytes));
        lines.put("helper", "scientificmethod/Xxh3.java");
        lines.put("helper_sha256", sha256(helperBytes));
        return lines;
    }

    private static byte[] readAll(ResourceFile file) throws Exception {
        try (InputStream in = file.getInputStream()) {
            return in.readAllBytes();
        }
    }

    /**
     * The addresses split into placed pieces. A byte in an overlay block is placed by its offset in the
     * imported file, which the Standard allows only in an MZ file; any other byte by its address. With
     * record, a byte that cannot be placed is recorded as a problem with the reason, after owner.
     */
    private List<Piece> place(Memory memory, String owner, AddressSetView body, boolean record) {
        List<Piece> pieces = new ArrayList<>();
        int before = problems.size();
        AddressSet left = new AddressSet(body);
        for (MemoryBlock block : memory.getBlocks()) {
            if (left.isEmpty()) {
                break;
            }
            AddressSet part = left.intersectRange(block.getStart(), block.getEnd());
            if (part.isEmpty()) {
                continue;
            }
            left.delete(part);
            if (!block.getStart().getAddressSpace().isOverlaySpace()) {
                for (AddressRange range : part) {
                    Piece piece = addressPiece(range);
                    if (piece == null) {
                        problem(owner, range, noWrittenForm(range));
                    } else {
                        pieces.add(piece);
                    }
                }
                continue;
            }
            if (!fileOffsets) {
                for (AddressRange range : part) {
                    problem(owner, range, "is in overlay block " + block.getName()
                        + ", and the Standard places code by file offset only in an MZ file");
                }
                continue;
            }
            for (MemoryBlockSourceInfo info : block.getSourceInfos()) {
                AddressSet sourced = part.intersectRange(info.getMinAddress(), info.getMaxAddress());
                part.delete(sourced);
                for (AddressRange range : sourced) {
                    FileBytes from = info.getFileBytes().orElse(null);
                    if (from == null || !from.equals(imported)) {
                        problem(owner, range, "is in overlay block " + block.getName() + ", whose bytes there come "
                            + (from == null ? "from no file" : "from " + from.getFilename() + ", not the imported file"));
                        continue;
                    }
                    long offset = info.getFileBytesOffset(range.getMinAddress());
                    pieces.add(new Piece(true, offset, offset + range.getLength(), String.format("0x%02X", offset), 0));
                }
            }
            for (AddressRange range : part) {
                problem(owner, range, "is in overlay block " + block.getName() + ", which gives it no source");
            }
        }
        for (AddressRange range : left) {
            problem(owner, range, "is in no memory block");
        }
        if (!record) {
            problems.subList(before, problems.size()).clear();
        }
        return pieces;
    }

    /**
     * The piece an address range in a non-overlay block makes, or null when it is outside the program's
     * default address space or its start or end has no written form.
     */
    private Piece addressPiece(AddressRange range) {
        Address start = range.getMinAddress();
        if (!start.getAddressSpace().equals(currentProgram.getAddressFactory().getDefaultAddressSpace())) {
            return null;
        }
        long linear = start.getOffset();
        String text;
        long segment = 0;
        if (notation == Notation.SEGMENTED) {
            if (!(start instanceof SegmentedAddress segmented)) {
                return null;
            }
            segment = segmented.getSegment();
            text = String.format("%04X:%04X", segment, segmented.getSegmentOffset());
        } else {
            text = String.format(notation == Notation.FLAT32 ? "0x%08X" : "0x%016X", linear);
        }
        Piece piece = new Piece(false, linear, linear + range.getLength(), text, segment);
        return endText(piece) == null ? null : piece;
    }

    /** Why addressPiece has no piece for a range. */
    private String noWrittenForm(AddressRange range) {
        AddressSpace space = range.getMinAddress().getAddressSpace();
        if (!space.equals(currentProgram.getAddressFactory().getDefaultAddressSpace())) {
            return "is in address space " + space.getName() + ", which the Standard's notation does not cover";
        }
        if (notation == Notation.SEGMENTED) {
            return "cannot be written in four hex digits of segment and offset";
        }
        return "ends past the highest address " + (notation == Notation.FLAT32 ? "8" : "16") + " hex digits can write";
    }

    /**
     * The exclusive end of a piece. A segmented end keeps the start's segment while its offset fits
     * in four hex digits and moves to the lowest segment that holds it otherwise; null past FFFF:FFFF.
     */
    private String endText(Piece piece) {
        if (piece.offset()) {
            return String.format("0x%02X", piece.end());
        }
        if (notation == Notation.FLAT32) {
            return piece.end() >= 0x1_0000_0000L ? null : String.format("0x%08X", piece.end());
        }
        if (notation == Notation.FLAT64) {
            // A body that ends at the top of the space ends at 2^64, which wraps to 0.
            return Long.compareUnsigned(piece.end(), piece.start()) <= 0 ? null : String.format("0x%016X", piece.end());
        }
        long segment = piece.segment();
        long offset = piece.end() - segment * 16;
        if (offset > 0xFFFF) {
            segment = (piece.end() - 0xFFFF + 15) / 16;
            offset = piece.end() - segment * 16;
        }
        return segment > 0xFFFF ? null : String.format("%04X:%04X", segment, offset);
    }

    /**
     * The rows of the regions file: a region row for each initialized executable block, or for each
     * part of an overlay block that one file range supplies; then an outside row for each stretch of a
     * function body in no region, split the same way at block and source boundaries; then an entry row
     * for each function start where no instruction starts. Each row counts the instruction, defined-data and undefined bytes of its range, in all
     * and outside every function body. A range that cannot be written in the notation is a problem.
     * The outside and entry rows lie in function bodies, so when a body could not be placed they would
     * repeat its problem, and only the region rows are checked.
     */
    private List<String> regionRows(Memory memory, AddressSet bodies, List<Address> entries) {
        boolean bodiesPlaced = problems.isEmpty();
        List<String> rows = new ArrayList<>();
        AddressSet measured = new AddressSet();
        for (MemoryBlock block : memory.getBlocks()) {
            if (!block.isInitialized() || !block.isExecute()) {
                continue;
            }
            AddressSet whole = new AddressSet(block.getStart(), block.getEnd());
            measured.add(whole);
            for (AddressSet part : blockParts(memory, whole)) {
                row(rows, memory, "region", "Executable block " + block.getName() + ":", part, bodies);
            }
        }
        if (!bodiesPlaced) {
            return rows;
        }
        for (AddressSet part : blockParts(memory, bodies.subtract(measured))) {
            row(rows, memory, "outside", "Function body outside every executable block:", part, bodies);
        }
        Listing listing = currentProgram.getListing();
        for (Address entry : entries) {
            if (listing.getInstructionAt(entry) == null) {
                row(rows, memory, "entry", "Function start without an instruction:", new AddressSet(entry), bodies);
            }
        }
        return rows;
    }

    /**
     * The set split into the contiguous parts that place() writes as one piece each: a part per memory
     * block and, in an overlay block, per source range, then the parts in no block.
     */
    private static List<AddressSet> blockParts(Memory memory, AddressSetView set) {
        List<AddressSet> parts = new ArrayList<>();
        AddressSet left = new AddressSet(set);
        for (MemoryBlock block : memory.getBlocks()) {
            if (left.isEmpty()) {
                break;
            }
            AddressSet part = left.intersectRange(block.getStart(), block.getEnd());
            if (part.isEmpty()) {
                continue;
            }
            left.delete(part);
            if (block.getStart().getAddressSpace().isOverlaySpace()) {
                for (MemoryBlockSourceInfo info : block.getSourceInfos()) {
                    AddressSet sourced = part.intersectRange(info.getMinAddress(), info.getMaxAddress());
                    part.delete(sourced);
                    for (AddressRange range : sourced) {
                        parts.add(new AddressSet(range));
                    }
                }
            }
            for (AddressRange range : part) {
                parts.add(new AddressSet(range));
            }
        }
        for (AddressRange range : left) {
            parts.add(new AddressSet(range));
        }
        return parts;
    }

    /**
     * Adds the row of one part from blockParts(), or records the problem that keeps it from being
     * written. Such a part places as one piece or not at all.
     */
    private void row(List<String> rows, Memory memory, String kind, String what, AddressSet range, AddressSet bodies) {
        List<Piece> pieces = place(memory, what, range, true);
        if (pieces.size() != 1) {
            return; // place() recorded why the range has no written form.
        }
        AddressSet outside = range.subtract(bodies.intersectRange(range.getMinAddress(), range.getMaxAddress()));
        AddressSet instructions = covered(range, true);
        AddressSet data = covered(range, false);
        AddressSet undefined = range.subtract(instructions).subtract(data);
        rows.add(String.join("\t", kind, pieces.get(0).startText(), Long.toString(range.getNumAddresses()),
            count(instructions), count(instructions.intersect(outside)), count(data), count(data.intersect(outside)),
            count(undefined), count(undefined.intersect(outside))));
    }

    /** The bytes of the range that instructions, or defined data, cover, including a unit that starts before it. */
    private AddressSet covered(AddressSet range, boolean instructions) {
        Listing listing = currentProgram.getListing();
        AddressSet covered = new AddressSet();
        Address first = range.getMinAddress();
        CodeUnit before = instructions ? listing.getInstructionContaining(first) : listing.getDefinedDataContaining(first);
        if (before != null) {
            covered.add(before.getMinAddress(), before.getMaxAddress());
        }
        Iterator<? extends CodeUnit> units = instructions ? listing.getInstructions(range, true)
            : listing.getDefinedData(range, true);
        while (units.hasNext()) {
            CodeUnit unit = units.next();
            covered.add(unit.getMinAddress(), unit.getMaxAddress());
        }
        return covered.intersect(range);
    }

    private static String count(AddressSetView set) {
        return Long.toString(set.getNumAddresses());
    }

    private void problem(String owner, AddressRange range, String why) {
        problems.add(owner + " " + range.getMinAddress() + ".." + range.getMaxAddress()
            + " (" + range.getLength() + " bytes) " + why + ".");
    }

    private static String sha256(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
}
