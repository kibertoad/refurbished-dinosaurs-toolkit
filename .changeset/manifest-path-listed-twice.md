---
"@scientific-method/standard-checker": minor
---

A build manifest that lists the same path twice now fails with `<path> is listed twice`, whether or not the two items give the same format, size and hash. A manifest path written as an unquoted number such as `0` is read as that text instead of failing with `every file has a path`, and a path written as a map or list, in a manifest or a list of other files, fails with `a path is text, not a map or list`.
