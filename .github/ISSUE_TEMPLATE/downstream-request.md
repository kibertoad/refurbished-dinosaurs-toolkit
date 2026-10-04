---
name: Downstream tooling request
about: A restoration reports something the toolkit's packages would not let it do
labels: downstream-request
---

<!--
This repository is public. Do not paste anything that came from the original game: executable
bytes, disassembly listings with addresses and operands, extracted strings or assets, decompiler
output, analyzer exports or full reports. Describe the shape instead: field names, region layout,
offsets, the instruction sequence in mnemonics.

Filing from the command line:
  gh issue create --repo kibertoad/refurbished-dinosaurs-toolkit \
    --template "Downstream tooling request" --label downstream-request
-->

## Restoration

<!-- Which restoration is asking. -->

## What you ran

<!-- Package names and versions, or the toolkit commit of a vendored copy. -->

## Findings

<!--
One subsection per finding, under your own id (for example "### G27"). Replies will use your ids.
For each: what you tried, what came back (an error, a stopped report, a report that claimed too
little or too much), what you expected, and what you ask for.
-->

### <your id>

- Command: <!-- trace, incoming, callees, a Ghidra script, a reader or checker call -->
- Config shape: <!-- the fields used, with proprietary values removed -->
- Result:
- Expected:
- Ask:

## Positive control

<!-- A case in your game where the answer is already known, so a change can be checked against it. -->

## Workaround

<!-- A hand reading, a one-off script, a local patch to a vendored copy. -->
