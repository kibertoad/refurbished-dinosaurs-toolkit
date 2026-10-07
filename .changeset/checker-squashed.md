---
"@scientific-method/standard-checker": minor
---

Add `--squashed OLD=NEW[+NEW...],...`, and the check-documentation action's `squashed` input, for a restoration that squashes its superseded entries into their replacements before its spec is relied on outside the project. A listed deletion passes instead of failing IDENTIFIERS-6 when the entry's `superseded_by` at the base names exactly the listed replacements and each exists and is not superseded. A listed ID that still exists fails, one the base does not have is named as a skipped step, and a squashed ID still cited anywhere fails, a build or source alias included.
