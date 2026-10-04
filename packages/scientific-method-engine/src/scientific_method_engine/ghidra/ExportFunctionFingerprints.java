// Exports normalized function and instruction fingerprints for cross-version mapping.
// @category Restoration

import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.scalar.Scalar;

public class ExportFunctionFingerprints extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) {
            throw new IllegalArgumentException("Expected output TSV path.");
        }

        Path output = Path.of(args[0]).toAbsolutePath().normalize();
        Files.createDirectories(output.getParent());
        // The rows go to a temporary file that replaces the output only once every function and
        // instruction is written, so a failed or cancelled run leaves no partial TSV behind.
        Path temporary = Files.createTempFile(output.getParent(), "fingerprints-", ".partial");
        try {
            write(temporary);
            Files.move(temporary, output, StandardCopyOption.REPLACE_EXISTING);
        }
        finally { Files.deleteIfExists(temporary); }
        println("Wrote " + output);
    }

    private void write(Path temporary) throws Exception {
        try (BufferedWriter writer = Files.newBufferedWriter(temporary, StandardCharsets.UTF_8)) {
            writer.write("kind\tfunction\taddress\tend\tsize\tinstructionCount\tfingerprint\tsignature\n");
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                monitor.checkCancelled();
                Function function = functions.next();
                List<Instruction> instructions = new ArrayList<>();
                InstructionIterator iterator = currentProgram.getListing()
                    .getInstructions(function.getBody(), true);
                StringBuilder functionSignature = new StringBuilder();
                while (iterator.hasNext()) {
                    Instruction instruction = iterator.next();
                    instructions.add(instruction);
                    functionSignature.append(normalize(instruction)).append(';');
                }

                String entry = hex(function.getEntryPoint());
                writer.write(String.join("\t",
                    "F", entry, entry, hex(function.getBody().getMaxAddress()),
                    Long.toString(function.getBody().getNumAddresses()),
                    Integer.toString(instructions.size()), sha256(functionSignature.toString()),
                    functionSignature.toString()));
                writer.newLine();

                for (Instruction instruction : instructions) {
                    String signature = normalize(instruction);
                    writer.write(String.join("\t",
                        "I", entry, hex(instruction.getAddress()),
                        hex(instruction.getMaxAddress()), Integer.toString(instruction.getLength()),
                        "1", sha256(signature), signature));
                    writer.newLine();
                }
            }

            InstructionIterator allInstructions = currentProgram.getListing().getInstructions(true);
            while (allInstructions.hasNext()) {
                monitor.checkCancelled();
                Instruction instruction = allInstructions.next();
                Function function = currentProgram.getFunctionManager()
                    .getFunctionContaining(instruction.getAddress());
                String owner = function == null ? "" : hex(function.getEntryPoint());
                String signature = normalize(instruction);
                writer.write(String.join("\t",
                    "A", owner, hex(instruction.getAddress()),
                    hex(instruction.getMaxAddress()), Integer.toString(instruction.getLength()),
                    "1", sha256(signature), signature));
                writer.newLine();
            }
        }
    }

    // A scalar that names an address in the program is relocatable between versions, so it is
    // fingerprinted as an address rather than by value.
    private boolean isProgramAddress(long value) {
        try {
            return currentProgram.getMemory().contains(toAddr(value));
        } catch (RuntimeException outOfRange) {
            return false;
        }
    }

    private String normalize(Instruction instruction) {
        StringBuilder result = new StringBuilder(instruction.getMnemonicString().toLowerCase());
        for (int operand = 0; operand < instruction.getNumOperands(); operand++) {
            result.append('|');
            Object[] objects = instruction.getOpObjects(operand);
            for (Object object : objects) {
                if (object instanceof Register register) {
                    result.append('R').append(register.getName().toLowerCase());
                } else if (object instanceof Address address) {
                    result.append(address.isMemoryAddress() ? "A" : "X");
                } else if (object instanceof Scalar scalar) {
                    long value = scalar.getSignedValue();
                    long unsigned = scalar.getUnsignedValue();
                    if (instruction.getFlowType().isFlow() || isProgramAddress(unsigned)) {
                        result.append('A');
                    } else {
                        result.append('S').append(value);
                    }
                } else {
                    result.append('O').append(object.getClass().getSimpleName());
                }
                result.append(',');
            }
        }
        return result.toString();
    }

    private static String hex(Address address) {
        return String.format("%08x", address.getOffset());
    }

    private static String sha256(String value) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] hash = digest.digest(value.getBytes(StandardCharsets.UTF_8));
        StringBuilder text = new StringBuilder(hash.length * 2);
        for (byte b : hash) {
            text.append(String.format("%02x", b & 0xff));
        }
        return text.toString();
    }
}
