# Documentation standard check

`tools/check-documentation.mjs` checks a restoration's `spec/`, `parity/` and `deviations/`
against version 1 of the
[documentation standard](https://dinorefurb.com/documentation-standard/#checks) and writes the
four indexes in `spec/index/` and the totals in `PARITY.md`. It needs Node.js 20 or newer and no
packages. It started as `tools/check-spec.mjs` in the Chaos Overlords restoration.

`actions/check-documentation` runs it in GitHub Actions. The toolkit repository is public, so any
workflow can use the action by path and commit. Nothing needs publishing to the Marketplace.

## Setting up the action

### 1. Lay out the documentation

The check expects these at the root it is given (the repository root by default):

- `spec/README.md` with the sections Scope, Standard version and Areas, in that order;
- `spec/LICENSE`, and `spec/glossary/` with one `<term>.md` per term;
- the entries in `spec/builds/`, `spec/sources/`, `spec/formats/`, `spec/rules/`,
  `spec/findings/`, `spec/experiments/`, `spec/bugs/` and `spec/screens/`, as far as the
  restoration has any, with a `<ID>.files.yaml` manifest beside each build entry and any CSV
  value files beside the entries that name them;
- `parity/`, with one `<AREA>.md` of parity rows per area, split by kind and then by block of 100
  numbers where an area would pass 1,000 lines;
- `deviations/`, with one `<ID>.md` per deviation;
- `VALIDATION.md`, once any parity row is `validated` (see below).

The check writes `PARITY.md`. An empty directory needs a `.gitkeep` so that git keeps it.

`tests/documentation-standard/valid/` is the smallest layout that passes and can be copied as a
starting point.

### 2. Pick the toolkit commit

Pin the action to a full commit SHA of the toolkit's `main`, never to a branch:

```sh
git ls-remote https://github.com/kibertoad/refurbished-dinosaurs-toolkit refs/heads/main
```

Use the same SHA for every toolkit action a restoration uses, and for the local copy of the
script in step 5, so CI and local runs apply the same checks.

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
| `data-dirs` | from the build entries | Top-level directories of the original's data. A path into one must name a build file with its exact case. |
| `base` | fork point | Ref whose IDs, areas and deviations must still exist. |
| `kaitai-version` | `0.11` | Compiler release to install, or empty to skip the install. With no `.ksy` file the install is skipped anyway. |
| `java-version` | empty | Java to install with `actions/setup-java` before the compiler. |

The code directories are scanned for `.cs`, `.ts`, `.mjs`, `.js`, `.ps1`, `.fs`, `.md` and `.json`
files, skipping `bin`, `obj`, `node_modules`, `.git` and `artifacts`. Every spec or deviation ID
they cite must exist and not be superseded.

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
missing or stale, or when `spec/index/` holds a file the check would not write. Run the script
once locally without `--check` and commit what it writes:

```sh
curl -fsSLo check-documentation.mjs \
  https://raw.githubusercontent.com/kibertoad/refurbished-dinosaurs-toolkit/<sha>/tools/check-documentation.mjs
node check-documentation.mjs
git add spec/index PARITY.md
```

Keep the downloaded script out of the repository (add it to `.gitignore`) or delete it after use.

### 6. Make it required

To block merging on a failing check, add the job to the branch protection rule or ruleset for
`main` as a required status check. Its name there is the job's `name`, `Documentation standard` in
the workflow above.

If the restoration limits which actions may run (Settings > Actions > General > Actions
permissions), allow `kibertoad/refurbished-dinosaurs-toolkit/*` there. The default setting allows
all actions.

### Updating

To pick up new checks, replace the SHA in the workflow with a newer toolkit commit, regenerate the
indexes and `PARITY.md` with the script from that commit, and fix what it reports in the same pull
request.

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

## Running the script locally

```sh
node check-documentation.mjs            # check, then rewrite stale indexes and PARITY.md
node check-documentation.mjs --check    # check, and fail on a stale index or PARITY.md
node check-documentation.mjs --help     # every option
```

Run it from the restoration's root or pass `--root`. The command-line options match the action's
inputs: `--code`, `--references`, `--data-dirs` and `--base`, plus `--no-ksy` to skip compiling
and `--glossary <path>` to accept the terms of a draft term file or a directory of them. Set `KSC` to the compiler's
launcher, or put `kaitai-struct-compiler` on `PATH`, to compile the `.ksy` definitions.

## Recording a validation run

The tests of a `validated` row need the original game's files, which CI never has, so they run on
a maintainer's machine. After a run in which every test in those files passed and none was
skipped, record it:

```sh
node check-documentation.mjs --record-validation BLD-GOG-EN-1.1
```

This writes `VALIDATION.md` at the root: the commit, the date, the builds the run used, and the
SHA-256 of every test file a validated row lists, hashed with CRLF read as LF. Commit it with the
change. From then on the check, in CI as well, fails a validated row whose test file is missing from
the record or has changed since, and a record that lists a file no validated row lists. The script
cannot tell whether the tests passed; running them before recording is the maintainer's part.

## Moving a restoration onto it

A restoration that has its own copy of `check-spec.mjs`:

1. Delete the copy and the workflow step that installs the Kaitai compiler.
2. Add the action as above, with the inputs that reproduce the old directory list.
3. Point local validation scripts at the downloaded script.
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
5. Run the script without `--check` to write `PARITY.md` and the indexes, which now have a path
   heading, one row per entry in `references.md`, and are split where they would pass the limit.

No ID changes, so code and tests that cite IDs stay as they are.

## What it does not check

A few checks in the standard's list need something the script does not have, and are left to
review:

- that a glossary entry gives what the standard asks of its kind of term, and that no procedure
  assigns to a value from outside the game, since the glossary does not mark kinds in a form a
  script can read;
- that a neutral name is the one from the entry's first build;
- that no list of fixed length is given to `append`, `insert` or `remove_at`, and that every field
  a procedure names is in its format's layout;
- that a Kaitai definition's fixed sizes match its layout table;
- the fixture schema, the field paths of fixtures and save patches, and the hashes of saves and
  recordings, since the schemas are not published yet and xxHash3 needs a package;
- the spec package version, since the package does not exist yet.

The tests in `tests/documentation-standard/` run the script over a small fixture restoration and
over broken copies of it.
