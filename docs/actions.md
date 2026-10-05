# Composite actions

The toolkit publishes composite GitHub Actions under `actions/`. Reference them by commit
SHA, as with any third-party action:

```yaml
- uses: kibertoad/refurbished-dinosaurs-toolkit/actions/verify-repository@<sha>
```

| Action | Runs on | Does |
|---|---|---|
| `check-documentation` | any | Runs `@scientific-method/standard-checker --check` from the pinned toolkit commit. On a pull request it fetches the base branch and fails when the comparison with it cannot run. |
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
installs the pinned Mesa build's x64 directory, `opengl32.dll` with the libraries it loads, under a
private `RUNNER_TEMP` directory. It verifies the archive SHA-256 before extraction and requires
7-Zip. A download, checksum or extraction failure fails the step; it never skips the graphics test.
It refuses runners other than Windows x64.

| Output | Value |
|---|---|
| `driver-path` | The absolute `opengl32.dll` path. |
| `directory` | The directory holding it and the libraries it loads. |

The action changes nothing else: the host selects the driver for its smoke test alone. Windows
looks for `opengl32.dll` beside the executable before the system directory, and resolves the
driver's own libraries, such as `libgallium_wgl.dll`, through the normal search order, which does
not include the driver's directory. So either load `driver-path` explicitly (for SDL,
`SDL_OPENGL_LIBRARY`) with `directory` first on `PATH`, or copy the directory beside the test
executable. Mesa picks its D3D12 backend before llvmpipe when the runner offers D3D12; set
`GALLIUM_DRIVER=llvmpipe` for the same software renderer on every runner. Never copy the
directory into published outputs. Consume the action by commit SHA.

```yaml
- uses: kibertoad/refurbished-dinosaurs-toolkit/actions/setup-software-opengl@<sha>
  id: gl
- run: |
    $env:PATH = '${{ steps.gl.outputs.directory }};' + $env:PATH
    dotnet test tests/Graphics.SmokeTests
  shell: pwsh
  env:
    SDL_OPENGL_LIBRARY: ${{ steps.gl.outputs.driver-path }}
    GALLIUM_DRIVER: llvmpipe
```
