// Exports every function's start, size and body ranges in the Standard's notation, with a provenance file.
// @category Restoration

import java.io.BufferedWriter;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
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
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.mem.MemoryBlockSourceInfo;
import scientificmethod.Xxh3;

public class ExportFunctionInventory extends GhidraScript {
    private static final long MAX_FILE = 256L * 1024 * 1024;
    private static final int MAX_PROBLEM_LINES = 1000;

    private enum Notation { SEGMENTED, FLAT32, FLAT64 }

    /** A placed stretch of a body: a half-open range of linear addresses or of file offsets. */
    private record Piece(boolean offset, long start, long end, String startText) {
    }

    private Notation notation;
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
        Path provenance = output.resolveSibling(name.substring(0, name.length() - 4) + ".provenance.tsv");
        String snapshot = arguments[1];
        if (snapshot.isEmpty() || snapshot.length() > 200 || snapshot.chars().anyMatch(Character::isISOControl)) {
            throw new IllegalArgumentException("The snapshot identifier must be 1 to 200 characters with no tab or line break.");
        }
        for (Path path : List.of(output, provenance)) {
            if (Files.exists(path)) {
                throw new IllegalArgumentException("Output already exists: " + path);
            }
        }

        notation = notation();
        Map<String, String> source = provenance(snapshot);

        // Every row is built before anything is written, so a function that cannot be placed refuses
        // the whole export and no inventory with a smaller denominator appears.
        Memory memory = currentProgram.getMemory();
        List<String> rows = new ArrayList<>();
        Map<String, Address> starts = new LinkedHashMap<>();
        long bytes = 0;
        long withRanges = 0;
        FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
        while (functions.hasNext()) {
            if (monitor.isCancelled()) {
                throw new InterruptedException("Function inventory export cancelled.");
            }
            Function function = functions.next();
            Address entry = function.getEntryPoint();
            List<Piece> pieces = place(memory, entry, function.getBody(), true);
            Piece first = place(memory, entry, new AddressSet(entry), false).stream().findFirst().orElse(null);
            if (first == null) {
                continue; // The body's problems include the byte at the start.
            }
            // Ghidra keeps a body in one address space, and a space is an overlay or not as a whole, so
            // every piece is placed the way the start is.
            pieces.sort((a, b) -> Long.compare(a.start(), b.start()));
            List<Piece> body = new ArrayList<>();
            for (Piece piece : pieces) {
                Piece last = body.isEmpty() ? null : body.get(body.size() - 1);
                if (last != null && last.end() == piece.start()) {
                    body.set(body.size() - 1, new Piece(last.offset(), last.start(), piece.end(), last.startText()));
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

        if (!problems.isEmpty()) {
            for (String line : problems.subList(0, Math.min(problems.size(), MAX_PROBLEM_LINES))) {
                printerr(line);
            }
            if (problems.size() > MAX_PROBLEM_LINES) {
                printerr("Output stopped after " + MAX_PROBLEM_LINES + " of " + problems.size() + " problems.");
            }
            throw new IllegalStateException((problems.size() == 1 ? "1 problem keeps" : problems.size() + " problems keep")
                + " the inventory from listing every function's body in the Standard's notation. Nothing was written.");
        }

        Files.createDirectories(output.getParent());
        Path inventoryPart = Files.createTempFile(output.getParent(), "inventory-", ".partial");
        Path provenancePart = Files.createTempFile(output.getParent(), "provenance-", ".partial");
        try {
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
            // No replacement. The inventory moves last, so a run that stops before it leaves no
            // inventory, and a provenance file without its inventory is removed.
            Files.move(provenancePart, provenance);
            try {
                Files.move(inventoryPart, output);
            } catch (Exception e) {
                Files.deleteIfExists(provenance);
                throw e;
            }
        } finally {
            Files.deleteIfExists(inventoryPart);
            Files.deleteIfExists(provenancePart);
        }
        println("Exported " + rows.size() + " functions with " + bytes + " body bytes, " + withRanges
            + " of them with a ranges column, to " + output + ", and their provenance to " + provenance);
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
     * snapshot the researcher names, and this script's SHA-256. The file's bytes are those the program
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
        byte[] scriptBytes;
        try (InputStream in = script.getInputStream()) {
            scriptBytes = in.readAllBytes();
        }
        Map<String, String> lines = new LinkedHashMap<>();
        lines.put("xxh3", Xxh3.hexDigest(file));
        lines.put("sha256", sha256(file));
        lines.put("tool", "Ghidra");
        lines.put("tool_version", Application.getApplicationVersion());
        lines.put("snapshot", snapshot);
        lines.put("script", "ExportFunctionInventory");
        lines.put("script_sha256", sha256(scriptBytes));
        return lines;
    }

    /**
     * The body split into placed pieces. A byte in an overlay block is placed by its offset in the
     * imported file, which only the segmented notation allows; any other byte by its address. With
     * record, a byte that cannot be placed is recorded as a problem with the reason.
     */
    private List<Piece> place(Memory memory, Address entry, AddressSetView body, boolean record) {
        List<Piece> pieces = new ArrayList<>();
        int before = problems.size();
        AddressSet left = new AddressSet(body);
        for (MemoryBlock block : memory.getBlocks()) {
            AddressSet part = left.intersectRange(block.getStart(), block.getEnd());
            if (part.isEmpty()) {
                continue;
            }
            left.delete(part);
            if (!block.getStart().getAddressSpace().isOverlaySpace()) {
                for (AddressRange range : part) {
                    Piece piece = addressPiece(range);
                    if (piece == null) {
                        problem(entry, range, "cannot be written in four hex digits of segment and offset");
                    } else {
                        pieces.add(piece);
                    }
                }
                continue;
            }
            if (notation != Notation.SEGMENTED) {
                for (AddressRange range : part) {
                    problem(entry, range, "is in overlay block " + block.getName()
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
                        problem(entry, range, "is in overlay block " + block.getName() + ", whose bytes there come "
                            + (from == null ? "from no file" : "from " + from.getFilename() + ", not the imported file"));
                        continue;
                    }
                    long offset = info.getFileBytesOffset(range.getMinAddress());
                    pieces.add(new Piece(true, offset, offset + range.getLength(), String.format("0x%02X", offset)));
                }
            }
            for (AddressRange range : part) {
                problem(entry, range, "is in overlay block " + block.getName() + ", which gives it no source");
            }
        }
        for (AddressRange range : left) {
            problem(entry, range, "is in no memory block");
        }
        if (!record) {
            problems.subList(before, problems.size()).clear();
        }
        return pieces;
    }

    /** The piece an address range in a non-overlay block makes, or null when its start has no written form. */
    private Piece addressPiece(AddressRange range) {
        Address start = range.getMinAddress();
        long linear = start.getOffset();
        String text;
        if (notation == Notation.SEGMENTED) {
            if (!(start instanceof SegmentedAddress segmented)) {
                return null;
            }
            text = String.format("%04X:%04X", segmented.getSegment(), segmented.getSegmentOffset());
        } else {
            text = String.format(notation == Notation.FLAT32 ? "0x%08X" : "0x%016X", linear);
        }
        Piece piece = new Piece(false, linear, linear + range.getLength(), text);
        return endText(piece) == null ? null : piece;
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
            return String.format("0x%016X", piece.end());
        }
        long segment = Long.parseLong(piece.startText().substring(0, 4), 16);
        long offset = piece.end() - segment * 16;
        if (offset > 0xFFFF) {
            segment = (piece.end() - 0xFFFF + 15) / 16;
            offset = piece.end() - segment * 16;
        }
        return segment > 0xFFFF ? null : String.format("%04X:%04X", segment, offset);
    }

    private void problem(Address entry, AddressRange range, String why) {
        problems.add("Function at " + entry + ": body " + range.getMinAddress() + ".." + range.getMaxAddress()
            + " (" + range.getLength() + " bytes) " + why + ".");
    }

    private static String sha256(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
}
