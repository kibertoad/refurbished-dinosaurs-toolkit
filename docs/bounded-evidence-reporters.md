# Bounded instruction reports

Run `python -m pip install -r tools/evidence/requirements.txt` once in the Python
environment used for research. Python 3.10 or later, Capstone 5.0.7 and Node 22 or
later are required. `EVIDENCE_PYTHON` selects another Python executable.
Reports and their configurations stay in `GAME_DIR` and are not committed.
For example, from PowerShell with `GAME_DIR` set to the owned game's directory:

```powershell
node tools/evidence/report.mjs trace "$env:GAME_DIR/analysis/query.json"
```

Save redirected output under `GAME_DIR` too. The reporter does not run the
original program, invoke DOSBox or change a spec status.

The input names a hash-checked source and evidenced code regions. For MZ/FBOV,
the Node entry point derives relocation membership and canonical trampoline
targets from the source. The Python entry point is a lower-level interface for
synthetic data or already checked mappings. Its relocation metadata is supplied
input, not independently verified evidence. Use the Node entry point for originals.

```json
{
  "source": "../owned.exe",
  "sourceKind": "mz",
  "sha256": "replace-with-the-source-sha256",
  "entry": 64,
  "regions": [{
    "name": "resident-helper",
    "start": 64, "end": 96, "ip": 0, "segment": 4096,
    "entries": [64], "evidence": "build code range and established entry"
  }],
  "maxSteps": 512, "maxPaths": 64, "maxDepth": 8
}
```

Offsets are decimal shipped-file offsets. Region ends are exclusive; `ip` and
`segment` describe the mapping of the first byte. MZ resident mappings are checked
against the source. Overlay view mappings remain explicit researcher inputs and
must have distinct coordinates; the loader checks containment in a declared
payload, not the truth of a researcher's entry or code classification. Select
complete declared code regions for incoming-call searches. A narrower region is
a narrower search, even when every instruction in it was decoded. PE32/i386 is also supported as described below. Other executable
formats are rejected. `synthetic-raw` is for constructed test inputs.

Initial registers are unknown. An optional `registers` object supplies explicit
starting assumptions. Each access reports its effective segment, offset expression,
width, full interval, byte producers and missing producers. BP-derived offsets
accessed through BX use DS. `push ss; pop ds` establishes equality along that path.
Unknown segment/base aliases invalidate cached bytes; concrete disjoint address
domains can retain them. All assumptions remain conditional, and matching numeric
offsets alone never establish storage identity.

## Commands

All commands return JSON with the input fingerprint and schema `bounded-x86-v1`.
`trace` follows direct calls and local branches, records ordered effects and keeps
each return separately. `arguments`, `effects`, `returns`, `memory` and `guards`
select the relevant events from the same traversal. Event order numbers refer to
the complete traversal, so gaps in a projection are expected.

Arguments are recognized by consumed stack offsets and widths relative to each
call frame. Near returns occupy two bytes and far returns four; an immediately executed
`push cs` followed by a near call supplies a four-byte frame that must end in
a matching far return with unchanged stack/segment provenance; saved BP is
accounted for by actual pushes. LDS/LES consuming four bytes establishes a far
pointer grouping. Adjacent pushes alone do not. Register widening, frame cleanup,
stack overwrites and unknown return addresses remain visible. A root query may
set `returnBytes` to 4 for a far entry (default 2). The root return must use
that width and leave SP where it was on entry; otherwise the path stops and the
report is not complete within the model.

Return snapshots retain full and partial registers. Optional `returnContracts`
contain `entry`, `register`, `failures` (numeric encodings) and `evidence`. Only
that entry's returns get the label. Caller truncation, stores, flag producers and
branch predicates are decoded separately. A raw field with the same bits is not
a failure unless its own contract says so. A matching failure encoding does not
prove that the original can reach it.

Guards retain the compared value and branch polarity. A memory access or indirect
call says whether its pointer equals the checked expression. A later load after
a write or an unknown callee gets fresh provenance. An earlier access has no
later guard attached. Equality alone is not a safety proof: the consumer must
still inspect the predicate, domain and unknown effects.

`uses` adds `query: {"offset": 512, "width": 2, "access": "both"}` and optional
numeric `segment`. It traces each established entry instead of linearly decoding
a region. `controls` names known matching instruction offsets; a missed control
is an error. The report separates matching accesses, possible unknown aliases,
raw operand candidates and undecoded ranges. After a stopped effect trace,
explicit memory operands reached by the entry CFG are still inventoried, labelled
as operand observations with unknown values and segment state. Their default or
overridden segment-register name is retained. Reachability is conditional on
encoded guards and returning callees; these observations do not prove callee
preservation, effective-address values, or feasible native execution. A concrete
segment query leaves those unpropagated operands unresolved. LEA is not a use. The control is a known use of this
query, so a controlled inventory normally contains at least that use. For an
absence claim about additional uses, compare the inventory with that known set
and account for every gap; `negativeUsable` is deliberately conservative.

`incoming` adds a canonical `target`, a result `limit` and `controls` of known
call sites to any resolved target. For FBOV a `targetSelector` may instead name a
`descriptor` and canonical resident `trampoline` offset. The loader verifies both.
The reporter scans every byte of the named `searchRegions` (all regions by
default), including callers placed at higher addresses than the target's code. It separates relative
calls, resident relocations and overlay fixups, preserves encoded descriptor
words and resolved addresses, and distinguishes raw candidates from entry-path
instructions. Aliases resolve by canonical target. Computed calls, unrelocated
far calls and prefix-started raw candidates are excluded. Even a zero report
covers only the declared domain.

`dispatch` adds `dispatch.site`, `inputRegister`, `indexRegister`, up to 256 numeric
`inputs`, `indexEvidence`, and `table` with `start`, `count`, `stride`, `width`,
`countEvidence`, `offset` and `mappingEvidence`. `offset` is the encoded memory
displacement; `start` is its evidenced file mapping. The actual indirect near jump
must use `indexRegister` as its sole address register and read the declared width.
`indexDivisor` defaults to and must equal `stride`. Each case executes the decoded
normalization and gates before reporting the table position and raw target.
Returning before dispatch stays distinct from a stop on unsupported code.
Normalization, source-to-table mapping and count evidence remain in the report.

`allocation` adds `allocations`, each naming a call `site`, `requestRegister`,
`unitBytes`, `unitEvidence`, and optional `headerBytes` with `headerEvidence`.
The report keeps request arithmetic width and modulus, unit conversion, returned
registers and all later writes separately. An optional `extent` names `site`,
`register`, `unitBytes` and `evidence`; an optional `pointer` names `site`,
`segmentRegister`, `offsetRegister` and `evidence`. These are observation points
before the named instruction, after the call. Their values come from decoded
instructions. Concrete writes are compared against the observed pointer and
extent; an unknown value leaves the comparison unresolved. Header extent units,
usable capacity and pointer normalization need their own reading. Failure writes
and saved versus returned pointers stay visible; no rollback is inferred.

## Limits and assumptions

The decoder supports 16-bit addressing and a bounded subset of ordinary integer
operations: MOV/MOVZX/MOVSX, XCHG, low-result two/three-operand IMUL (flags unresolved),
LEA, LDS/LES, PUSH/POP, LEAVE, ADD/SUB, bitwise logic,
shifts, INC/DEC and effective-size sign extension. It follows direct near/far
calls, jumps, common conditional branches and balanced returns. Unsupported
instructions, repeat prefixes, 32-bit control transfers, indirect targets,
hardware accesses and recursion/loop limits stop the affected path. INC/DEC and
shifts leave flags unresolved; unknown branch conditions are explored both ways.
There is no solver claiming that all symbolic paths are feasible.

Explicit `callModels` can describe an external return for a conditional query.
Each has `site`, `evidence`, full-register `preserves`, and `cases` with register
values. A model for push-CS/near-call must explicitly give `returnBytes: 4`; its
existing CS word is checked and consumed. A mismatched encoded frame fails.
The model assumes a returning call with balanced stack and preserved CS;
it invalidates memory, flags and every unpreserved register. Its assumptions are
printed on each affected path. A model supplies no evidence about actual external
services, hardware behavior or native failure reachability.

Defaults cap each path at 512 instructions, the query at 20,000 steps, paths at
64 and call depth at 8. Raw scans stop after 65,536 byte positions (`scanLimit`,
maximum 1,048,576); use inventories stop after 64 entries (`entryLimit`, maximum
256). `instructionLimit` bounds graph traversal; `limit` bounds search results.
Caps, undecoded ranges and unsupported cases are explicit. Source size is capped
at 256 MiB, config size at 1 MiB (16 MiB for the relocation-expanded config the
Node wrapper pipes to Python) and each symbolic expression at 1,024 tuple nodes.
The Node wrapper caps output at 32 MiB and execution at 120 seconds. A limit never
turns a partial search into an absence claim. `completeWithinModel` means all
explored paths reached a return within these assumptions, not a complete reading
under the documentation standard.

## Acceptance and propagation

Run `python -B -m unittest discover -s tests/evidence -p 'test*.py'` and
`node --test tests/evidence/bridge.test.mjs`. The fixtures are entirely synthetic. The paired segment test reads distinct
values through the same BP-derived BX offset before and after `push ss; pop ds`;
the incoming-call test places a caller at a higher address than the target's code.
The template vendors an exact pinned copy; refine the toolkit source and update
the template's copy and digest record together. The website describes acceptance
contracts, while these executable tests establish delivered reporter behavior.
A reporter need not support every query. Each supported query must meet its
contract, with unsupported cases and remaining limits stated separately. A
request for reporter behaviour in a game's repository stays open until the
reporter passes that request's own case. Passing synthetic cases or adopting
review guidance alone does not close it. These requirements follow standards
PR 26, merged at `94f8f678afb05171567f48d9fb19488e48309f12`.
A report that says its search is complete makes that claim only for the stated
domain and model; it establishes neither native reachability nor a complete
reading under the standard.

### Refinements verified on restoration cases

Unresolved flag producers carry distinct generations: two different unmodelled
flag-setting instructions do not imply the same later branch outcome. Branches
on one unchanged producer still share their complementary condition. IMUL
reports only its low result; its signed overflow flags remain unresolved.
CBW/CWDE and CWD/CDQ report effective operand size, source/destination registers
and any mismatch with the decoder mnemonic. Width comes from the prefix and the
model's default operand size.

## PE32/i386 model

Select `sourceKind: "pe32"`. The Python loader independently parses the source
COFF/PE32 headers and section table for both CLI paths; supplied mapping metadata
is not trusted. The source fingerprint guard remains mandatory at the CLI.
Declare regions with file-offset `start`/exclusive `end`, established file-offset
`entries`, unique `name` and bounds `evidence`. `ip` and `segment` may be omitted:
virtual addresses are derived from ImageBase, section RVA and raw offset, with
segment zero as a mapping token. Supplied values must agree. Each region stays
inside one raw executable section. Virtual zero-fill and raw alignment padding
past VirtualSize are not initialized source code. Overlapping raw or virtual sections, truncated headers and sections,
unsupported machines, PE32+, conflicting mappings and MZ relocation/overlay
inputs fail. Headers and section metadata appear in every report's sourceMapping.

The model decodes i386 instructions with 32-bit effective addresses, ESP/EBP
stack frames, four-byte near return addresses and E8 rel32 target arithmetic.
File-offset query sites remain distinct from loaded virtual addresses: variable
`query.offset` and table `offset` are preferred-base VAs; `entry`, `target`,
`controls`, checkpoints and table `start` are file offsets. PE table mappings
are checked against the loaded raw section bytes. A SIB jump with only one
index register is accepted; its encoded scale participates in table selection.
No actual loader, import resolver or relocation execution is simulated. Rebasing,
packed/self-modifying code and runtime-written targets require another reading.

CS/DS/ES/SS bases are explicitly assumed zero under the flat Windows model.
Selectors remain separate values; FS/GS bases stay unknown even when their
selector value is supplied. Selector writes, descriptor loads, far transfers,
address-size overrides and operand-size control-transfer overrides stop paths.
Memory begins unknown: file data and virtual zero-fill are not silently turned
into runtime values. Scaled base/index addressing retains byte widths and
producers; unknown aliases invalidate memory rather than prove preservation.
Allocation pointer observations use only `offsetRegister` in this model; the
base is the explicit flat assumption, not the DS selector.

All ten commands have PE32 synthetic acceptance: use inventories with data
between entries; source/VA mapping; flat and unknown FS memory; mixed-width
arguments and callee cleanup; early effects; low-byte return predicates; partial
byte producers; late/raw incoming calls; guarded reloads after unknown writers;
wrapped allocation requests and observed extents; normalized/scaled dispatch
and rejection. Malformed PE sources, mapping mismatches, failed controls and
limits are negative cases. The legacy 16-bit suite remains mandatory.

Incoming queries follow established instruction entries, retaining overlaps as
explicit unresolved boundary gaps. A raw E8 candidate inside another instruction
is never a confirmed hit. The raw scan covers all selected declared regions,
including later callers; reached prefixed and indirect calls are also reported.
Unknown calls have explicit gaps and fallthrough assumes they return. Narrower
regions and exhausted budgets are partial scope, even with zero hits. There is
no universal call-completeness or native-reachability claim. PE indirect imports,
IAT trampolines, stored callables, exception dispatch and computed targets remain
unresolved rather than guessed. This initial model implements bounded reports,
not a solver, loader emulator or whole-program analysis.
