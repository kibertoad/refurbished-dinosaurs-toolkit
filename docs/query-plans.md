# One-session Ghidra query plans

Use a plan for related references, caller windows and bounded decompilations that
close one behavior contract. Give each requested target its own ID and completion
and rejection controls. Reuse the loaded program rather than launching Ghidra for
each adjacent address. This does not establish complete reading or behavior parity.

Run the packaged `RunQueryPlan.java` with the existing headless launcher:

```powershell
$scripts = scientific-method-engine ghidra-scripts
& "$env:GHIDRA_HOME/support/analyzeHeadless.bat" $projectDirectory $projectName `
  -process $programName -noanalysis -readOnly -scriptPath "$scripts;$localScripts" `
  -postScript RunQueryPlan.java $planPath $newIgnoredRunDirectory
```

The plan and output remain in ignored local storage. The output directory must
not exist. Supply a v1 JSON plan with `source` (absolute owned source path),
`sourceSha256`, `snapshot` (the project's audited checkpoint identity), `audit`
and `queries`. The audit and every query have this shape:

```json
{
  "id": "caller-window",
  "script": "ReportInstructionWindow.java",
  "args": ["0x1234", "12"],
  "maxCharacters": 16384,
  "complete": "EXPECTED TARGET-SPECIFIC COMPLETION TEXT",
  "reject": "(?i)error|not found|no instruction|output capped|limit reached"
}
```

Choose the actual reporter arguments from its catalog. The example control is a
placeholder, not an acceptance test. Completion must identify the requested
target, not just a generic reporter heading. Rejection must cover that reporter's
missing-target and internal-limit messages, as well as its errors. One query per
target makes these controls attributable. A positive known target belongs in
each contract batch. A completed report means only its configured controls passed.

There must be 1..31 queries plus one audit, with unique IDs, at most 64 arguments
per query, each at most 4,096 characters. Output and stderr are each bounded by
`maxCharacters` (1..262,144 UTF-16 characters). The JSON plan is at most 256 KiB.
Only script basenames are accepted; recursive plans are refused. Scripts must be
reviewed read-only reporters. Keep project changes in a separate analysis step.
The launcher retains ownership of its process, timeout and local project policy.

Before any child runs, the runner compares the source SHA-256 with both the plan
and the program's imported executable SHA-256. This is an import identity check,
not proof that mapped bytes, loader settings or flow overrides are correct. The
required project-specific audit script must check those relationships and emit
the audit's completion marker. Its failure prevents all target queries.

`report.json` records the plan/source/runner/helper digests, Ghidra version,
program path and modification number, snapshot identity, language/compiler and
each executed script's digest. Every target finishes as
`completed`, `failed`, `limited`, `cancelled` or `not-run`. Stderr, a missed
completion control, a rejection control or an exception fails that target;
later targets still run. Audit failure or cancellation stops subsequent targets.
The source is hashed again after execution. A changed program modification number
prevents further script execution. Any non-completed target, program change or
source change makes the batch incomplete and raises a script error. Check the JSON and
the headless log: Ghidra's launcher exit code alone is not script acceptance.
If the process is killed, a report left at `running` is incomplete; missing rows
are unexecuted or unknown, never successful negatives. Preserve the plan with it.

This runner does not add a cache. Reuse an export only under the existing
[audited export workflow](ghidra-workflow.md), with matching source, project
snapshot, script/tool versions, query controls and limits. Changed provenance
requires a new export. For repeated raw caller/operand scans use the existing
content-keyed `scan-index`, rather than decoding the executable again for every
query. Record the contract's remaining callers, producers, indirect transfers and
evidence dependencies explicitly; an output cap remains an open dependency.
