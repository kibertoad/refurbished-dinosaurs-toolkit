---
"@scientific-method/standard-checker": minor
---

Check argument counts. Every `call` must pass one argument for each item of the called rule's Parameters list, every call to a function a rule defines one for each parameter of its `define`, and every `emit` one for each item of the Parameters list of each handler its glossary entry names. A split callee or handler is counted in each entry that lists one of the calling or emitting rule's builds. Two `emit`s of one event in rules that share a build must pass the same number of arguments, which is the only check an event with no handlers gets. A Parameters list counts only when each item opens with one code span followed directly by a colon. Any other Parameters section, a list with an item such as ``- `x`, `y`: the cell`` included, cannot be counted, so the calls and emits against it are named as a skipped step and do not fail the run. So is a call or emit whose argument list is never closed.
