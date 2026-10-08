---
"@scientific-method/standard-checker": patch
---

A path into a data directory now passes when a build's `<ID>.other-files.yaml` gives it, as well as when a manifest lists it. A Source location that cites a bundled archive the manifest leaves out no longer fails once the first manifest file in its directory turns the check on there. A path in another case names the file that spells it, a path under a directory exclusion fails naming the exclusion, and a path in neither list fails with `path <p> is in no build's manifest or list of other files`.
