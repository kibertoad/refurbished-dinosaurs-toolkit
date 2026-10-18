package scientificmethod;

import java.io.Writer;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.concurrent.CancellationException;
import java.util.regex.Pattern;

/** Bounded, sequential query execution in a caller-owned analysis session. */
public final class QueryBatch {
    public static final int MAX_QUERIES = 32;
    public static final int MAX_CHARACTERS = 262144;

    public static final class Query {
        public String id;
        public String script;
        public String[] args;
        public String complete;
        public String reject;
        public int maxCharacters;
    }

    public static final class Result {
        public String id;
        public String status;
        public String output;
        public String error;
        public Result(String id, String status, String output, String error) {
            this.id = id;
            this.status = status;
            this.output = output;
            this.error = error;
        }
        /** Set when the target's failure stops the batch; not part of the report. */
        transient boolean stopsBatch;
    }

    @FunctionalInterface
    public interface Executor {
        void run(Query query, Writer output, Writer error) throws Exception;
    }

    /** Thrown by an executor when its target fails and no later target may run. */
    public static final class Stop extends RuntimeException {
        public Stop(String message) { super(message); }
    }

    private static final class Limit extends RuntimeException {
        Limit() { super("Output limit reached."); }
    }
    private static final class Capture extends Writer {
        private final StringBuilder text = new StringBuilder();
        private final int maximum;
        private boolean limited;
        Capture(int maximum) { this.maximum = maximum; }
        public void write(char[] buffer, int offset, int length) {
            int remaining = maximum - text.length();
            text.append(buffer, offset, Math.min(length, remaining));
            if (length > remaining) { limited = true; throw new Limit(); }
        }
        public void flush() {}
        public void close() {}
        public String toString() { return text.toString(); }
    }

    /** Validate the audit and every target; returns them in run order, audit first. */
    public static List<Query> validate(Query audit, List<Query> queries) {
        if (queries == null) throw new IllegalArgumentException("Supply 1..31 bounded queries.");
        ArrayList<Query> all = new ArrayList<>();
        all.add(audit);
        all.addAll(queries);
        validate(all);
        return all;
    }

    /** Validate every target before any script can run. Patterns are owner-supplied controls. */
    public static void validate(List<Query> queries) {
        if (queries == null || queries.isEmpty() || queries.size() > MAX_QUERIES) {
            throw new IllegalArgumentException("Supply 1..32 bounded queries.");
        }
        HashSet<String> ids = new HashSet<>();
        for (Query query : queries) {
            if (query == null || query.id == null || !query.id.matches("[a-zA-Z0-9_-]{1,80}") || !ids.add(query.id)) {
                throw new IllegalArgumentException("Query IDs must be unique simple names.");
            }
            if (query.script == null || !query.script.matches("[a-zA-Z0-9_]+\\.java") ||
                query.script.equals("RunQueryPlan.java") || query.args == null || query.args.length > 64 ||
                query.maxCharacters < 1 || query.maxCharacters > MAX_CHARACTERS ||
                query.complete == null || query.complete.isEmpty() || query.reject == null || query.reject.isEmpty()) {
                throw new IllegalArgumentException("Each query needs a script basename, bounded args/output and completion/rejection controls.");
            }
            for (String arg : query.args) {
                if (arg == null || arg.length() > 4096) throw new IllegalArgumentException("Invalid query argument.");
            }
            Pattern.compile(query.complete);
            Pattern.compile(query.reject);
        }
    }

    /** A limit or missed control is never a successful negative result. */
    public static Result execute(Query query, Executor executor) {
        Capture output = new Capture(query.maxCharacters);
        Capture error = new Capture(query.maxCharacters);
        String status = "completed";
        String failure = "";
        boolean stopsBatch = false;
        try { executor.run(query, output, error); }
        catch (CancellationException exception) { status = "cancelled"; failure = "Session cancelled."; }
        catch (Stop exception) { status = "failed"; failure = exception.getMessage(); stopsBatch = true; }
        catch (Exception | LinkageError | AssertionError exception) {
            status = "failed";
            failure = exception.getClass().getSimpleName() + ": " + exception.getMessage();
        }
        if (!status.equals("cancelled") && (output.limited || error.limited)) status = "limited";
        else if (status.equals("completed")) {
            var rejected = Pattern.compile(query.reject).matcher(output.toString());
            if (!error.toString().isEmpty()) { status = "failed"; failure = "Script wrote to stderr."; }
            else if (rejected.find()) { status = "failed"; failure = "Rejection control matched: " + rejected.group(); }
            else if (!Pattern.compile(query.complete).matcher(output.toString()).find()) {
                status = "failed";
                failure = "Completion control not observed.";
            }
        }
        String diagnostic = error.toString() + failure;
        if (diagnostic.length() > query.maxCharacters) diagnostic = diagnostic.substring(0, query.maxCharacters);
        Result result = new Result(query.id, status, output.toString(), diagnostic);
        result.stopsBatch = stopsBatch;
        return result;
    }

    /**
     * Audit failure, cancellation or a {@link Stop} stops the batch; any other query failure is
     * local and later targets still run.
     */
    public static List<Result> run(Query audit, List<Query> queries, Executor executor) {
        validate(audit, queries);
        ArrayList<Result> results = new ArrayList<>();
        Result audited = execute(audit, executor);
        results.add(audited);
        boolean stopped = !audited.status.equals("completed");
        for (Query query : queries) {
            Result result = stopped
                ? new Result(query.id, "not-run", "", "Audit failed, session cancelled or batch stopped.")
                : execute(query, executor);
            results.add(result);
            if (result.status.equals("cancelled") || result.stopsBatch) stopped = true;
        }
        return results;
    }
}
