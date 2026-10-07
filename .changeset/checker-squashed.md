---
"@scientific-method/standard-checker": minor
---

Add `--squashed OLD=NEW[+NEW...],...`, and the check-documentation action's `squashed` input, for a restoration that squashes its superseded entries into their replacements before its spec is relied on outside the project. A listed deletion passes instead of failing IDENTIFIERS-6 when the entry's `superseded_by` at the base names exactly the listed replacements and each exists and is not superseded or is squashed in the same change, so `A=B,B=C` squashes a chain. A listed ID that still exists fails, which keeps a squashed ID from being used again while the option stays in place, one the base does not have is named as a skipped step, and a squashed ID still cited in the spec, the glossary, the code, the `--references` directories, `parity/` or `deviations/` fails, a build or source alias included, with a message that names the replacements to cite.
