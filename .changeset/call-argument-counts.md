---
"@scientific-method/standard-checker": minor
---

Check argument counts. Every `call` must pass one argument for each item of the called rule's Parameters list (in each entry of a split rule that lists one of the caller's builds), every function call one for each parameter of its `define`, and every `emit` one for each item of the Parameters list of each handler its glossary entry names (in each entry of a split handler that lists one of the emitting rule's builds). Two `emit`s of one event in rules that share a build must pass the same number of arguments. A Parameters section that is neither `None.` nor a list whose items each open with a code span followed directly by a colon cannot be counted, so the calls and emits against it are named as a skipped step and do not fail the run. So is a call or emit whose argument list is never closed.
