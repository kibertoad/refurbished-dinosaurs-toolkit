---
"@scientific-method/standard-checker": major
---

The citation check reads `.fs` files under `--code` and `--references` like the other code files, so an F# file that cites a spec ID that does not exist or is superseded, or a deviation ID missing from `deviations/`, now fails the check.
