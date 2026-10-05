# @scientific-method/standard-checker

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
