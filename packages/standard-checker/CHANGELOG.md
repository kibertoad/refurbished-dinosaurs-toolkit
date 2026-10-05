# @scientific-method/standard-checker

## 0.3.0

### Minor Changes

- 617f243: A field name after a dot in a procedure is now checked against the layout of its structure's format, wherever the structure's type is written in the notation's type form: a `let` with a type or `new`, the rule's Parameters section, a `define` signature, the Outputs of a called rule, the glossary entry, or the layout row of the field before it. A spec that names a field its format's layout lacks now fails the check. A name whose type is only described in prose stays unchecked. The documentation standard check guide lists the forms the checker reads.

  A parameter written `` `name: type` `` in the Parameters section now counts as a local, and a `#` inside a string in a procedure no longer starts a comment that hides the rest of the line and the lines up to the next quote.

## 0.2.0

### Minor Changes

- 86a854a: A problem that breaks a numbered rule of the documentation standard now ends with the rule's label in brackets, such as `[STATUS-4]`, and the summary says once what the labels mean. The labels cover the rules numbered so far: Identifiers, Status and the shared part of Entry types.

## 0.1.0

### Minor Changes

- baedab9: First release: the documentation standard check, extracted from `tools/check-documentation.mjs` of
  the toolkit, as the `standard-checker` command.
