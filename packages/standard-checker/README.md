# @scientific-method/standard-checker

Checks a game restoration's `spec/`, `parity/` and `deviations/` against version 1 of the
[dinorefurb documentation standard](https://dinorefurb.com/documentation-standard/#checks), and
writes what the standard says is generated: the four indexes in `spec/index/`, the totals in
`PARITY.md`, and on request `VALIDATION.md`. It has no dependencies and needs Node 22 or later.

```sh
pnpm add -D @scientific-method/standard-checker
pnpm exec standard-checker            # check, then rewrite stale indexes and PARITY.md
pnpm exec standard-checker --check    # check, and fail on a stale index or PARITY.md
```

It exits with 0 when the repository passes, 1 when it reports problems (one line per problem,
starting with the file's path), and 2 when the options are invalid or `--record-validation` cannot
write the record. `--record-validation` cannot be combined with `--check`, nor `--require-ksc` with
`--no-ksy`, nor `--message` with either `--check` or `--record-validation`.

A pass ends with `spec check passed:` and the counts of entries, parity rows and deviations. When a
step of the check did not run, it ends with `spec check passed with skipped steps:`, the counts, and
`Skipped:` followed by each step and why, such as
`Skipped: Kaitai compilation of 2 definitions (--no-ksy).` A failing run lists the skipped steps
after its problems. A `call` or `emit` whose arguments cannot be counted, because the Parameters
section it is counted against is missing or is not `None.` or a list of parameters, or because its
argument list is never closed, is a skipped step as well. For a section in another form, the
skipped step names the first thing that stops the count, such as `item 2 names more than one
parameter` or `it holds text and no list`.

Without `--base`, the checker compares the spec with where HEAD forked from `origin/$GITHUB_BASE_REF`,
or from `origin/main` when that variable is unset. Outside a git repository, in a shallow clone, or
when that branch was never fetched, the fork point does not resolve and the comparison does not
run. The result line then names it, such as `Skipped: comparison with the base branch (HEAD has no
merge-base with origin/main, fetch it with enough history or pass --base).` Without git on `PATH`
the skipped step names the missing git instead. With `--require-base`, either case is a problem and
the run fails.

A problem that breaks a numbered rule of the standard ends with the rule's label in brackets, such
as `[STATUS-14]`. The standard opens that rule with the heading `###### STATUS-14`, anchored at
`#status-14` on the site and in the copies restorations vendor, so the rule can be read on its
own. Rules are numbered in Identifiers, Status and the shared part of Entry types so far, and
problems under other sections carry no label yet.

## Options

| Option | Meaning | Default |
|---|---|---|
| `--root <dir>` | The repository to check. | the current directory |
| `--check` | Fail when an index or `PARITY.md` is stale, instead of rewriting it. | rewrite |
| `--scheduled-generation` | For a restoration that updates the indexes and `PARITY.md` on its main branch only, such as from a scheduled job. Neither write nor compare them with the spec, and fail when the change since the base (where HEAD forked from `--base`, or the fork point) edits, adds or removes one, untracked files git does not ignore included. A file that matches its copy at the base branch's tip (`--base`, or `origin/$GITHUB_BASE_REF` or `origin/main`) passes, as does a change that only regenerates them and touches nothing else. The result line names the comparison with the spec as skipped. Without a base the change is not compared either, and the base comparison is named as skipped or, with `--require-base`, fails. | write or compare |
| `--base <ref>` | Also fail when a spec ID, area or deviation that exists at `<ref>` is gone, or when a superseded format entry has no layout table although it had one at `<ref>`. | where HEAD forked from `origin/$GITHUB_BASE_REF` or `origin/main`; when that does not resolve, the comparison is named as skipped |
| `--squashed <list>` | Comma-separated items `OLD=NEW`, or `OLD=NEW+NEW` for an entry replaced by several: superseded entries this change deleted, squashed into the replacements that keep the final content. Without it, a deleted ID fails `IDENTIFIERS-6`. A listed deletion passes when the entry's `superseded_by` at the base names exactly the listed replacements and each replacement exists now and is not superseded, or is listed too, so `A=B,B=C` squashes a chain. An `OLD` that still exists fails, so keeping the option after the squash reaches the main branch stops a squashed ID from being used again. One the base does not have is named as a skipped step. A squashed ID still cited in the spec, the glossary, the `--code` and `--references` directories, `parity/` or `deviations/` fails, a build or source alias included, and the message names the replacements to cite. The indexes and `PARITY.md` are written by the check, not searched for citations, so with `--scheduled-generation` they may name a squashed ID until the main branch regenerates them. Keep the list where the reference check does not read it, such as a workflow file or a text file a wrapper script reads: a `.js`, `.mjs`, `.ts`, `.cs` or `.ps1` file under a checked directory that spells the list out cites every squashed ID in it and fails. | none |
| `--require-base` | Fail when no `--base` is given and the fork point does not resolve, instead of passing with the comparison skipped. | pass with the comparison skipped |
| `--no-ksy` | Skip compiling the Kaitai definitions in `spec/formats/`. The result line names the skipped compilation. | compile |
| `--require-ksc` | Fail when `spec/formats/` holds Kaitai definitions and no compiler is found, instead of passing with the compilation skipped. | pass with the compilation skipped |
| `--glossary <path>` | Also accept the terms of a draft glossary file, or of a directory of them. | none |
| `--code <dirs>` | Comma-separated directories whose files may cite spec and deviation IDs and hold `PLACEHOLDER` comments. | `src,tests,tools` |
| `--references <dirs>` | Comma-separated directories whose files may cite IDs but whose `PLACEHOLDER` comments do not count against parity. | none |
| `--images <ranges>` | Comma-separated half-open address ranges of the original's flat 32-bit images, such as `0x00400000..0x004C9000`. A `0x` value inside one that a code comment gives, or that the code uses under a comment, must be recorded in an entry the comment cites. | none, so only `fn_` and `g_` names are checked |
| `--max-range <bytes>` | The largest address range an entry can record an address by. A larger one, such as a whole section, records only its two ends. | `0x10000` |
| `--data-dirs <dirs>` | Comma-separated top-level directories of the original's data. A path into one must name a file of some build, with its exact case. | the top-level directories of the files the build entries list |
| `--rebuild <dirs>` | Comma-separated directories that hold the rebuild. No Markdown file in `spec/` may name a path in them, or a source file found in them by its file name. An empty value turns the check off. | `src,tests` |
| `--message <file>` | Check only the commit message in the file: every address it gives must be recorded in an entry it cites, as for a code comment. Everything from the scissors line of `git commit --verbose` on is left out; comment lines before it are checked, since git keeps them under `git commit -m`. Exits with 0 or 1, or 2 when the file cannot be read. For a `commit-msg` hook. | not checked |
| `--record-validation <builds>` | Write `VALIDATION.md` for the marked test files of validated parity rows, naming the comma-separated build IDs the run used and HEAD as the commit the run tested. Run it only after every test in those files passed with none skipped, against HEAD as committed: it refuses, with exit code 2, when the working tree differs from HEAD in anything other than `VALIDATION.md`, counting untracked files that git does not ignore. | not written |
| `--help` | Print the options. | |

The `KSC` environment variable names the Kaitai Struct compiler. Without it, the checker looks for
`kaitai-struct-compiler` or `ksc` on `PATH`. When it finds neither, it skips the compilation and
names it in the result line, or with `--require-ksc` reports the missing compiler as a problem.

On Windows the search tries each name with the `PATHEXT` extensions, as `cmd.exe` does, so it finds
the `.bat` launcher of the official release and skips the extensionless Unix script beside it. The
checker then runs the launcher by its full path. A `KSC` that holds a bare name, with no directory,
is looked up on `PATH` the same way.

On every platform, a launcher found on `PATH` whose `--version` fails is not used, and the checker
prints a warning naming its path, with its output.

## Coverage

`standard-coverage` measures how much of each analysed file of code the spec cites, against the
function inventories in `coverage/`, as the work protocol's
[Measuring progress](https://dinorefurb.com/work-protocol/#measuring-progress) section describes.

```sh
pnpm exec standard-coverage                       # per-file shares and the uncited functions
pnpm exec standard-coverage --list                # also every function with the entries citing it
pnpm exec standard-coverage --json                # the same figures as JSON
pnpm exec standard-coverage --require-complete    # fail while an in-scope function is uncited
```

An inventory is `coverage/<build>/<file>.tsv`, with `<file>` the manifest's path and a `CD:` prefix
written as a directory `@CD`. Its columns are `start` and `size`, then optionally `name`,
`out_of_scope` and `ranges`. `start` is in the standard's notation for the file's format: an address,
or for MZ overlay code an offset inside a row of the build's Code ranges. When the body is not one
range from `start`, `ranges` lists its half-open ranges as `start..end` in the same notation,
separated by spaces: `0x00401000..0x00401010 0x00401200..0x00401210`. `size` is then their total,
one of them holds `start`, and they do not overlap. In an `NE` file each range, and a body without
`ranges`, ends in the segment it starts in. The `.provenance.tsv` and `.regions.tsv` files
the work protocol puts beside an inventory are not read.

An entry cites a function when one of its `locations` names the same build and file and its
address, offset or half-open range overlaps the function's body: its `ranges`, or `size` bytes from
`start` when the row gives none. Locations with `kind: file-data`, into the unpacked form of a
packed file, or of superseded entries cite nothing, and neither does an address written in an
entry's body. Real-mode segmented addresses (`MZ`, `COM`) are compared by the linear address they
name, and each `NE` segment is a space of its own. A row without `ranges` is measured as if its body
were contiguous. The shares count functions and bytes of the functions not out of scope.

It exits with 0, with 1 when an inventory is invalid or `--require-complete` finds an uncited
function or no inventory at all, and with 2 when the options are invalid. Problems with the spec
itself are left to `standard-checker`; a warning gives how many there were while loading it and how
many locations in files of code did not parse, since those cite nothing.

`standard-checker` reads the same inventories. Ranges are half-open, so a range whose end is an
inventoried function's last byte (`start` plus `size` minus one, or for a row with `ranges` the last
byte of each range, with ranges that touch taken as one) stops a byte short, the usual slip when a
range is copied from an analyzer that gives last bytes, and the check fails it with the end it
should have. It checks every range a location of a current entry gives in that build and file,
by address or by offset, and the address ranges written in the body of an entry whose locations all
name that one build and file. Without inventories it checks nothing and reports no skipped step.

## In GitHub Actions

The toolkit's `actions/check-documentation` composite action runs this checker with `--check`,
installs a pinned Kaitai compiler when the repository has `.ksy` files (and then passes
`--require-ksc`), and exposes every option
above as an input, apart from `--message` and `--record-validation`. On a pull request with no `base` input, it fetches the base branch with enough
history for the fork point and passes `--require-base`. Pin the action to the toolkit commit whose `packages/standard-checker` matches
the version installed here, so CI and local runs apply the same checks.

[The documentation standard check guide](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/documentation-standard-check.md)
covers setting up the action, every check the tool makes, and moving an older restoration onto it.
