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
`--no-ksy`.

A pass ends with `spec check passed:` and the counts of entries, parity rows and deviations. When a
step of the check did not run, it ends with `spec check passed with skipped steps:`, the counts, and
`Skipped:` followed by each step and why, such as
`Skipped: Kaitai compilation of 2 definitions (--no-ksy).` A failing run lists the skipped steps
after its problems.

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
| `--base <ref>` | Also fail when a spec ID, area or deviation that exists at `<ref>` is gone. | where HEAD forked from `origin/$GITHUB_BASE_REF` or `origin/main`, when that resolves |
| `--no-ksy` | Skip compiling the Kaitai definitions in `spec/formats/`. The result line names the skipped compilation. | compile |
| `--require-ksc` | Fail when `spec/formats/` holds Kaitai definitions and no compiler is found, instead of passing with the compilation skipped. | pass with the compilation skipped |
| `--glossary <path>` | Also accept the terms of a draft glossary file, or of a directory of them. | none |
| `--code <dirs>` | Comma-separated directories whose files may cite spec and deviation IDs and hold `PLACEHOLDER` comments. | `src,tests,tools` |
| `--references <dirs>` | Comma-separated directories whose files may cite IDs but whose `PLACEHOLDER` comments do not count against parity. | none |
| `--images <ranges>` | Comma-separated half-open address ranges of the original's flat 32-bit images, such as `0x00400000..0x004C9000`. A `0x` value inside one that a code comment gives must be recorded in an entry the comment cites. | none, so only `fn_` and `g_` names are checked |
| `--max-range <bytes>` | The largest address range an entry can record an address by. A larger one, such as a whole section, records only its two ends. | `0x10000` |
| `--data-dirs <dirs>` | Comma-separated top-level directories of the original's data. A path into one must name a file of some build, with its exact case. | the top-level directories of the files the build entries list |
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

## In GitHub Actions

The toolkit's `actions/check-documentation` composite action runs this checker with `--check`,
installs a pinned Kaitai compiler when the repository has `.ksy` files (and then passes
`--require-ksc`), and exposes every option
above as an input. Pin the action to the toolkit commit whose `packages/standard-checker` matches
the version installed here, so CI and local runs apply the same checks.

[The documentation standard check guide](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/documentation-standard-check.md)
covers setting up the action, every check the tool makes, and moving an older restoration onto it.
