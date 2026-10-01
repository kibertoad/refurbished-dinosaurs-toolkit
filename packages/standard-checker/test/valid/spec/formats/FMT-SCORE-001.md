---
id: FMT-SCORE-001
title: The best score in DATA/SCORES.BIN
status: sourced
builds: [BLD-EXAMPLE-1.0]
superseded_by: []
files: ["DATA/SCORES.BIN"]
byte_order: little
size: 2
text: false
definition: fmt_score_001.ksy
evidence: [SRC-MANUAL]
conflicting: []
split_with: []
related: []
---

## Layout

`DATA/SCORES.BIN` holds one record.

| Offset | Size | Type | Name | Meaning | Status | Evidence |
|---|---|---|---|---|---|---|
| `0x00` | 2 | `UINT16LE` | `best` | The best score. | sourced | SRC-MANUAL |
| `0x02` | | | | Total size 2 | | |

## Enumerations and flags

None.

## Differences between builds

None known.

## Coverage

Every byte.

## Open questions

None known.
