# ADR 0006: Shared runtime primitives

Status: Accepted

## Context

Restorations duplicate portable asset references, backup writes, settings recovery, PCM conversion
and button transitions. Their save payloads, original audio rules and control defaults differ.

## Decision

Extend Core with portable paths, validated generation storage and generic input snapshots/bindings.
Keep WAVE container parsing/writing in LegacyFormats. Add dependency-free Media.Audio for PCM
conversion and backend-neutral resource lifetimes, usable without legacy container readers.
The game supplies serializers, size bounds, incompatible-version admission, token identities,
voice limits, routing and device objects. No MonoGame dependency enters these packages.

## Consequences

Each slice can release independently in the shared .NET version series. Consumers migrate against
published versions after their synthetic controls pass. File operations require serialized writers;
filesystem durability and concurrent replacement are documented limits. These primitives do not
standardize save envelopes, original control rules or music fades.
