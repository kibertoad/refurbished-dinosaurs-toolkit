# Documentation standard check

`tools/check-documentation.mjs` checks a restoration's `spec/`, `PARITY.md` and `DEVIATIONS.md`
against version 1 of the
[documentation standard](https://dinorefurb.com/documentation-standard/#checks) and writes the
four indexes in `spec/index/`. It needs Node.js 20 or newer and no packages. It started as
`tools/check-spec.mjs` in the Chaos Overlords restoration.

## In CI

```yaml
- uses: actions/checkout@<sha>
  with:
    persist-credentials: false
    # Full history, so the check can find where a pull request forked from its base branch and
    # fail when the pull request deletes a spec ID, area or deviation that exists there.
    fetch-depth: 0
- uses: kibertoad/refurbished-dinosaurs-toolkit/actions/check-documentation@<sha>
  with:
    references: multiplayer
```

The action runs the check with `--check`, so a stale index fails it. It installs Kaitai Struct
compiler 0.11 through `actions/setup-kaitai` first, so a `spec/formats/*.ksy` definition that does
not compile fails the job instead of printing a warning. The compiler runs on the runner's Java;
set `java-version` on a runner without one, or set `kaitai-version: ""` to skip the install.

| Input | Default | Meaning |
|---|---|---|
| `root` | `.` | Workspace-relative path of the repository to check. |
| `code` | `src,tests,tools` | Directories whose files may cite spec and deviation IDs and hold `PLACEHOLDER:` comments. |
| `references` | empty | Directories whose files may cite IDs but whose `PLACEHOLDER:` comments do not count against parity. |
| `data-dirs` | from the build entries | Top-level directories of the original's data. A path into one must name a build file with its exact case. |
| `base` | fork point | Ref whose IDs, areas and deviations must still exist. |
| `kaitai-version` | `0.11` | Compiler release to install, or empty to skip. |
| `java-version` | empty | Java to install with `actions/setup-java` before the compiler. |

`actions/setup-kaitai` can also be used on its own. It exports `KSC` and refuses any version
whose SHA-256 is not pinned in `actions/setup-kaitai/install.sh`.

## Locally

```sh
node <toolkit>/tools/check-documentation.mjs            # check, then rewrite stale indexes
node <toolkit>/tools/check-documentation.mjs --check    # check, and fail on a stale index
node <toolkit>/tools/check-documentation.mjs --help
```

Run it from the restoration's root or pass `--root`. Set `KSC` to the compiler's launcher, or put
`kaitai-struct-compiler` on `PATH`, to compile the `.ksy` definitions.

## Moving a restoration onto it

Delete the restoration's own copy of the script, point its workflow at the action and its local
validation at this script, and run it once without `--check`: the generated index header now
names the documentation standard check instead of `tools/check-spec.mjs`, so every index is
rewritten once. For Chaos Overlords, pass `references: multiplayer` to keep the old behaviour.

The tests in `tests/documentation-standard/` run the script over a small fixture restoration and
over broken copies of it.
