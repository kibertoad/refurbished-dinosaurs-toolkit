// Runs bounded read-only queries in one audited, already-loaded Ghidra program.
// @category Restoration

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import generic.jar.ResourceFile;
import ghidra.app.script.GhidraScript;
import ghidra.app.script.GhidraScriptUtil;
import ghidra.app.script.ScriptControls;
import ghidra.framework.Application;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CancellationException;
import scientificmethod.QueryBatch;

public class RunQueryPlan extends GhidraScript {
    private static final class Plan {
        int version;
        String source;
        String sourceSha256;
        String snapshot;
        QueryBatch.Query audit;
        List<QueryBatch.Query> queries;
    }

    private String sha256(byte[] bytes) throws Exception {
        return java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    private String resourceSha256(ResourceFile resource) throws Exception {
        try (var stream = resource.getInputStream()) { return sha256(stream.readAllBytes()); }
    }

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 2 || currentProgram == null) {
            throw new IllegalArgumentException("Supply query-plan.json and a NEW local output directory with a loaded program.");
        }
        Path planPath = Path.of(args[0]).toAbsolutePath();
        Path destination = Path.of(args[1]).toAbsolutePath();
        if (Files.size(planPath) > 262144) throw new IllegalArgumentException("Plan exceeds 256 KiB.");
        byte[] planBytes = Files.readAllBytes(planPath);
        Gson gson = new GsonBuilder().setPrettyPrinting().create();
        Plan plan = gson.fromJson(new String(planBytes, StandardCharsets.UTF_8), Plan.class);
        if (plan == null || plan.version != 1 || plan.source == null || plan.sourceSha256 == null ||
            !plan.sourceSha256.matches("[a-fA-F0-9]{64}") || plan.snapshot == null || plan.snapshot.isBlank() ||
            plan.snapshot.length() > 256 || plan.queries == null || plan.queries.isEmpty()) {
            throw new IllegalArgumentException("Invalid v1 query plan.");
        }
        List<QueryBatch.Query> all = new ArrayList<>();
        all.add(plan.audit);
        all.addAll(plan.queries);
        QueryBatch.validate(all);
        Path source = Path.of(plan.source).toAbsolutePath();
        String sourceHash = sha256(Files.readAllBytes(source));
        if (!sourceHash.equalsIgnoreCase(plan.sourceSha256) ||
            !sourceHash.equalsIgnoreCase(currentProgram.getExecutableSHA256())) {
            throw new IllegalArgumentException("Source/import identity mismatch; no query ran.");
        }
        Files.createDirectory(destination); // Never overwrite another run.
        Map<String, Object> report = new LinkedHashMap<>();
        report.put("version", 1);
        report.put("planSha256", sha256(planBytes));
        report.put("sourceSha256", sourceHash);
        report.put("ghidra", Application.getApplicationVersion());
        report.put("program", currentProgram.getDomainFile().getPathname());
        report.put("snapshot", plan.snapshot);
        report.put("requested", all);
        report.put("language", currentProgram.getLanguageID().toString());
        report.put("compiler", currentProgram.getCompilerSpec().getCompilerSpecID().toString());
        report.put("status", "running");
        ResourceFile runner = GhidraScriptUtil.findScriptByName("RunQueryPlan.java");
        report.put("runnerSha256", resourceSha256(runner));
        report.put("helperSha256", resourceSha256(new ResourceFile(runner.getParentFile(), "scientificmethod/QueryBatch.java")));
        long modification = currentProgram.getModificationNumber();
        report.put("modificationNumber", modification);
        Map<String, String> scripts = new LinkedHashMap<>();
        report.put("scripts", scripts);
        Path reportPath = destination.resolve("report.json");
        Files.writeString(reportPath, gson.toJson(report));
        List<QueryBatch.Result> results = QueryBatch.run(plan.audit, plan.queries, (query, output, error) -> {
            if (monitor.isCancelled()) throw new CancellationException();
            if (currentProgram.getModificationNumber() != modification) {
                throw new IllegalStateException("Program changed during batch; target not run.");
            }
            ResourceFile scriptFile = GhidraScriptUtil.findScriptByName(query.script);
            if (scriptFile == null) throw new IllegalArgumentException("Script not found: " + query.script);
            scripts.put(query.id, resourceSha256(scriptFile));
            PrintWriter out = new PrintWriter(output, true);
            PrintWriter err = new PrintWriter(error, true);
            GhidraScript script = GhidraScriptUtil.getProvider(scriptFile).getScriptInstance(scriptFile, err);
            script.setScriptArgs(query.args);
            try { script.execute(getState(), new ScriptControls(out, err, monitor)); }
            catch (ghidra.util.exception.CancelledException exception) { throw new CancellationException(); }
            finally { out.flush(); err.flush(); }
        });
        report.put("results", results);
        boolean sourceUnchanged = sourceHash.equals(sha256(Files.readAllBytes(source)));
        boolean programUnchanged = currentProgram.getModificationNumber() == modification;
        boolean completed = sourceUnchanged && programUnchanged && results.stream().allMatch(result -> result.status.equals("completed"));
        report.put("sourceUnchanged", sourceUnchanged);
        report.put("programUnchanged", programUnchanged);
        report.put("status", completed ? "completed" : "incomplete");
        Files.writeString(reportPath, gson.toJson(report));
        if (!completed) throw new IllegalStateException("Query plan incomplete; inspect every target in " + reportPath);
        println("Query plan completed: " + reportPath);
    }
}
