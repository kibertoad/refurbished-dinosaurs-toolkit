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
   takes its bytes, a read that held varnodes tile is pieced from them, and any other read is
   unknown. A write keeps the bytes of overlapped varnodes that it does not cover, so `mov bl, al;
   xor bh, bh` gives BX the range of AL.

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

6. **Memory and calls are unknown unless shown otherwise.** A value in memory is unknown, except
   a stack slot at a known offset from the frame that a store on every path into the read wrote.
   A call to a routine the analysis does not walk, or an interrupt, makes every register and
   tracked stack slot it may write unknown.

7. **A derived count is reported as derived.** It replaces a declaration's count only for an
   exhaustive table that declares none, and only when the analysis proves the index's range on
   every path into the dispatch and that range steps through the table's rows: its stride a
   multiple of the table stride and its first value on a row. The report gives the range, the
   dispatch site, the bounding sites and the assumptions the derivation rests on (the table's
   segment and location stay declared). Without a proof the site stays unresolved, with one
   reason: no bound, a path that skips the bound, an operation without a transfer function, an
   unknown memory read, a call or interrupt not walked, the widening limit, or an index that does
   not step by the table stride. A declared count larger than the proven range is refused,
   because the extra rows cannot be read. A smaller one is followed, and the report lists the
   rows it leaves out.

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
