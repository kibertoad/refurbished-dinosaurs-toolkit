# Documentation standard check

`@scientific-method/standard-checker` (source in `packages/standard-checker`) checks a
restoration's `spec/`, `parity/` and `deviations/` against version 1 of the
[documentation standard](https://dinorefurb.com/documentation-standard/#checks) and writes the
four indexes in `spec/index/` and the totals in `PARITY.md`. It needs Node.js 22 or newer and has
no dependencies. It started as `tools/check-spec.mjs` in the Chaos Overlords restoration.

`actions/check-documentation` runs it in GitHub Actions. The toolkit repository is public, so any
workflow can use the action by path and commit. Nothing needs publishing to the Marketplace.

## Setting up the action

### 1. Lay out the documentation

The check expects these at the root it is given (the repository root by default):

- `spec/README.md` with the sections Scope, Standard version and Areas, in that order;
- `spec/LICENSE`, and `spec/glossary/` with one `<term>.md` per term;
- the entries in `spec/builds/`, `spec/sources/`, `spec/formats/`, `spec/rules/`,
  `spec/findings/`, `spec/experiments/`, `spec/bugs/` and `spec/screens/`, as far as the
  restoration has any, with a `<ID>.files.yaml` manifest beside each build entry, a
  `<ID>.other-files.yaml` beside it where the build's list of left-out paths is long, a
  `<ID>.listing.yaml` listing record where the build keeps one, and any CSV value files beside the
  entries that name them;
- `parity/`, with one `<AREA>.md` of parity rows per area, split by kind and then by block of 100
  numbers where an area would pass 1,000 lines;
- `deviations/`, with one `<ID>.md` per deviation;
- `validation/`, once a `validated` row lists a test file marked `needs: GAME_DIR` (see below).

The check writes `PARITY.md`. An empty directory needs a `.gitkeep` so that git keeps it.

`packages/standard-checker/test/valid/` is the smallest layout that passes and can be copied as a
starting point.

### 2. Pick the toolkit commit

Pin the action to a full commit SHA of the toolkit's `main`, never to a branch:

```sh
git ls-remote https://github.com/kibertoad/refurbished-dinosaurs-toolkit refs/heads/main
```

Use the same SHA for every toolkit action a restoration uses, and the checker version that
commit carries for local runs (step 5), so CI and local runs apply the same checks.

### 3. Add the workflow

`.github/workflows/documentation.yml`, or a job in an existing workflow:

```yaml
name: Documentation

on:
  pull_request:
  push:
    branches: [main]

permissions: {}

jobs:
  check:
    name: Documentation standard
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: kibertoad/refurbished-dinosaurs-toolkit/actions/check-documentation@<sha>
```

On a pull request, the check fails when the pull request deletes a spec ID, area or deviation that
exists where it forked from its base branch. The action finds that fork point itself: it fetches
`origin/$GITHUB_BASE_REF` and HEAD with 50 commits of history beyond what the checkout holds, then 500, then all of it, until the
merge-base resolves, and runs the check with `--require-base`, so a fork point that still does not
resolve fails the check. The fetch runs `git fetch origin` without the checkout's credentials when
`persist-credentials` is `false`, which works for a public repository. A private repository either
leaves `persist-credentials` at its default or checks out with `fetch-depth: 0`, which makes the
fetch unnecessary.

On a push there is no base branch. The check compares with where HEAD forked from `origin/main`
when that resolves, and otherwise passes with its result line naming the comparison as skipped.

The job needs only `contents: read`. When `spec/formats/` holds a `.ksy` file, the action installs
the Kaitai Struct compiler on the runner's Java; the GitHub-hosted Linux, Windows and macOS runners
all have one. On a self-hosted runner without Java, set `java-version: "21"`. The compiler download
is cached per version, and its SHA-256 is checked on every run. A restoration with no `.ksy` file
skips the install and needs no Java.

### 4. Choose the inputs

Most restorations need no inputs. Set one when the defaults do not match the repository:

| Input | Default | Meaning |
|---|---|---|
| `root` | `.` | Workspace-relative path of the directory holding `spec/`, `parity/` and `deviations/`. |
| `code` | `src,tests,tools` | Directories whose files may cite spec and deviation IDs and hold `PLACEHOLDER:` comments. |
| `references` | empty | Directories whose files may cite IDs but whose `PLACEHOLDER:` comments do not count against parity. |
| `images` | empty | Half-open address ranges of the original's flat 32-bit images, such as `0x00400000..0x004C9000`. See below. |
| `max-range` | `0x10000` | The largest address range by which an entry records an address. |
| `data-dirs` | from the build entries | Top-level directories of the original's data. A path into one must name a build file with its exact case. |
| `base` | fork point | Ref whose IDs, areas and deviations must still exist. When it is set, the action fetches nothing and does not pass `--require-base`. |
| `kaitai-version` | `0.11` | Compiler release to install, or empty to skip the install. With no `.ksy` file the install is skipped anyway. |
| `java-version` | empty | Java to install with `actions/setup-java` before the compiler. |

The code and reference directories are scanned for `.cs`, `.ts`, `.mjs`, `.js`, `.ps1`, `.fs`,
`.md` and `.json` files, skipping `bin`, `obj`, `dist`, `node_modules`, `.git` and `artifacts`.
Every spec ID cited in those files and in the Markdown files of `parity/` and `deviations/` must exist and not
be superseded, and every deviation ID cited in those files and in `parity/` must be in
`deviations/`. A deviation file may cite the superseded entry it departed from, and the deviation
IDs it cites are not checked.

A passing citation check shows only that each cited ID exists and, outside `deviations/`, is not
superseded. A `BLD-` or `SRC-` alias that names no entry is skipped, because an alias can collide
with an ordinary word. Notes, plans and handovers outside the scanned directories are not read at
all. In the files it reads, the check does not compare the words around a citation with the entry
the citation names, so a note that calls a bitmap entry a configuration entry passes as long as the
ID exists. When prose describes what an entry is, take the description from the entry:
`spec/index/by-kind.md` lists every spec ID with its title and status (builds and sources have no
status and record a replacement in `superseded_by`), `deviations/` holds the deviations, and the
entry itself has its evidence and, for a format, the files it describes. An index that would pass
the line limit is a directory of the same name, such as `spec/index/by-kind/`, split by area, then
by kind. Reviewing that agreement is part of the restoration's own review of its notes and plans;
the checker does not compare prose with titles, because a paraphrase is a legitimate way to cite an
entry.

| `rebuild` | `src,tests` | Directories that hold the rebuild. No Markdown file in `spec/` may name a path in them or a source file found in them. Empty turns the check off. See below. |

### Addresses in code

An address of the original that the code gives, in a comment or in the code itself, must be
recorded in an entry the comment it belongs to cites, or in an entry that one of those cites as
`evidence`. Citing a rule whose finding records the address is enough. Without this, a comment
can cite an existing finding that says nothing about the address it gives, and the citation check
still passes. A superseded entry records nothing.

- **Which comments.** `//` and `/* … */` comments in `.cs`, `.ts`, `.js` and `.mjs` files, and `#`
  and `<# … #>` comments in `.ps1` files, of the code and reference directories. Text inside a
  string literal is not a comment. In PowerShell, `#` starts a comment where a new token may begin:
  at the start of a line, after whitespace, or after one of `; | & ( ) { } , =`. A comment block is
  a run of consecutive comment-only lines. A comment that trails code also takes the comment lines
  above it and the comment lines below it that start in its column, or that continue its
  `/* … */`, and each of those lines is read with the whole of that block.
- **Which code.** An address in the code itself, written as a number or inside a string, belongs
  to the comment that trails its line and to the nearest comment-only line above it, however many
  lines of code or blank lines lie between, and that line's block. Either may cite the entry that
  records it. So one comment above a table of addresses covers every row of the table, a row with
  a comment of its own included, and an address with neither a comment on its line nor one above
  it fails. A number may group its digits with `_` (`0x0040_1000`) and carry a `u`, `U`, `l` or `L`
  suffix or two, or a BigInt `n`. Code cannot write a half-open range,
  so a value that its comment gives as the end of one (`0x00401000..0x00401010`, for a test such
  as `a < 0x00401010`) stands for the byte before it.
- **Which addresses.** A neutral name, `fn_` or `g_` followed by eight hex digits, is always an
  address. A plain `0x` value of eight hex digits is one only inside an image given by `images`,
  so colours, masks and offsets are left alone. Without `images`, only neutral names are checked.
  The end of a half-open range (`..0x…`) stands for the byte before it. Segmented addresses are
  not checked, and neither are other files, such as test data in `.json`, which have no comment to
  cite an entry from.
- **What records an address.** The address written in the entry's `locations` or text, alone or
  inside a range, in either case. A range larger than `max-range` (64 KiB by default), such as a
  whole section, records only its two ends, nothing inside it; otherwise every finding that gives
  the extent of the code section would vouch for any address in the program.

Take the image's base and size from the finding that records them, for example a PE's
`ImageBase` and `SizeOfImage`:

```yaml
      - uses: kibertoad/refurbished-dinosaurs-toolkit/actions/check-documentation@<sha>
        with:
          images: 0x00400000..0x004C9000
```

When a comment fails, cite the finding that records the address. When no finding does, write
one in the same change. A value inside the image that is not an address, such as a colour written
with eight digits, fails as well: write it with fewer digits, or give `images` the image's exact
extent.

### Addresses in commit messages

A commit message that gives an address as evidence is held to the same rule as a comment: the
address must be recorded in an entry the message cites, or in that entry's evidence. The checker
checks one message with `--message <file>`, which reads only the message and the spec and exits
with 0 when every address passes and 1 when one does not. It leaves out everything from the
scissors line of `git commit --verbose` on, which git always cuts. It checks comment lines (those
starting with `#`, or `core.commentChar`) too: git keeps them under `git commit -m` and the
`whitespace` and `verbatim` cleanup modes, and a hook cannot tell which mode the commit uses. An
address that git's own comments name, such as a branch or file name, therefore needs a citation as
well. Run it from a `commit-msg` hook, with the restoration's `--images`:

```sh
#!/bin/sh
# .githooks/commit-msg
exec pnpm exec standard-checker --message "$1" --images 0x00400000..0x004C9000
```

The hook checks against the spec in the working tree. A commit that adds the finding it cites
passes when the finding is in the working tree, staged or not.

### The spec does not name the rebuild

The standard says the spec never names a class, file or setting of the rebuild. The checker fails
each line of a Markdown file in `spec/`, other than the generated indexes, that names a path in one
of the `rebuild` directories, such as `tests/Score.Tests/ScoreTests.cs` or `../../src/Score.cs`, or a
source file (`.cs`, `.fs`, `.ts`, `.mjs`, `.js`, `.ps1`) found in one of them by its file name alone.
The search for those names skips `bin`, `obj`, `dist`, `node_modules`, `.git` and `artifacts`, so a
file name that only build output has, such as a bundler's `index.js`, stays free to use.
A path counts when it exists, or when it is written with forward slashes and has a file extension
or goes more than one level down. So prose such as "tests/experiments" does not count, and neither
does a path of the original's own sources quoted as evidence with backslashes, such as
`src\game\score.cpp` from an assert string. Describe the comparison in the entry without the file, and list
the test in the parity row, which is where the rebuild points at the spec.

Tools that read the original, such as a research script in `tools/` or a probe that runs the
original, are not part of the rebuild, and a finding's How to reproduce section may name them, so
`tools` is not in the default. A restoration whose rebuild has more directories adds them:

```yaml
      - uses: kibertoad/refurbished-dinosaurs-toolkit/actions/check-documentation@<sha>
        with:
          rebuild: src,tests,multiplayer
```

A class or setting named without its file is not caught; that is left to review.

When the action installs the compiler, it runs the check with `--require-ksc`, so a compiler that
cannot be found fails the check. With `kaitai-version: ""` and no compiler on the runner, the
`.ksy` definitions are not compiled and the check passes with its result line naming the skipped
Kaitai compilation, so leave the install on unless the restoration has no binary formats yet.

For example, Chaos Overlords keeps a TypeScript multiplayer workspace whose placeholders are not
parity work:

```yaml
      - uses: kibertoad/refurbished-dinosaurs-toolkit/actions/check-documentation@<sha>
        with:
          references: multiplayer
```

### 5. Generate the indexes and PARITY.md before the first run

The action runs with `--check`, which fails when an index in `spec/index/` or `PARITY.md` is
missing or stale, or when `spec/index/` holds a file the check would not write. Run the checker
once locally without `--check` and commit what it writes:

```sh
npx @scientific-method/standard-checker@<version>
git add spec/index PARITY.md
```

Use the version the action's toolkit commit carries in `packages/standard-checker/package.json`,
so the local run and CI agree.

### Updating the generated files on the main branch only

When several pull requests are open at once, most of them change the indexes and `PARITY.md`, and
those files conflict between them on almost every merge. A restoration can instead update them on
the main branch only, by running the checker there without `--check` and committing what it
writes, by hand or from a scheduled job. Branches then never change them, and the main branch's
copies are as old as the last such run.

Set the action's `scheduled-generation` input to `"true"`, and pass `--scheduled-generation` to
local runs, such as a pre-commit hook. The check then neither compares the generated files with the
spec nor writes them, and names that comparison as skipped. It fails when the change since the
fork point edits, adds or removes one of them; restore such a file as it is on the base branch. A
file that matches its copy at the base branch's tip is not the branch's change, so a branch that
takes the main branch's newer copies, by a merge, a squash merge or a cherry-pick, passes.

A change that only regenerates them passes too: it touches no other file, and leaves each one as
the checker without `--check` writes it. That is the commit the main branch takes, so the
scheduled job can push it to the main branch or open a pull request with it, and the required
check passes either way. Regenerating them beside any other change fails.

Move a legacy `PARITY.md` that holds rows into `parity/` (see
[Moving to the directory layout](#moving-to-the-directory-layout)) before turning this on. The
move changes `PARITY.md` together with the files under `parity/`, so it fails once the input is
set.

```yaml
      - uses: kibertoad/refurbished-dinosaurs-toolkit/actions/check-documentation@<sha>
        with:
          scheduled-generation: "true"
```

### Squashing superseded entries

The standard keeps a superseded entry in place, so the checker fails a change that deletes one.
Before a restoration's spec is relied on outside the project, it may squash its superseded
entries instead: delete each one, keep its replacements with the final content, and move every
citation of the old ID to them. A squashed ID is never used again.

List each squashed entry with its replacements in the action's `squashed` input and in
`--squashed` for local runs: `FND-AI-008=FND-AI-064,FND-AI-025=FND-AI-069`, or
`FND-X-001=FND-X-002+FND-X-003` for an entry split in two. The check accepts a listed deletion
when the entry's `superseded_by` at the base names exactly those replacements and each of them
exists and is not superseded. A chain is squashed by listing each link: with `A` superseded by `B`
and `B` by `C`, `A=B,B=C` deletes both, and a citation of `A` is pointed at `C`. It fails for a
listed entry that still exists, and for a squashed ID still cited in the spec, the glossary, the
code, the `references` directories, `parity/` or `deviations/`, including a build or source alias
in the code. The indexes and `PARITY.md` are not searched for citations: with
`scheduled-generation` they may name a squashed ID until the main branch regenerates them, and
without it the check rewrites them.

Keep the input after the squash reaches the main branch: a listed ID that exists again fails, so
the input is what stops a squashed ID from being used again. A listed ID the base does not have is
named as a skipped step. Keep the list for local runs where the reference check does not read it,
such as a text file a wrapper script reads: a `.js`, `.mjs`, `.ts`, `.cs` or `.ps1` file under a
checked directory that spells the list out cites every squashed ID in it and fails.

```yaml
      - uses: kibertoad/refurbished-dinosaurs-toolkit/actions/check-documentation@<sha>
        with:
          squashed: FND-AI-008=FND-AI-064,FND-AI-025=FND-AI-069
```

### 6. Make it required

To block merging on a failing check, add the job to the branch protection rule or ruleset for
`main` as a required status check. Its name there is the job's `name`, `Documentation standard` in
the workflow above.

If the restoration limits which actions may run (Settings > Actions > General > Actions
permissions), allow `kibertoad/refurbished-dinosaurs-toolkit/*` there. The default setting allows
all actions.

### Updating

To pick up new checks, replace the SHA in the workflow with a newer toolkit commit, regenerate the
indexes and `PARITY.md` with the checker version from that commit, and fix what it reports in the same pull
request.

A build entry written before the Code ranges section existed fails with a missing section. Add
`## Code ranges` after Other files, with `None.` where no finding locates code by offset, or with
one row per code range where one does.

Before the check on addresses in code comments, a comment could give an address its citation does
not record. On upgrading, a comment that names an address `fn_…` or `g_…` without citing an entry
that records it fails. Set `images` to check plain `0x` addresses as well, and fix each failure
by citing the finding that records the address, or by writing one. The migration guide describes
what changed when the check started reading PowerShell comments and the code itself, and when the
spec's paths into the rebuild started to fail.

## Measuring coverage

The same package installs `standard-coverage`, which reads the function inventories in `coverage/`
and the entries' `locations`, and prints for each analysed file the share of its functions and of
their bytes that some entry cites, followed by the functions that no entry cites and that are not
out of scope. `--require-complete` fails while any are left, which is the Audit stage's condition on
functions. [The package README](../packages/standard-checker/README.md#coverage) gives the inventory
format and what counts as citing a function. The figures change with every batch, so print them on
demand rather than committing them.

## Using setup-kaitai on its own

`actions/setup-kaitai` installs the compiler without running the check, for a workflow that
compiles `.ksy` definitions for other reasons:

```yaml
      - uses: kibertoad/refurbished-dinosaurs-toolkit/actions/setup-kaitai@<sha>
      - shell: bash
        run: '"$KSC" --target python --outdir out spec/formats/*.ksy'
```

It exports `KSC` and sets the `path` output to the compiler's launcher. It refuses any version
whose SHA-256 is not pinned in `actions/setup-kaitai/install.sh`; to support a new release, add
its hash there.

## Running the checker locally

Add it as a development dependency (`pnpm add -D @scientific-method/standard-checker`), then:

```sh
pnpm exec standard-checker            # check, then rewrite stale indexes and PARITY.md
pnpm exec standard-checker --check    # check, and fail on a stale index or PARITY.md
pnpm exec standard-checker --help     # every option
```

Run it from the restoration's root or pass `--root`. The command-line options match the action's
inputs: `--code`, `--references`, `--images`, `--max-range`, `--data-dirs`, `--rebuild`, `--base`,
`--scheduled-generation` and `--squashed`, plus `--no-ksy` to skip compiling
and `--glossary <path>` to accept the terms of a draft term file or a directory of them. Set `KSC` to the compiler's
launcher, or put `kaitai-struct-compiler` on `PATH`, to compile the `.ksy` definitions.

A run that compiled every definition, or had none to compile, ends with `spec check passed:` and
the counts. A run that did not compile them, because of `--no-ksy` or because it found no
compiler, ends with `spec check passed with skipped steps:`, the counts, and a `Skipped:` part
naming the Kaitai compilation and the reason; both exit with 0. Pass `--require-ksc` where the
compilation must run, such as in CI: a missing compiler then fails the check with exit code 1.
`--require-ksc` cannot be combined with `--no-ksy`.

Without `--base`, the check compares with where HEAD forked from `origin/$GITHUB_BASE_REF`, or
`origin/main` when that variable is unset. When that fork point does not resolve, because the
directory is not in a git repository, the clone is shallow, or the branch was never fetched, the
result line names the skipped step, such as `Skipped: comparison with the base branch (HEAD has no
merge-base with origin/main, fetch it with enough history or pass --base).` Without git on `PATH`
the skipped step names the missing git instead. Pass `--require-base` where the comparison must
run: either case then fails the check with exit code 1.

Each problem is one line that starts with the path it concerns, or `spec` for a problem with the
spec as a whole, such as a deleted ID. When the problem breaks a numbered rule of the standard, the
line ends with the rule's label, such as `[STATUS-4]`. The standard opens each rule with a
sixth-level heading of that label, anchored at `#status-4`, and a restoration's vendored copy keeps
the same anchors, so an agent can read that rule alone: its upstream link tooling gives the heading
a line range like any other. Only Identifiers, Status and Entry types up to the end of Builds
are numbered so far, and problems under other sections have no label yet.

## Recording a validation run

A listed test runs in CI unless it needs content that cannot be committed: the shipped files a
format test decodes, captures of the original's screens, or a base save. A test file that reads
such files through `GAME_DIR` carries the comment `needs: GAME_DIR`, and CI, which never has them,
skips it. The check fails a listed test file that mentions `GAME_DIR` without the comment, since CI
would skip it while its row claims to be validated.

A marked test file of a `validated` row runs on a maintainer's machine. After a run in which every
test in those files passed and none was skipped, record it:

```sh
pnpm exec standard-checker --record-validation BLD-GOG-EN-1.1
```

This writes a run file, `validation/<date>-<commit>.md`, where the date is the day of the run and
the commit is the first 12 hex digits of HEAD. It holds the full commit, the date, the builds the
run used, and the SHA-256 of every marked test file a validated row lists, hashed with CRLF read as
LF. Recording then deletes every other run file, since the new run records every marked test file as
it is now. It keeps a run file that lists a test file git tracks but the checkout does not hold, as
a sparse checkout leaves its skip-worktree files absent, because that run may still be the one that
validates the file in a full checkout. Runs recorded on other branches are not in the tree, so they
come back when those branches merge.

The standard defines the commit as the commit the run tested, and the check writes HEAD there, so
the run has to test HEAD as committed. The check refuses to record, and exits with 2, when the
working tree differs from HEAD in anything outside `validation/`, including untracked files that git
does not ignore. A run file cannot name the commit that contains it, so a change to a validated
row's marked tests or the code they exercise goes in two commits on the same branch: first the
change, then, after the run against that commit, the new run file.

A run file is never edited after it is written. Two branches that each record a run add two files
with different names, and when they merge, each run keeps counting for the test files it still
matches. A marked test file of a validated row passes while any run file records the hash it has
now. The check, in CI as well, fails a validated row whose marked test file no run records, or no
run records as it is now; a run file none of whose files has the hash it recorded, unless it lists
a tracked file the checkout does not hold; a run file whose
name does not match its Date and Commit; any other file in `validation/`; and a `VALIDATION.md` at
the root. A run file can also come to match nothing without a new run: after a merge of two
branches that between them changed every file it matched, or when the rows whose files it matched
stop being `validated`. Delete such a file by hand; that needs no run against the original's files.
A restoration whose validated rows list no marked file needs no run file. The run files
hold hashes of the marked test files only, so the check fails on the first commit when a marked
test file changed, and passes it when only the code they exercise did. The checker cannot tell
whether the tests passed; running them before recording is the maintainer's part.

## Moving a restoration onto it

A restoration that has its own copy of `check-spec.mjs`:

1. Delete the copy and the workflow step that installs the Kaitai compiler.
2. Add the action as above, with the inputs that reproduce the old directory list.
3. Add `@scientific-method/standard-checker` as a development dependency and point local
   validation scripts at `standard-checker`.
4. Regenerate the indexes. The generated header now names the documentation standard check
   instead of `tools/check-spec.mjs`, so all four indexes change once.

## Moving to the directory layout

The standard's 1,000-line limit replaced three shared files with directories. A restoration still
on the single files gets one problem per file saying where it moves:

1. Move each `##` term of `spec/glossary.md` to `spec/glossary/<term>.md`, with the term as the
   file's `#` heading and no front matter.
2. Move each `##` deviation of `DEVIATIONS.md` to `deviations/<ID>.md` the same way.
3. Move each area's parity rows out of `PARITY.md` to `parity/<AREA>.md`, which opens with
   `# <AREA>` and holds one table. The check tells you where an area has to be split further.
4. Move each build entry's `files` list to `builds/<ID>.files.yaml` and put
   `manifest: <ID>.files.yaml` in the entry.
5. Run the checker without `--check` to write `PARITY.md` and the indexes, which now have a path
   heading, one row per entry in `references.md`, and are split where they would pass the limit.

No ID changes, so code and tests that cite IDs stay as they are.

## Field names in procedures

The checker reports a field name after a dot that is not a Name in the layout of the structure's
format, such as `probe.made_up_field` after `let probe = new FMT-DATA-005`. It checks a field only
where it knows the structure's type, and it knows a type only where the type is written in the
notation's form: a format ID (`FMT-DATA-005`), a pointer to one (`PTR32<FMT-DATA-005>`) or a list of
either (`FMT-DATA-005[]`, `FMT-DATA-005[count]`). It reads the type from:

- a `let` with a type (`let gang: FMT-DATA-005 = ...`), `let gang = new FMT-DATA-005`, or a `let`
  whose value has a known type: another name or field, `copy` of one, a function whose `define`
  gives its result type (`-> FMT-DATA-005`), or `call` of a rule whose Outputs section opens with
  `Returns` and the type (`Returns a FMT-DATA-005, the gang it made.`);
- the rule's Parameters section, written `` `gang`: FMT-DATA-005 `` or `` `gang: FMT-DATA-005` ``,
  and the typed parameters of a `define`;
- for a name that is not a local, its glossary entry, written `` `gang: FMT-DATA-005` ``, or a
  location of the form ``kept in the field `players` of FMT-SAVE-001`` whose layout row has a type;
- the list a `for each` loop visits, whose element type the loop variable takes.

A field takes the type in its layout row, so `world.occupancy.cell_count` is checked against the
layout of `occupancy`'s type in turn. A field that the entries of a format split by build give
different types has no type. An index into a list gives its element, and an index on a
pointer gives the structure it points at. A format named anywhere else in prose, such as
"the entry whose state is FMT-DATA-003", gives no type, because prose names formats for many reasons.
A name whose type comes from none of these, or that two declarations give different types, is not
checked, and neither is a format whose layout table has the wrong columns or a row with the wrong
number of cells. A declaration that writes no type, such as a parameter the Parameters section
describes only in prose, leaves the type another declaration writes in place. A format whose Layout
section has no table yet has no fields, so every field named on it is reported. The problem names
where the type came from, so a type stated wrongly can be fixed where it is written.

## Argument counts

The checker counts the arguments of every `call`, function call and `emit` in a live rule's
procedure:

- `call RULE-COMBAT-012(attacker, defender)` passes one argument for each parameter of the called
  rule's Parameters section. When the called rule is split by build, the count is compared with
  each entry of the split that lists one of the calling rule's builds.
- `roll(100)` passes one argument for each parameter of the `define` of `roll`, for a function a
  rule defines. A function that a split rule defines is compared with the `define` of each entry
  that lists one of the calling rule's builds. When no entry lists one, the call fails only if it
  fits none of the `define`s. Built-in functions such as `sprintf` have no `define` and are not
  counted, and neither is a call to a name the procedure declares itself, as a `let`, a loop
  variable, a parameter of its `define`s or an item of its Parameters section.
- `emit GangDetected(gang)` passes one argument for each parameter of the Parameters section of
  every rule the event's glossary entry names as a handler. Every rule ID in the glossary entry
  counts as a handler except the event's emitter: a rule whose own procedure emits the event and
  whose When it runs section does not name it. So a glossary entry can say which rule emits the
  event (`RULE-COMBAT-004 emits it.`) without the emit being compared with that rule's Parameters
  section. A handler that emits its event again names the event in When it runs, which gives what
  triggers the rule, and is still counted. A split handler is compared with each entry of the split
  that lists one of the emitting rule's builds. An entry of the emitter's split counts as a handler
  only when its own When it runs section names the event.
- Two `emit`s of one event in rules that share a build pass the same number of arguments. An
  event with no handlers gets only this check, since its glossary entry gives what it carries in
  prose.

A `call` or `emit` with no parentheses passes no arguments. Commas inside nested parentheses,
brackets or braces do not separate arguments, so `call RULE-COMBAT-012(max(a, b), c)` passes two.
A call or `emit` whose argument list is never closed cannot be counted, and is named in the result
line as a skipped step.

The checker counts a Parameters section only in the form the standard gives it: `None.` for a rule
that takes no parameters, or a list with one item per parameter, each opening with a code span that
holds the name, or the name and type, followed directly by a colon:

```markdown
## Parameters

- `attacker: FMT-DATA-005`: the gang that attacks.
- `defender: FMT-DATA-005`: the gang it attacks.
```

An item may continue on indented lines. An item that names two parameters, such as
``- `x`, `y`: the cell``, or puts anything between the code span and the colon, makes the whole list
one the checker does not count, since counting its items would give the wrong number. Such a list,
and a section that holds anything besides the list, prose or `None known.` included, still passes,
but its parameters cannot be counted. Each call and `emit`
counted against such a section is named in the result line instead, grouped by the rule whose
section it is, with the first thing in the section that stops the count, such as
``Skipped: argument counts against RULE-AI-002, whose Parameters section is neither `None.` nor a
list with one item per parameter: item 2 names more than one parameter (call in RULE-AI-001 (2
times), emit of GangDetected in RULE-AI-007).`` The reason is one of: the section is empty, it holds
text and no list (a sentence that names the parameter, or `None known.`), it holds text before the
list, text after item N is neither a list item nor indented under it, or item N names more than
one parameter, does not open with a code span holding the parameter's name, does not follow its
code span directly with a colon, or has a code span holding neither a name nor a name, a colon and
a type. Items are numbered from 1 in the order the section lists them. A rule with no Parameters
section at all is named the same way, as one `which has no Parameters section`. Converting that
rule's Parameters section to the list form puts those calls under the check. Function calls are
always counted, since a `define` already writes its parameters in a fixed form.

The checker does not compare an argument's type with the parameter's type.

## What it does not check

A few checks in the standard's list need something the checker does not have, and are left to
review:

- that a glossary entry gives what the standard asks of its kind of term, and that no procedure
  assigns to a value from outside the game, since the glossary does not mark kinds in a form a
  checker can read;
- that a neutral name is the one from the entry's first build;
- that no list of fixed length is given to `append`, `insert` or `remove_at`;
- a field name on a structure whose type is not written in one of the forms
  [above](#field-names-in-procedures);
- that a Kaitai definition's fixed sizes match its layout table;
- the fixture schema beyond a run's `draws`, the field paths of fixtures and save patches, and
  the hashes of saves and recordings, since the schemas are not published yet and xxHash3 needs a
  package. Each draw is checked to be `{ rule, bound, result }` with integer bound and result,
  naming a rule entry that a live experiment's fixture may not name once it is superseded;
- the spec package version, since the package does not exist yet.

The tests in `packages/standard-checker/test/` run the checker over a small fixture restoration and
over broken copies of it.

The checker's entry point is `packages/standard-checker/src/standard-checker.ts`. It reads the
options and runs the phases in `src/load/`, `src/checks/` and `src/generate/` in a fixed order, so
problems are always reported in the same order. The standard's kinds, sections, fields and limits
are in `src/standard.ts`.

## Version 1 evidence locations and historical rules

A superseded rule keeps its Procedure text for history. Its declarations do
not own active function, table or clock names; live rules still cannot define
the same name outside a declared split group. References to superseded entries
continue to fail where the Standard requires a living citation.

A superseded format entry stays as it was when it was replaced, so it needs neither a Kaitai
definition nor a layout table. An `unknown` entry that only listed a file can be retired by setting
`status: superseded` and naming its replacement in `superseded_by`, with its Layout section still
saying `None known.`. A format entry at any status other than `unknown` or `superseded` needs a
layout table. Because the entry stays as it was, one that had a layout table at the base (`--base`,
or where the branch forked) fails when it has none now.

### Locations by file format

Each location in a finding names a shipped file and gives either an `address` or an `offset`.
The file's `format` in the build manifest decides which is allowed. These are the only formats
with a rule:

| Format | `address` notation | `offset` |
|---|---|---|
| `MZ` | `SSSS:OOOO` | overlay code only |
| `COM` | `SSSS:OOOO` | no |
| `NE` | `SSSS:OOOO` | no |
| `PE` | `0xXXXXXXXX` | no |
| `LE` | `0xXXXXXXXX` | no |
| `LX` | `0xXXXXXXXX` | no |
| `ELF` | `0xXXXXXXXX` or `0xXXXXXXXXXXXXXXXX` | no |
| `data` | no | yes |
| `cdda` | no | yes |

When the file is packed, its unpacked format decides both whether an address or an offset is
allowed and the address notation. An offset is `0x` followed by at least two upper-case hex
digits, naming a single byte of the shipped file, or a half-open range of two, as the Standard's
Notation section writes ranges: `0x0200..0x0400` covers `0x0200` up to but not including
`0x0400`. The checker validates its syntax, ordering and bounds against the shipped file's size,
so a range may end exactly at the end of the file, and fails an empty range.

MZ executables accept offsets because overlay code sits outside the load image and has no fixed
address. The other executable formats map their code through the loader, so their code always
has an address and an offset into them fails.

A location in an executable may also give `kind: code` (the default) or `kind: file-data`. A
`file-data` location names executable headers, container tables, a packer's header or other
shipped bytes that are read as data, so it gives an `offset` into the shipped file in any
executable format, never an `address`, and needs no Code ranges row. Bytes that exist only once
a packed file is unpacked and are not in its load image, such as the relocation table an
unpacker writes, are located with `kind: file-data`, `unpacked: true` and an offset into the
unpacked form, which the checker bounds by that form's `size` in the manifest. Other bytes that
exist only once the file is unpacked are addressed in the unpacked file like code; the checker
cannot tell whether a shipped-file offset points into compressed data, or whether a `file-data`
location in fact names code, so review catches both. The checker fails any other kind, an
address on a `file-data` location, `unpacked` with any value but `true`, on a location that is
not `file-data` or in a file that is not packed, and any `kind` on a location in a `data` or
`cdda` file, which holds no code to tell apart.

### Code ranges

An offset into overlay code locates code only if that part of the file holds code, and the
file's length does not say which parts do. Every build entry has a Code ranges section after
Other files: a table `File | Range | Overlay | Finding`, or `None.` for a build whose code is all
located by address. Each row gives a file of the manifest, one half-open `offset` range of it,
the overlay or bank number needed to read that range or `-`, and the ID of the finding that
shows the range holds code. The checker fails a row whose file is not in the manifest or is not
MZ (after unpacking; no other format holds code located by offset), whose range is malformed,
empty or outside the file, whose overlay is neither a number nor `-`, or whose finding does not
exist, does not list the build or is superseded. It also fails a row whose finding has no
location in that file of that build other than a `file-data` one, which includes a finding with
no locations at all: such a finding shows no code in the row's range.

An `offset` into an executable (an MZ file, or a packed file whose unpacked form is MZ) must lie
wholly inside one row for that file. Adjacent rows are not joined, so a range that crosses from
one bank into the next fails, and so does one that crosses a hole or the end of a code payload.
Lying inside a row does not show that an instruction starts at the offset; the finding still
has to. An offset into a `data` or `cdda` file needs no row.

### Other files

A build's Other files section accounts for every path of the installation's listing that the
manifest leaves out (ENTRY-TYPES-14). Where that list would take the entry past the line limit, it
goes in `builds/<ID>.other-files.yaml`, whose only key `other_files` is a list of maps of `path`
and `reason`, and the section names the file. The checker fails a list that the section does not
name, a section that names a list that does not exist, a list that belongs to no build, an item
without a path or a reason, a path listed twice, and a path that is also in the manifest. It
also fails a manifest that lists a path twice, whether or not the two items agree. In both files a
path is text: an unquoted name such as `1990` or `0` is read as that text, and a path written as a
map or list fails. A path in the list that ends in `/` is a directory exclusion (ENTRY-TYPES-15),
and the checker fails a manifest path that lies under one. The checker reads no paths from an
Other files section written as prose.

### Listing records

A build may keep its listing in `builds/<ID>.listing.yaml` and name it in the entry's `listing`
field (ENTRY-TYPES-16 and ENTRY-TYPES-17). The checker fails a `listing` field that names any other
file or a file that does not exist, a listing record that no `listing` field names, and one that
belongs to no build entry. It checks the record's fields: `tool`, `date` as `YYYY-MM-DD`, `links`
as `listed`, `followed` or `refused`, `cycles` given for followed links and `null` otherwise,
`media` with one prefix per medium (`""` for the installation directory with a null source and
layout, `CD:` or `CDn:` for a disc with one of the layouts `2048`, `MODE1/2352` and `MODE2/2352`),
and `archives` with a path and a depth from 1 for each archive that is a file item of the record.
Each item has a `path` and exactly one of `size`, `link` and `stopped`. The checker fails a path
listed twice, items out of order when paths are compared byte by byte, an item on a medium that
`media` does not name, and an archive member (`archive|member`) whose outermost archive `archives`
does not list or that lies deeper than that archive's depth.

`BuildListing.Make` in the `RefurbishedDinosaurs.LegacyFormats` NuGet package writes such a record
from an installation directory and `.iso` or cue/bin disc images, with disc paths in the form
ENTRY-TYPES-11 gives. Its README's
[Listing a build](../packages/dotnet/README.md#listing-a-build) says what it lists and what it
refuses.

It then compares the record with the manifest and the list of other files, as ENTRY-TYPES-18 says,
and fails, naming the path:

- a file item that is in neither the manifest nor the list of other files and lies under no
  directory exclusion (archive members are exempt);
- a file item whose size differs from the manifest's;
- an archive member whose container is not a file item of the record;
- a manifest path that is not a file item, or that the record gives as a link or stopped path;
- a path in the list of other files, other than a directory exclusion, that is not an item;
- a link or stopped item that the list of other files does not give by its own path. A directory
  exclusion above it does not count;
- a disc's `source` image that neither the manifest nor the list of other files gives by its own
  path (ENTRY-TYPES-16). A directory exclusion above it does not count.

A manifest or other-files path on a disc the record's `media` leave out need not be in the record,
and neither need a CD audio track (`CD:track02`) on a disc where the record lists no track. The
Other files section says why, and that is left to review.

Agreement shows only that the record, the manifest and the list of other files name the same paths
with the same sizes. It does not show that the game uses a file in the manifest, that an archive's
members were surveyed, that the Survey is complete, or that the listing itself missed nothing: a
listing that skipped a directory agrees with a manifest that skipped it too. The record holds no
hashes, so a changed file of the same size passes. The checker does not read the files the record
describes.

Where the build's list of other files is written in the Other files section as prose, the checker
still compares the record with the manifest, and names the rest of the comparison as a skipped
step, such as `comparison of BLD-X.listing.yaml with the list of other files of BLD-X (the checker
reads that list only from BLD-X.other-files.yaml)`. Move the list to `<ID>.other-files.yaml` to
have it compared. Where `<ID>.other-files.yaml` exists but could not be read, its problems are
reported and the skipped step gives that reason instead: `(BLD-X.other-files.yaml could not be
read)`.

A file in any other format fails the manifest check, and so does a packed file whose unpacked
form is in any other format. Before such a file is documented, the Standard must decide how
locations in that format are given and record that decision. Only then is the format added to
the `LOCATIONS` table in `packages/standard-checker/src/standard.ts` and to this table. These rules
implement Standard v1.
