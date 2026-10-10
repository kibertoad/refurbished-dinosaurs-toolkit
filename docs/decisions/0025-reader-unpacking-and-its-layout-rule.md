# ADR 0025: the reader unpacks packed executables by a versioned layout rule

Status: accepted. Adds the `unpack` command and the `unpack` export to the executable reader.

## Context

A build entry in the documentation standard gives a packed file's `packer` and an `unpacked` item
with the `size`, `xxh3`, `format` and `tool` of its unpacked form. Findings about a packed file
locate code in the unpacked form, and a bounded report on it names that form's `xxh3`. Until now
each restoration that met an LZEXE, PKLITE or EXEPACK file unpacked it with a tool of its own
choosing. Unpackers differ in the header they rebuild (field values, relocation order, padding),
so two projects reading the same packed file could end up with different bytes and different
hashes for findings about the same code.

The unpacked bytes are only reproducible if the rule that rebuilds them is fixed and named. The
rebuilt header also holds fields the packed file does not supply in a form the reader can check,
such as the original allocation sizes and checksum.

Two parts of the request were not taken:

- A standard-checker rule that a packed file's `unpacked.tool` names a tool the checker knows. The
  checker enforces the documentation standard and does not extend it, and the standard names no
  list of tools. Such a rule would also tie each checker release to the reader's versions.
- A release note whenever the layout changes, kept apart from the version. A layout change is a
  major release of the reader instead, which the changeset and the changelog carry.

## Decision

1. Unpacking belongs to the executable reader, which owns executable-format parsing. It decodes
   the packer's format itself and never runs the decompressor in the file. It reads that
   decompressor's header words and relocation table and nothing of its code.
2. The reader writes the unpacked file by a numbered layout rule, given in the bounded evidence
   reporter guide. The `unpack` report names the rule in `layout`, and `tool` gives the reader's
   package name and version. Header fields the packed file does not supply are set by the rule,
   and the report names them in `setByLayout`; they are never presented as the original values.
3. A change to the layout rule increments `UNPACK_LAYOUT` and ships as a major release of the
   reader, since it changes the `xxh3` every unpacked file is recorded by.
4. A packer is recognized by its signature alone. A file without a recognized signature is
   refused with an error that says this does not show the file is not packed. Every read is bounded
   and each failure names the file offset; a stream, copy or relocation outside its bounds fails
   the command and writes nothing.
5. Packers are added one at a time, each with a synthetic encoder in the reader's tests that
   covers every token form and relocation form of its format.

## Consequences

- Restorations that unpack with the reader agree on the bytes and the `xxh3` of the unpacked
  form for as long as they use the same reader major version.
- An unpacked form recorded with another tool keeps its own `tool`; switching it to the reader
  changes its `xxh3`, so the build entry and every config naming the old hash change with it.
- LZEXE 0.90 and 0.91 come first. PKLITE and EXEPACK follow as their own changes.
- EXEPACK gives no field for the length of its stub, so the reader finds the relocation table
  after the message that ends every known stub, and checks that the table ends where the header
  says the EXEPACK block ends. It reads that message and nothing else of the stub's code.
- PKLITE keeps its facts in its stub's code rather than in header words, so the reader matches
  that code against known byte sequences, as [ADR 0027](0027-pklite-stubs-matched-by-known-sequences.md)
  records.
- The prepared config is unchanged: `unpack` runs in Node and does not reach the engine.
