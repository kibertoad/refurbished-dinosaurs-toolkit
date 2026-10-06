---
"@scientific-method/standard-checker": minor
---

Check argument counts. Every `call` must pass one argument for each item of the called rule's Parameters list (in each entry of a split rule that lists one of the caller's builds), every function call one for each parameter of its `define`, and every `emit` one for each item of the Parameters list of each handler its glossary entry names. Every `emit` of one event must pass the same number of arguments. A Parameters section that is neither `None.` nor such a list cannot be counted, so the calls and emits against it are named as a skipped step and do not fail the run.
