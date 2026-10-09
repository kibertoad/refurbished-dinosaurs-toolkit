---
"@scientific-method/standard-checker": patch
---

A superseded format entry keeps the `files` it had when it was replaced (IDENTIFIERS-7). A pattern of such an entry that matches no manifest file now passes when it matches a path that the build's `<ID>.other-files.yaml` gives by its own path, so retiring an entry whose files left the manifest no longer means emptying its `files`. It still fails with `files pattern <p> matches no file of <build> in its manifest or its list of other files` when it matches neither, and names the directory exclusion, the prose list or the unreadable list when one of those is all it could have matched. Entries at every other status still need their files in the manifest.
