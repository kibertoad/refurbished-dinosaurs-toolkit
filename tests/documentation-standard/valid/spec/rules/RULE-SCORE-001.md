---
id: RULE-SCORE-001
title: A kill adds one point to the score
status: sourced
builds: [BLD-EXAMPLE-1.0]
superseded_by: []
evidence: [SRC-MANUAL]
conflicting: []
split_with: []
related: []
---

## Summary

Each kill adds one point.

## When it runs

When an enemy dies.

## Parameters

- `n`: the score before the kill.

## Inputs

None.

## Procedure

```text
define add_points(n: UINT16):
    return n + 1
```

## Outputs

Returns the new score.

## Edge cases

None known.

## What the sources say

SRC-MANUAL says each kill is worth one point.

## Differences between builds

None known.

## Open questions

None known.
