# Documentation standard check

`tools/check-documentation.mjs` checks a restoration's `spec/`, `PARITY.md` and `DEVIATIONS.md`
against version 1 of the
[documentation standard](https://dinorefurb.com/documentation-standard/#checks) and writes the
four indexes in `spec/index/`. It needs Node.js 20 or newer and no packages. It started as
`tools/check-spec.mjs` in the Chaos Overlords restoration.

`actions/check-documentation` runs it in GitHub Actions. The toolkit repository is public, so any
workflow can use the action by path and commit. Nothing needs publishing to the Marketplace.

## Setting up the action

### 1. Lay out the documentation

The check expects these at the root it is given (the repository root by default):

- `spec/README.md` with the sections Scope, Standard version and Areas, in that order;
- `spec/glossary.md` and `spec/LICENSE`;
- the entries in `spec/builds/`, `spec/sources/`, `spec/formats/`, `spec/rules/`,
  `spec/findings/`, `spec/experiments/`, `spec/bugs/` and `spec/screens/`, as far as the
  restoration has any;
- `PARITY.md` and `DEVIATIONS.md`.

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
| `root` | `.` | Workspace-relative path of the directory holding `spec/`, `PARITY.md` and `DEVIATIONS.md`. |
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

### 5. Generate the indexes before the first run

The action runs with `--check`, which fails when an index in `spec/index/` is missing or stale. Run
the script once locally without `--check` and commit what it writes:

```sh
curl -fsSLo check-documentation.mjs \
  https://raw.githubusercontent.com/kibertoad/refurbished-dinosaurs-toolkit/<sha>/tools/check-documentation.mjs
node check-documentation.mjs
git add spec/index
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
indexes with the script from that commit, and fix what it reports in the same pull request.

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
node check-documentation.mjs            # check, then rewrite stale indexes
node check-documentation.mjs --check    # check, and fail on a stale index
node check-documentation.mjs --help     # every option
```

Run it from the restoration's root or pass `--root`. The command-line options match the action's
inputs: `--code`, `--references`, `--data-dirs` and `--base`, plus `--no-ksy` to skip compiling
and `--glossary <file>` to accept the terms of a draft glossary. Set `KSC` to the compiler's
launcher, or put `kaitai-struct-compiler` on `PATH`, to compile the `.ksy` definitions.

## Moving a restoration onto it

A restoration that has its own copy of `check-spec.mjs`:

1. Delete the copy and the workflow step that installs the Kaitai compiler.
2. Add the action as above, with the inputs that reproduce the old directory list.
3. Point local validation scripts at the downloaded script.
4. Regenerate the indexes. The generated header now names the documentation standard check
   instead of `tools/check-spec.mjs`, so all four indexes change once.

The tests in `tests/documentation-standard/` run the script over a small fixture restoration and
over broken copies of it.
