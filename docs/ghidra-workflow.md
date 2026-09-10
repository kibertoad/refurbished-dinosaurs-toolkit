# Ghidra workflow

Pin and document the Ghidra version used for each investigation. Prefer a temporary,
ignored project created by `analyzeHeadless`; scripts in `/ghidra` emit deterministic
text reports that can be reviewed and summarized without committing the database or
original executable.

For each finding, record executable SHA-256, image base/address, symbol or function,
script and arguments, relevant output, interpretation, confidence, and unanswered
questions. Decompiled pseudocode is evidence, not ground truth: corroborate it with
callers, data references, instruction context, runtime observations, manuals, and file
formats. Rename symbols and types in the local project as understanding improves.

Do not commit Ghidra projects, memory dumps, extracted binaries, or large raw reports.
Commit concise factual notes and small scripts. Keep third-party reverse-engineering
sources cited and separate from clean-room implementation decisions.

Typical headless invocation:

```powershell
& $analyzeHeadless $projectRoot Restoration -import $ownedExecutable `
  -scriptPath "$repositoryRoot/ghidra" `
  -postScript ReportFunctionSummary.java `
  -deleteProject
```
