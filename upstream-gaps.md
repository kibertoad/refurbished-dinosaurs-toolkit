# Upstream gaps found while moving the engine to pypcode

Gaps in pypcode, Ghidra's x86 SLEIGH specification and the other libraries the engine uses, found
during ADR 0003. Each entry has the engine's workaround and what an upstream change would let it
drop. Every observation used pypcode 4.0.0 (Ghidra 12.1 SLEIGH files), Capstone 5.0.7 and
synthetic bytes only.

## pypcode

### A short buffer lifts as if it were zero-padded

`Context.translate` lifts an instruction that needs more bytes than the buffer holds by reading
zeros past the end: a lone `66` lifts as `66 00 00` (`add`), with an IMARK length of 3. Nothing
reports that the decoder read past the buffer.

- Engine workaround: lift exactly the bytes Capstone decoded and stop the path when the IMARK length
  differs from Capstone's size (`pcode_backend.Frame`).
- Upstream: raise, or return a flag, when decoding reads beyond the supplied bytes.

## x86 SLEIGH specification (Ghidra)

### Real mode: a CS override writes CS from the instruction address

With a `cs:` prefix, `x86:LE:16:Real Mode` emits `CS = (inst_next >> 4) & 0xf000` before the
`segment(CS, offset)` operation. That derives CS from the linear address of the next instruction,
which is only right when the code segment starts at a 64 KiB boundary. The real CS is whatever the
far transfer into the code loaded.

- Engine workaround: recognise that exact three-op sequence and drop it, because the declared region
  supplies CS (`pcode.cs_idiom`).
- Upstream: read CS as a register in real mode, as the other segment overrides do, instead of
  computing it.

### SCAS loads ES:[DI] once per flag

`scasb` (`ae`) lifts with three `LOAD`s of the same `segment(ES, DI)` address: one for CF, one for
OF and one for the difference that sets ZF, SF and PF. CMPS loads each operand once.

- Engine workaround: the string iteration caches the destination load, so the report records one
  read per iteration (`Pypcode.string_iteration`).
- Upstream: load the operand once into a temporary, as the CMPS constructor does. A consumer that
  counts or traces memory accesses sees three reads where the CPU performs one.

### Read-modify-write forms reload the memory operand after storing it

With a memory destination, most read-modify-write instructions load the operand several times
before the `STORE` and again after it. The later loads compute the flags from what memory holds
after the write. This happens in both `x86:LE:16:Real Mode` and `x86:LE:32:default`.

| Instruction | Load/store sequence |
|---|---|
| `add [bx],ax` (`01 07`) | `LOAD LOAD LOAD STORE LOAD LOAD LOAD` |
| `inc byte [bx]` (`fe 07`) | `LOAD LOAD STORE LOAD LOAD LOAD` |
| `rcl word [bx],1` (`d1 17`) | `LOAD LOAD STORE LOAD` |
| `shld [bx],ax,3` (`0f a4 07 03`) | `LOAD LOAD STORE LOAD LOAD LOAD LOAD` |

`adc`, `neg`, `shl` and `sar` show the same shape. `not`, `xchg` and `cmpxchg` load once.
`xadd` loads four times but never after the store. The CPU reads the operand once and takes the
flags from the computed result. A reload returns something else when the operand is a
memory-mapped port or memory another agent writes. A consumer that counts or traces memory accesses
sees reads the CPU never performs.

- Engine workaround: the instruction frame caches each operand's first load, and a reload after a
  full-width store returns the stored value (`pcode_backend.Frame`).
- Upstream: load the operand once into a temporary, and compute the flags from the result
  temporary, not from memory after the store.

## Capstone (not pypcode, recorded for completeness)

### RCL r/m, 1 on a memory operand reports its count with size 0

`d0 /2` and `d1 /2` with a memory operand (`rcl byte ptr [bx], 1`, `rcl word ptr [bx], 1`) decode
with an immediate operand of value 1 and size 0. The register forms and every other rotate or shift
by one report size 1 or 2.

- Engine workaround: read a rotate's immediate count as 8 bits regardless of the operand size.
- Upstream: report the implicit count with the same size as the other forms.
