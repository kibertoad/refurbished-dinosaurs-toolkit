# Composite actions

The toolkit publishes composite GitHub Actions under `actions/`. Reference them by commit
SHA, as with any third-party action:

```yaml
- uses: kibertoad/refurbished-dinosaurs-toolkit/actions/verify-repository@<sha>
```

| Action | Runs on | Does |
|---|---|---|
| `check-documentation` | any | Runs `@scientific-method/standard-checker --check` from the pinned toolkit commit. |
| `setup-kaitai` | any | Installs a Kaitai Struct compiler release whose SHA-256 is pinned. |
| `verify-repository` | any with PowerShell 7 | Fails when files that must stay local could be committed. |
| `setup-inno` | Windows | Installs a verified Inno Setup 7 compiler for building installers. |
| `setup-software-opengl` | Windows x64 | Provisions checksum-verified Mesa for graphics smoke tests outside build outputs. |

`check-documentation` and `setup-kaitai` are covered, with every input, in
[the documentation standard check guide](documentation-standard-check.md).

## verify-repository

Checks every tracked file and every untracked file that `.gitignore` does not exclude, so it also
catches what the next `git add` would pick up. It reports:

- any path under a denied root, such as `UserContent/` or `analysis/original/`;
- any file with a restricted extension (game media, executables, disc images) outside an approved
  clean-room or synthetic-fixture root;
- any file larger than the size limit that is not listed as an approved large file.

| Input | Meaning | Default |
|---|---|---|
| `policy` | Repository-relative path of the policy JSON. | `tools/repository-policy.json` |

The policy file follows [`schemas/repository-policy.schema.json`](../schemas/repository-policy.schema.json).
The toolkit's own [`tools/repository-policy.json`](../tools/repository-policy.json) is a starting
point:

| Field | Meaning |
|---|---|
| `deniedRoots` | Path prefixes that must never hold committable files. |
| `restrictedExtensions` | File extensions allowed only under `approvedRestrictedRoots`. |
| `approvedRestrictedRoots` | Path prefixes where restricted extensions are allowed. |
| `maximumTrackedFileBytes` | The largest file allowed without review. |
| `approvedLargeFiles` | Exact paths allowed past the size limit. |

Prefixes match from the repository root, ignoring case. The same script runs locally:

```powershell
pwsh tools/Verify-Repository.ps1 -RepositoryRoot . -PolicyPath tools/repository-policy.json
```

The action runs the toolkit's copy of the script from the pinned commit, so a restoration that uses
the action needs no copy of its own.

## setup-inno

Downloads the Inno Setup installer from the `jrsoftware/issrc` GitHub release, checks its GitHub
release attestation and its Authenticode signature (signer Pyrsys B.V.), installs it under
`RUNNER_TEMP` for the current user, and checks that `ISCC.exe --version` reports the requested
version. It sets `INNO_COMPILER` to the path of `ISCC.exe` for later steps.

| Input | Meaning | Default |
|---|---|---|
| `version` | Exact Inno Setup 7 version, such as `7.1.0`. | `7.1.0` |

```yaml
- uses: kibertoad/refurbished-dinosaurs-toolkit/actions/setup-inno@<sha>
- run: '& $env:INNO_COMPILER installer/setup.iss'
  shell: pwsh
```

## setup-software-opengl

Hosted Windows runners can expose only OpenGL 1.1 without framebuffer objects. This action
installs the pinned Mesa x64 driver and its adjacent libraries under a private `RUNNER_TEMP`
directory. It verifies the archive SHA-256 before extraction and requires 7-Zip. A download,
checksum or extraction failure fails the step; it never skips the graphics test.

The `driver-path` output is the absolute `opengl32.dll` path. The host explicitly configures
its graphics backend for its smoke test; the action does not alter game packages or default
rendering. Never copy this directory into published outputs. Consume the action by commit SHA.
