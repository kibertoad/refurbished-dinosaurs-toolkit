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
  `<ID>.other-files.yaml` beside it where the build's list of left-out paths is long, and any CSV
  value files beside the entries that name them;
- `parity/`, with one `<AREA>.md` of parity rows per area, split by kind and then by block of 100
  numbers where an area would pass 1,000 lines;
- `deviations/`, with one `<ID>.md` per deviation;
- `VALIDATION.md`, once a `validated` row lists a test file marked `needs: GAME_DIR` (see below).

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
          # Full history, so the check can find where a pull request forked from its base branch
          # and fail when the pull request deletes a spec ID, area or deviation that exists there.
          fetch-depth: 0
      - uses: kibertoad/refurbished-dinosaurs-toolkit/actions/check-documentation@<sha>
```

With the default `fetch-depth: 1` the check still runs, but the deleted-ID comparison is skipped
because `origin/main` is not fetched.

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
| `base` | fork point | Ref whose IDs, areas and deviations must still exist. |
| `kaitai-version` | `0.11` | Compiler release to install, or empty to skip the install. With no `.ksy` file the install is skipped anyway. |
| `java-version` | empty | Java to install with `actions/setup-java` before the compiler. |

The code directories are scanned for `.cs`, `.ts`, `.mjs`, `.js`, `.ps1`, `.fs`, `.md` and `.json`
files, skipping `bin`, `obj`, `node_modules`, `.git` and `artifacts`. Every spec or deviation ID
they cite must exist and not be superseded.

### Addresses in code comments

An address of the original that a comment in the code gives must be recorded in an entry the
comment cites, or in an entry that one of those cites as `evidence`. Citing a rule whose finding
records the address is enough. Without this, a comment can cite an existing finding that says
nothing about the address it gives, and the citation check still passes. A superseded entry
records nothing.

- **Which comments.** `//` and `/* … */` comments in `.cs`, `.ts`, `.js` and `.mjs` files of the
  code and reference directories. Text inside a string literal is not a comment. A comment block
  is a run of consecutive comment-only lines. A comment that trails code also takes the comment
  lines above it and the comment lines below it that start in its column, or that continue its
  `/* … */`, and each of those lines is read with the whole of that block.
- **Which addresses.** A neutral name, `fn_` or `g_` followed by eight hex digits, is always an
  address. A plain `0x` value of eight hex digits is one only inside an image given by `images`,
  so colours, masks and offsets are left alone. Without `images`, only neutral names are checked.
  The end of a half-open range (`..0x…`) stands for the byte before it. Segmented addresses are
  not checked.
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
one in the same change.

With `kaitai-version: ""` and no compiler on the runner, the `.ksy` definitions are not compiled
and the check only prints a warning, so leave the install on unless the restoration has no binary
formats yet.

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
by citing the finding that records the address, or by writing one.

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
inputs: `--code`, `--references`, `--images`, `--max-range`, `--data-dirs` and `--base`, plus `--no-ksy` to skip compiling
and `--glossary <path>` to accept the terms of a draft term file or a directory of them. Set `KSC` to the compiler's
launcher, or put `kaitai-struct-compiler` on `PATH`, to compile the `.ksy` definitions.

Each problem is one line that starts with the file's path. When the problem breaks a numbered
rule of the standard, the line ends with the rule's label, such as `[STATUS-4]`. The standard opens
each rule with a sixth-level heading of that label, anchored at `#status-4`, and a restoration's
vendored copy keeps the same anchors, so an agent can read that rule alone: its upstream link
tooling gives the heading a line range like any other. Only Identifiers, Status and the shared
part of Entry types are numbered so far, and problems under other sections have no label yet.

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

This writes `VALIDATION.md` at the root: the commit, the date, the builds the run used, and the
SHA-256 of every marked test file a validated row lists, hashed with CRLF read as LF. Commit it with
the change. From then on the check, in CI as well, fails a validated row whose marked test file is
missing from the record or has changed since, and a record that lists any other file. A
restoration whose validated rows list no marked file needs no record. The checker cannot tell
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

## What it does not check

A few checks in the standard's list need something the checker does not have, and are left to
review:

- that a glossary entry gives what the standard asks of its kind of term, and that no procedure
  assigns to a value from outside the game, since the glossary does not mark kinds in a form a
  checker can read;
- that a neutral name is the one from the entry's first build;
- that no list of fixed length is given to `append`, `insert` or `remove_at`, and that every field
  a procedure names is in its format's layout;
- that a Kaitai definition's fixed sizes match its layout table;
- the fixture schema beyond a run's `draws`, the field paths of fixtures and save patches, and
  the hashes of saves and recordings, since the schemas are not published yet and xxHash3 needs a
  package. Each draw is checked to be `{ rule, bound, result }` with integer bound and result,
  naming a rule entry that a live experiment's fixture may not name once it is superseded;
- the spec package version, since the package does not exist yet.

The tests in `packages/standard-checker/test/` run the checker over a small fixture restoration and
over broken copies of it.

## Version 1 evidence locations and historical rules

A superseded rule keeps its Procedure text for history. Its declarations do
not own active function, table or clock names; live rules still cannot define
the same name outside a declared split group. References to superseded entries
continue to fail where the Standard requires a living citation.

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
manifest leaves out. Where that list would take the entry past the line limit, it goes in
`builds/<ID>.other-files.yaml`, whose only key `other_files` is a list of maps of `path` and
`reason`, and the section names the file. The checker fails a list that the section does not
name, a section that names a list that does not exist, a list that belongs to no build, an item
without a path or a reason, a path listed twice, and a path that is also in the manifest. It
cannot tell whether the listing itself is complete; the section's account of how it was made is
left to review.

A file in any other format fails the manifest check, and so does a packed file whose unpacked
form is in any other format. Before such a file is documented, the Standard must decide how
locations in that format are given and record that decision. Only then is the format added to
the `LOCATIONS` table in `packages/standard-checker/src/standard-checker.ts` and to this table. These rules
implement Standard v1.
