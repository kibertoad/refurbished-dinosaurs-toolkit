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
starting with the file's path), and 2 when the options are invalid. `--record-validation` cannot be
combined with `--check`.

## Options

| Option | Meaning | Default |
|---|---|---|
| `--root <dir>` | The repository to check. | the current directory |
| `--check` | Fail when an index or `PARITY.md` is stale, instead of rewriting it. | rewrite |
| `--base <ref>` | Also fail when a spec ID, area or deviation that exists at `<ref>` is gone. | where HEAD forked from `origin/$GITHUB_BASE_REF` or `origin/main`, when that resolves |
| `--no-ksy` | Skip compiling the Kaitai definitions in `spec/formats/`. | compile |
| `--glossary <path>` | Also accept the terms of a draft glossary file, or of a directory of them. | none |
| `--code <dirs>` | Comma-separated directories whose files may cite spec and deviation IDs and hold `PLACEHOLDER` comments. | `src,tests,tools` |
| `--references <dirs>` | Comma-separated directories whose files may cite IDs but whose `PLACEHOLDER` comments do not count against parity. | none |
| `--images <ranges>` | Comma-separated half-open address ranges of the original's flat 32-bit images, such as `0x00400000..0x004C9000`. A `0x` value inside one that a code comment gives must be recorded in an entry the comment cites. | none, so only `fn_` and `g_` names are checked |
| `--max-range <bytes>` | The largest address range an entry can record an address by. A larger one, such as a whole section, records only its two ends. | `0x10000` |
| `--data-dirs <dirs>` | Comma-separated top-level directories of the original's data. A path into one must name a file of some build, with its exact case. | the top-level directories of the files the build entries list |
| `--record-validation <builds>` | Write `VALIDATION.md` for the marked test files of validated parity rows, naming the comma-separated build IDs the run used. Run it only after every test in those files passed with none skipped. | not written |
| `--help` | Print the options. | |

The `KSC` environment variable names the Kaitai Struct compiler. Without it, the checker looks for
`kaitai-struct-compiler` or `ksc` on `PATH`, and warns when it finds neither.

## In GitHub Actions

The toolkit's `actions/check-documentation` composite action runs this checker with `--check`,
installs a pinned Kaitai compiler when the repository has `.ksy` files, and exposes every option
above as an input. Pin the action to the toolkit commit whose `packages/standard-checker` matches
the version installed here, so CI and local runs apply the same checks.

[The documentation standard check guide](https://github.com/kibertoad/refurbished-dinosaurs-toolkit/blob/main/docs/documentation-standard-check.md)
covers setting up the action, every check the tool makes, and moving an older restoration onto it.
