---
"@scientific-method/standard-checker": minor
---

Check more of the addresses a restoration gives as evidence, and the spec's paths into the rebuild. The address check now reads PowerShell comments (`#` and `<# … #>`) as well as C#, TypeScript and JavaScript ones, and checks the addresses the code itself uses, as numbers or inside strings: each must be recorded in an entry that the comment trailing its line, or the nearest comment above it, cites. `--message <file>` checks the addresses of a commit message against the entries it cites, for a commit-msg hook. `--rebuild <dirs>` (default `src,tests`) fails a spec file that names a path in the rebuild's directories or a source file found in them, as the standard's rule that the spec never names the rebuild requires.
