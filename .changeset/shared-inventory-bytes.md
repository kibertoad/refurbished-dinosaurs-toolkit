---
"@scientific-method/standard-checker": minor
---

`standard-coverage` counts a byte that two inventory rows both list once. `bytes` and `citedBytes` are over the union of the in-scope bodies, so a shared function tail no longer adds its bytes once per function, and a new `sharedBytes` figure gives the in-scope bytes that more than one in-scope function lists. The text line ends with `; <n> bytes listed by more than one function` when there are any. A location in shared bytes still cites every function that lists them. The range-end check names every function whose range ends on the byte a location ends on, where it named only one.
