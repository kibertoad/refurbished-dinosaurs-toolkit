# ADR 0034: value ranges over p-code for dispatch-table bounds

Status: accepted. Builds on [ADR 0003](0003-established-instruction-semantics.md). Issue 460
tracks the work; the [roadmap](../ROADMAP.md) has its slices.

## Context

A declared indirect jump table (`indirectJumps`) gives its entry count, and the researcher reads
that count from the code: from a mask such as `and bx, 3` followed by a scaling `shl bx, 1`, or
from a guard such as `cmp bx, 9` and `ja` to a default branch. Declared computed call targets
(issue 457) rest on the same reading. Every negative that relies on an exhaustive table therefore
relies on the researcher's count, and the report can only list it as an assumption.

The bound is a fact about every path into the dispatch: nothing between the bound and the
dispatch writes the index, no other path enters the dispatch with an unbounded index, the scaling
is the one the bound assumes, and the table is read through the segment the declaration names. A
matcher that recognizes `and` and `shl` near the dispatch checks none of that, and it would be
handwritten instruction semantics, which ADR 0003 forbids. A wrong bound would make an exhaustive
table, and with it `negativeUsable`, rest on a guess.

The engine has no analysis that joins values across paths. `x86/values.py`, `machine.py` and
`relational.py` carry concrete and symbolic values along one path at a time.

## Decision

1. **The domain is a strided interval per varnode.** A `Range` holds the unsigned values `lo`,
   `lo + stride`, ..., `hi` of one width. The stride carries the scaling: `and bx, 3; shl bx, 1`
   gives 0, 2, 4, 6, which an interval alone would widen to 0..6 and so admit odd offsets into a
   word table. A small value set would also hold that case, but it needs a size cap that a large
   table exceeds, and it has no short widening chain. A signed view of a range is used only when
   the range lies in one sign half; otherwise a signed comparison stays undecided.

2. **Semantics come from p-code alone.** Each p-code operation has a transfer function or none.
   When every input is one value, the output is what the engine's p-code evaluator
   (`pcode.evaluate`) computes, so constants follow the same semantics as a trace. An operation
   that reads one varnode twice gets the rules the evaluator's same-term rules give (`x ^ x` and
   `x - x` are 0), plus `x + x` as a doubling, which keeps the stride of `add bx, bx`. An
   operation without a transfer function (division, remainder, user operations other than
   `segment`) and a `LOAD` make their output unknown, and the analysis records which operation
   did. No mnemonic is consulted. The CS-override idiom is dropped as the interpreter drops it.

3. **Registers are held by varnode.** Held varnodes never overlap. A read inside a held varnode
   takes its bytes. A read that covers held varnodes is pieced from them, with every byte no held
   varnode covers unknown, so after `xor bh, bh` BX lies in 0..255 whatever BL holds. A read that a
   held varnode only partly overlaps is unknown. A write keeps the bytes of overlapped varnodes that
   it does not cover, so `mov bl, al; xor bh, bh` gives BX the range of AL.

4. **Every transfer function is checked for soundness two ways.** For sampled input ranges, every
   output `pcode.evaluate` computes from values in them lies in the output range. Through instructions that lift
   to the operation, the registers Unicorn computes from sampled values in the input ranges lie in
   the output ranges (ADR 0003, decision 4). PIECE, `INT_LESSEQUAL` and `INT_SLESSEQUAL` have no
   Unicorn case, because no general-purpose real-mode instruction lifts to them; the register
   file's tiling, which pieces bytes the way PIECE does, has one.

5. **The analysis runs forward over the routine's control flow to the dispatch site.** It joins
   ranges where paths merge, and widens at a loop head after a bounded number of passes, which
   the query can set and the report names when it is reached. On each edge of a `CBRANCH` it
   narrows the operands of the comparison that wrote the condition's flags, using that
   comparison's p-code operation (`INT_LESS`, `INT_EQUAL`, `INT_SLESS` and the others) and the
   edge's outcome. It narrows only a register that still holds the value the comparison read, so
   `cmp bx, 9; ja default` bounds BX on the fall-through edge, and a write to BX between the
   comparison and the dispatch undoes the bound.

   In detail: the walk starts at the routine's start and follows the successors the CFG walk
   gives, stepping over calls and interrupts. A loop head is a site that a back edge of a
   depth-first walk from the start enters. A condition narrows through `BOOL_NEGATE`, through
   `BOOL_AND` and `BOOL_OR` (where either side decides the result, the edge joins both cases, so
   `jbe` after `cmp bx, 9` gives 0..9), and through a comparison of a register, or of a register
   plus or minus a constant, with another such operand. The signed jumps test `OF` and `SF`, which
   p-code writes from `INT_SBORROW` and the sign of the difference, so they narrow nothing; a signed
   guard does not bound an unsigned table offset anyway. An edge that no value can take is not
   walked. A dispatch jump continues at the rows its caller gives, and a dispatch call at its
   return site. Paths that enter the routine's code other than at its start are not read; the
   caller has to show that none exists. Two walked instructions that overlap leave the instruction
   boundary unverified, so both starts are reported as transfers the walk could not follow and the
   range is not proven.

6. **Memory and calls are unknown unless shown otherwise.** A value in memory is unknown, except
   a stack slot at a known offset from the routine's entry stack pointer that a store on every path
   into the read wrote and that lies at or above the stack pointer, since an interrupt can overwrite
   the bytes below it. A store through any other address, and a write to SS, forget every slot.
   The analysis walks no callee or handler, so a call or an interrupt makes every register and
   every stack slot unknown. An instruction whose p-code branches inside itself (a repeated string
   operation) makes every register it writes unknown.

7. **A derived count is reported as derived.** It replaces a declaration's count only for an
   exhaustive table that declares none, and only when the analysis proves the index's range on
   every path into the dispatch and that range steps through the table's rows: its stride a
   multiple of the table stride and its first value on a row. The report gives the range, the
   dispatch site, the bounding sites and the assumptions the derivation rests on (the table's
   segment and location stay declared). A range is bounded when it holds at most 256 values, the
   most rows a declared table may have. Each value carries the sites that bounded it, or the
   causes that left it unbounded. A byte that may hold any value bounds nothing, so after
   `mov bl, [si]; xor bh, bh` the bounding site of BX is the `xor`. Without a proof the site stays
   unresolved with its reasons, each at a site: no bound (a register value from the routine's
   entry), a path that skips the bound (at the merge, with the bounding sites), an operation without
   a transfer function, an operation that faults (a division by a constant zero), an unknown memory
   read, a call or interrupt not walked, the widening limit, a transfer the walk could not follow,
   the instruction limit, or an index that does not step by the table stride. A declared count
   larger than the proven range is refused, because the extra rows cannot be read. A smaller one is
   followed, and the report lists the rows it leaves out.

8. **It applies wherever tables are read.** `reach`, `inventory-check` and the path commands that
   read `indirectJumps` take derived counts, and declared computed call targets take them once
   that declaration lands.

## Consequences

- The domain and transfer functions ship first and change no report. Reports change only when
  the analysis and the report fields land, each with its own release label.
- A table count the code bounds no longer has to be declared, and a declared count the code
  contradicts is caught. A count the analysis cannot prove keeps the declaration and its evidence
  as today, so no existing configuration needs a change.
- New p-code operations reach the analysis as unknown until they get a transfer function, which
  comes with its soundness cases.
- Strided intervals lose precision on unions of distant values and on signed ranges that cross
  zero. Those cases stay unresolved with a reason; they are not guessed.
