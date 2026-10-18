import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CancellationException;
import scientificmethod.QueryBatch;

/** Synthetic executor controls; no Ghidra database or original program. */
public class QueryBatchHarness {
    private static QueryBatch.Query query(String id) {
        QueryBatch.Query q = new QueryBatch.Query();
        q.id = id;
        q.script = "Synthetic.java";
        q.args = new String[0];
        q.maxCharacters = 40;
        q.complete = "DONE";
        q.reject = "MISSING|CAPPED";
        return q;
    }
    private static void check(boolean condition) { if (!condition) throw new AssertionError(); }
    private static void invalid(List<QueryBatch.Query> queries) {
        try { QueryBatch.validate(queries); throw new AssertionError("Accepted invalid plan"); }
        catch (IllegalArgumentException expected) {}
    }
    public static void main(String[] args) {
        ArrayList<String> calls = new ArrayList<>();
        List<QueryBatch.Result> results = QueryBatch.run(query("audit"),
            List.of(query("one"), query("missing"), query("limit"), query("last")), (q, out, err) -> {
                calls.add(q.id);
                if (q.id.equals("missing")) out.write("MISSING DONE");
                else if (q.id.equals("limit")) out.write("x".repeat(41));
                else out.write("DONE");
            });
        check(calls.equals(List.of("audit", "one", "missing", "limit", "last")));
        check(results.stream().map(r -> r.status).toList().equals(List.of("completed", "completed", "failed", "limited", "completed")));
        check(results.get(3).output.length() == 40);
        check(QueryBatch.execute(query("stderr"), (q, out, err) -> { out.write("DONE"); err.write("error"); }).status.equals("failed"));
        check(QueryBatch.execute(query("exception"), (q, out, err) -> { throw new Exception("missing script"); }).status.equals("failed"));
        check(QueryBatch.execute(query("control"), (q, out, err) -> out.write("partial")).status.equals("failed"));
        check(QueryBatch.execute(query("stderr-limit"), (q, out, err) -> err.write("x".repeat(41))).status.equals("limited"));
        results = QueryBatch.run(query("audit"), List.of(query("one")), (q, out, err) -> out.write("bad audit"));
        check(results.get(1).status.equals("not-run"));
        results = QueryBatch.run(query("audit"), List.of(query("cancel"), query("last")), (q, out, err) -> {
            if (q.id.equals("cancel")) throw new CancellationException();
            out.write("DONE");
        });
        check(results.get(1).status.equals("cancelled") && results.get(2).status.equals("not-run"));
        // A cancelled target that also reached its limit still stops the batch.
        results = QueryBatch.run(query("audit"), List.of(query("cancel"), query("last")), (q, out, err) -> {
            if (q.id.equals("cancel")) { try { out.write("x".repeat(41)); } catch (RuntimeException limit) {} throw new CancellationException(); }
            out.write("DONE");
        });
        check(results.get(1).status.equals("cancelled") && results.get(2).status.equals("not-run"));
        // The target that stops the batch fails; later targets are not run.
        results = QueryBatch.run(query("audit"), List.of(query("mutate"), query("last")), (q, out, err) -> {
            out.write("DONE");
            if (q.id.equals("mutate")) throw new QueryBatch.Stop("changed");
        });
        check(results.get(1).status.equals("failed") && results.get(1).error.equals("changed"));
        check(results.get(2).status.equals("not-run"));
        // An error thrown by a script is a failed target, and the batch goes on.
        results = QueryBatch.run(query("audit"), List.of(query("broken"), query("last")), (q, out, err) -> {
            if (q.id.equals("broken")) throw new NoClassDefFoundError("Missing");
            out.write("DONE");
        });
        check(results.get(1).status.equals("failed") && results.get(2).status.equals("completed"));
        check(QueryBatch.execute(query("rejected"), (q, out, err) -> out.write("MISSING DONE")).error.contains("MISSING"));
        invalid(List.of());
        invalid(List.of(query("same"), query("same")));
        ArrayList<QueryBatch.Query> many = new ArrayList<>();
        for (int i = 0; i < 32; i++) many.add(query("q" + i));
        QueryBatch.validate(many);
        many.add(query("q32"));
        invalid(many);
        QueryBatch.Query q = query("bad"); q.maxCharacters = 262145; invalid(List.of(q));
        q = query("bad"); q.script = "../Synthetic.java"; invalid(List.of(q));
        q = query("bad"); q.complete = ""; invalid(List.of(q));
        q = query("bad"); q.args = new String[] { null }; invalid(List.of(q));
        System.out.println("QueryBatch synthetic controls passed.");
    }
}
