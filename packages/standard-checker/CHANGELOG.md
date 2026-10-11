# @scientific-method/standard-checker

## 4.2.0

### Minor Changes

- bf0da8a: The comparison with the base branch now finds an ID taken twice before the two branches meet. A spec ID or deviation whose file the change adds since the fork point, committed or not, fails `IDENTIFIERS-6` when the tip of the base branch (`--base`, or `origin/$GITHUB_BASE_REF` or `origin/main`) holds a file under that ID that matches neither the working tree, the index, nor the file at any commit of the branch since the fork point: `<ID> also exists at <ref> with content this branch never held; renumber this one before it is merged`. A copy at the tip that began as one of the branch's versions, merged or cherry-picked there and possibly edited since, passes. Run before committing, such as from a `pre-commit` hook, it lets the branch renumber before commit messages cite the ID. `--base <ref>` now names the branch the change merges into: deleted IDs, areas, deviations and layout tables are compared from where HEAD forked from `<ref>`, so entries that branch gained since are no longer reported as deleted, and a message about that comparison names the fork point and the ref when they differ.

## 4.1.2

### Patch Changes

- 27ecade: The range-end check no longer fails a range whose end is the byte of a one-byte function, or of a one-byte range of a function's body. That byte is both its first and its last, so a correct range that stops where such a function starts was failed with an end that would claim the function's byte. A range ending there still fails when a longer range of another function ends on the same byte.

## 4.1.1

### Patch Changes

- 05852f8: A superseded format entry keeps the `files` it had when it was replaced (IDENTIFIERS-7). A pattern of such an entry that matches no manifest file now passes when it matches a path that the build's `<ID>.other-files.yaml` gives by its own path, so retiring an entry whose files left the manifest no longer means emptying its `files`. It still fails with `files pattern <p> matches no file of <build> in its manifest or its list of other files` when it matches neither, and names the directory exclusion, the prose list, or the list that could not be read or does not exist, when one of those is all it could have matched. Entries at every other status still need their files in the manifest. Where the Other files section names a `<ID>.other-files.yaml` that does not exist, the skipped comparison of a listing record with the list of other files now gives that reason.

## 4.1.0

### Minor Changes

- 7deef29: `standard-coverage` counts once a byte that two inventory rows both list. `bytes` and `citedBytes` are over the union of the in-scope bodies, so a shared function tail no longer adds its bytes once per function, and a new `sharedBytes` figure gives the in-scope bytes that more than one in-scope function lists. The text line ends with `; <n> bytes listed by more than one in-scope function` when there are any. A location in shared bytes still cites every function that lists them. A byte counts as cited when a cited function lists it. The range-end check names every function whose range ends on the byte a location ends on, where it named only one, and says which of them that byte ends a range of.

## 4.0.1

### Patch Changes

- 1a0b71e: A path into a data directory now passes when a build's `<ID>.other-files.yaml` gives it, as well as when a manifest lists it. A Source location that cites a bundled archive the manifest leaves out no longer fails once the first manifest file in its directory turns the check on there. A path or directory in another case names the file that spells it, a path under a directory exclusion, in its case or another, fails naming the exclusion, and a path in neither list fails with `path <p> is in no build's manifest or list of other files`.

## 4.0.0

### Major Changes

- 6f2032f: `standard-coverage` gives no figures for an inventory with a row it cannot read. It prints `<path>: not measured, <n> of <m> rows are invalid`, and `--json` lists such an inventory, and one it cannot read at all, under a new `unmeasured` array of `path` and `reason` instead of under `inventories`. Figures over the readable rows left the other functions out of both counts, and an inventory whose rows were all invalid printed `0 of 0 functions cited`.

## 3.0.1

### Patch Changes

- 88ec74c: A source entry in `spec/sources/` may cite its external source's own paths, such as `src/gpl/state.c` from another project's repository or the members of a shipped archive. In a source entry, a path under a `--rebuild` directory counts as the rebuild's only when it exists in the restoration. Other Markdown in the spec, including files under `spec/sources/` that are not source entries, keeps the existing rule. Whether a path exists in the rebuild is now decided ignoring case on every platform, so Linux reports the same paths as Windows and macOS.

## 3.0.0

### Major Changes

- afa327e: Validation runs are recorded in `validation/`, one file per run named `<date>-<commit>.md`, in place of `VALIDATION.md`. A marked test file of a validated row passes while any run file records the hash it has now, `--record-validation` deletes every other run file except one that lists a test file a sparse checkout does not hold, and the check fails while `VALIDATION.md` exists. Two branches that each record a run no longer conflict. The migration guide shows how to move an existing record.

## 2.9.1

### Patch Changes

- be596da: Read `''` inside a single-quoted YAML value as one single quote, as YAML does. A value such as
  `'C:\Users\O''Brien\Saves'` was cut at the first quote, so a listing record or spec file could not
  hold a value with a single quote and also a double quote or a backslash.

## 2.9.0

### Minor Changes

- 4b749df: Check a build's listing record, `spec/builds/<ID>.listing.yaml`, which the standard now allows a build to keep and name in its `listing` field (ENTRY-TYPES-16 and ENTRY-TYPES-17). The checker fails a `listing` field that names another file or a missing one, a record no build names, and a record whose fields or items break the standard's shape: an unknown `links` mode, `cycles` that does not match it, a medium without a valid prefix or layout, a disc image `source` that neither the manifest nor the list of other files gives by its own path, an item without exactly one of `size`, `link` and `stopped`, a path listed twice, items out of byte order, or an archive member outside the archives the record lists. It then compares the record with the manifest and `<ID>.other-files.yaml` as ENTRY-TYPES-18 says, and fails with the path named: a file in neither list and under no directory exclusion, a size that differs from the manifest's, a manifest path or listed other file the record lacks, and a link or stopped path the list of other files does not give by its own path. Paths on a disc the record's media leave out, and audio tracks of a disc whose tracks the listing did not read, are exempt. Where the list of other files is prose, or `<ID>.other-files.yaml` could not be read, the comparison with it is named as a skipped step with that reason.

  A manifest path under a directory exclusion (a path in the list of other files that ends in `/`) now fails, with or without a listing record (ENTRY-TYPES-15). Problems with build entries, manifests, lists of other files and Code ranges now end with the label of the Builds rule they break, ENTRY-TYPES-9 to ENTRY-TYPES-19.

## 2.8.0

### Minor Changes

- 74cd9fe: Read a function inventory's `ranges` column, which the work protocol allows when a function's body is not one range from its start: half-open `start..end` ranges in the start's notation, separated by spaces, whose total is the row's size and one of which holds the start. In an NE file, each range and each body without ranges must end in the segment it starts in. `standard-coverage` measures citations against those ranges, so a location in a gap of the body no longer cites the function and one in a part placed elsewhere does. The range-end check also fails a range that ends on the last byte of any of a body's ranges, taking ranges that touch as one. The `.provenance.tsv` and `.regions.tsv` files the protocol puts beside an inventory are no longer read as inventories.

## 2.7.2

### Patch Changes

- d2bcb20: The skipped step for calls and emits against a Parameters section the argument-count check cannot count now quotes `None.` and names the first thing that stops the count: an empty section, text with no list, text before or after the list, or the list item by position that names more than one parameter, does not open with the parameter's code span, does not follow it directly with a colon, or holds no parameter name in it.

## 2.7.1

### Patch Changes

- 3754a4d: Stop counting an event's emitter as one of its handlers in the argument count check. A rule that an event's glossary entry names, whose own procedure emits the event and whose When it runs section does not name it, is the emitter, so its Parameters section is no longer compared with the event's `emit`s and no longer gives a skipped step while it is in prose. A handler that emits its event again names the event in When it runs and is still counted. An entry of the emitter's split counts as a handler only when its own When it runs section names the event.

## 2.7.0

### Minor Changes

- e275a83: Add `--squashed OLD=NEW[+NEW...],...`, and the check-documentation action's `squashed` input, for a restoration that squashes its superseded entries into their replacements before its spec is relied on outside the project. A listed deletion passes instead of failing IDENTIFIERS-6 when the entry's `superseded_by` at the base names exactly the listed replacements and each exists and is not superseded or is squashed in the same change, so `A=B,B=C` squashes a chain. A listed ID that still exists fails, which keeps a squashed ID from being used again while the option stays in place, one the base does not have is named as a skipped step, and a squashed ID still cited in the spec, the glossary, the code, the `--references` directories, `parity/` or `deviations/` fails, a build or source alias included, with a message that names the replacements to cite.

## 2.6.0

### Minor Changes

- 88028e5: Fail a range that ends on a function's last byte. Where the repository has function inventories in `coverage/`, the ones `standard-coverage` reads, a range whose end is an inventoried function's last byte stops a byte short, since ranges are half-open. The check covers every range a location of a current entry gives in that build and file, by address or by offset, and the address ranges written in the body of an entry whose locations all name that one file. The problem gives the end the range should have. Without inventories nothing is checked and no step is reported as skipped.

## 2.5.1

### Patch Changes

- 60dcd21: Skip `dist` directories when walking the code, reference and rebuild directories, as `bin`, `obj` and `node_modules` already are. A file name that only a build of a package has, such as `index.js`, no longer counts as a rebuild source file the spec may not name, and addresses in bundled output are no longer checked against comments.

## 2.5.0

### Minor Changes

- e884c41: Check more of the addresses a restoration gives as evidence, and the spec's paths into the rebuild. The address check now reads PowerShell comments (`#` and `<# … #>`) as well as C#, TypeScript and JavaScript ones, and checks the addresses the code itself uses, as numbers or inside strings: each must be recorded in an entry that the comment trailing its line or the nearest comment above it cites. `--message <file>` checks the addresses of a commit message against the entries it cites, for a commit-msg hook. `--rebuild <dirs>` (default `src,tests`) fails a spec file that names a path in the rebuild's directories or a source file found in them, as the standard's rule that the spec never names the rebuild requires.

## 2.4.0

### Minor Changes

- 5b15fb2: Add `--scheduled-generation`, for a restoration that updates `spec/index/` and `PARITY.md` on its main branch only, such as from a scheduled job. The check then neither writes nor compares those files, and fails when the change since the base edits, adds or removes one. A file that matches its copy at the base branch's tip passes, as does a change that only regenerates them. The fork point now takes in every head of a merge in progress, an octopus merge's included.

## 2.3.0

### Minor Changes

- 780405e: Add `standard-coverage`, which measures how much of each analysed file the spec's locations cite against the function inventories in `coverage/`, lists the functions no entry cites, and with `--require-complete` fails while any in-scope function is uncited.

## 2.2.0

### Minor Changes

- 4f63593: Check argument counts. Every `call` must pass one argument for each item of the called rule's Parameters list, every call to a function a rule defines one for each parameter of its `define`, and every `emit` one for each item of the Parameters list of each handler its glossary entry names. A split callee or handler is counted in each entry that lists one of the calling or emitting rule's builds. Two `emit`s of one event in rules that share a build must pass the same number of arguments, which is the only check an event with no handlers gets. A Parameters list counts only when each item opens with one code span followed directly by a colon. Any other Parameters section, a list with an item such as ``- `x`, `y`: the cell`` included, cannot be counted, so the calls and emits against it are named as a skipped step and do not fail the run. So is a call or emit whose argument list is never closed.

## 2.1.0

### Minor Changes

- fd8cff7: A deviation file may carry two new items. `Replaces`, right after Departs from, names the entries of Departs from that the deviation replaces entirely; only a `mandatory` deviation has one, and it names at least one rule, format or screen. `Tests`, between Justification (or Default) and Dropped, lists the test files that check the rebuild does what the deviation says. While the deviation is not dropped, each listed path must be a file that mentions the deviation's ID, and that does not mention `GAME_DIR`, since nothing records a local run of a deviation's tests. A parity row that a listed `mandatory` deviation names in `Replaces` has `Tests` set to `None`, since the original behaviour is gone; its tests go in the deviation's Tests item. When such a row's Code is `complete` and its spec status is not `disputed`, it is now `deviated` if every `mandatory` deviation it lists has a Tests item, and `implemented` otherwise. `PARITY.md` gains a `deviated` row in its Status table, so a repository that upgrades runs the check once without `--check` to regenerate it. A test file of a parity row or a deviation now has to mention the whole ID: `RULE-SCORE-0010` no longer counts as mentioning `RULE-SCORE-001`, and a listed directory is reported where the check used to stop with an error.

## 2.0.0

### Major Changes

- 9f4e0e8: An experiment whose `starting_state` is none of the forms the documentation standard defines (a save or save patch in `saves/`, `new-game`, `emulated-call` or null) now fails with `starting_state <value> is none of the forms the standard defines`, which lists them. Before, any other value was accepted and the run reported only the missing save hash, so a typo such as `new_game` or a value the standard has no word for, such as `cold-boot`, read as a missing hash, and with a hash given it passed. A save or save patch is a file under `saves/`, so `saves/` alone or a path with an empty, `.` or `..` segment fails the same way. Such an experiment, and one that lacks `starting_state`, no longer gets the save hash problems, since which hashes the fixture needs depends on the form; a missing field is reported only as `front matter lacks starting_state`.

## 1.1.0

### Minor Changes

- 03c3ce3: A run without `--base` whose fork point with `origin/$GITHUB_BASE_REF` (or `origin/main`) does not resolve now names the comparison with the base branch as a skipped step, so its result line reads `spec check passed with skipped steps:` and says to fetch the branch or pass `--base`. Before, the comparison was left out without a word. The new `--require-base` option fails the run in that case instead. The `check-documentation` action fetches the base branch on a pull request with enough history for the fork point and passes `--require-base`, unless its `base` input is set.

## 1.0.0

### Major Changes

- 024b6cf: The citation check reads `.fs` files under `--code` and `--references` like the other code files, so an F# file that cites a spec ID that does not exist or is superseded, or a deviation ID missing from `deviations/`, now fails the check.

## 0.6.0

### Minor Changes

- beac389: A build manifest that lists the same path twice now fails with `<path> is listed twice`, whether or not the two items give the same format, size and hash. A manifest path written as an unquoted number such as `0` is read as that text instead of failing with `every file has a path`, and a path written as a map or list, in a manifest or a list of other files, fails with `a path is text, not a map or list`.

## 0.5.1

### Patch Changes

- c587d28: A superseded format entry no longer needs a layout table. An `unknown` format entry that only listed a file can be retired by naming its replacement in `superseded_by`, without adding a table. A superseded format entry that had a layout table at the base still fails when the table is removed.

## 0.5.0

### Minor Changes

- 099ff81: `--record-validation` writes HEAD as the record's Commit only when the working tree matches HEAD. When any tracked file differs from HEAD, or an untracked file is not ignored, it lists the paths and exits with 2 without writing `VALIDATION.md`, since HEAD is then not the commit the run tested. An earlier `VALIDATION.md` may differ. Commit the change, run the marked tests against that commit, record, and commit `VALIDATION.md` after it.

## 0.4.2

### Patch Changes

- a3eac9a: On Windows the checker now finds the Kaitai Struct compiler's `.bat` launcher on `PATH`. It looks for `kaitai-struct-compiler` and `ksc` with each `PATHEXT` extension, skips the extensionless Unix script that the official release puts beside the launcher, and runs the launcher by its full path. Before, it ran the bare name through `cmd.exe`, which started the launcher with `%~dp0` set to the working directory, so the launcher could not find its jars and the checker reported no compiler. A `KSC` holding a bare name is looked up on `PATH` the same way. A launcher found on `PATH` whose `--version` fails now gets a warning naming it, with its output, instead of reading as a missing compiler.

## 0.4.1

### Patch Changes

- 67332cf: An experiment whose fixture gives no save hash now gets a problem that says where the hash goes (`starting_state.xxh3`). With `starting_state: null`, the problem also says that null names a save kept with the captures, and that an experiment starting without a save has `starting_state` new-game or emulated-call. Which experiments need the hash is unchanged. A fixture's `starting_state.xxh3` and `starting_state.base_xxh3` must now be 32 lower-case hex digits, as a build's file hashes already are.

## 0.4.0

### Minor Changes

- 25c3921: A run that did not compile the Kaitai definitions in `spec/formats/` now says so in its result line: it prints `spec check passed with skipped steps:`, the counts, and `Skipped: Kaitai compilation of N definitions` with the reason (`--no-ksy`, or no compiler found). The separate warning about a missing compiler is gone, and a failing run lists the skipped steps after its problems. The new `--require-ksc` option fails the run when there are definitions to compile and no compiler is found; it cannot be combined with `--no-ksy`. With `--no-ksy`, a `.ksy` file that belongs to no format entry is now reported, as it is without the option. The `check-documentation` action passes `--require-ksc` when it installs a compiler.

## 0.3.0

### Minor Changes

- 617f243: A field name after a dot in a procedure is now checked against the layout of its structure's format, wherever the structure's type is written in the notation's type form: a `let` with a type or `new`, the rule's Parameters section, a `define` signature, the Outputs of a called rule, the glossary entry, or the layout row of the field before it. A spec that names a field its format's layout lacks now fails the check. A name whose type is only described in prose stays unchecked. The documentation standard check guide lists the forms the checker reads.

  A parameter written `` `name: type` `` in the Parameters section now counts as a local, and a `#` inside a string in a procedure no longer starts a comment that hides the rest of the line and the lines up to the next quote.

## 0.2.0

### Minor Changes

- 86a854a: A problem that breaks a numbered rule of the documentation standard now ends with the rule's label in brackets, such as `[STATUS-4]`, and the summary says once what the labels mean. The labels cover the rules numbered so far: Identifiers, Status and the shared part of Entry types.

## 0.1.0

### Minor Changes

- baedab9: First release: the documentation standard check, extracted from `tools/check-documentation.mjs` of
  the toolkit, as the `standard-checker` command.
