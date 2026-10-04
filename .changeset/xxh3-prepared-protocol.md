---
"@scientific-method/executable-reader": major
---

Prepared-config protocol 2: a query names its source by `xxh3`, the source's XXH3-128 hash as 32
lower-case hex digits, as the documentation standard hashes every file. `sha256` is no longer read.
The reader refuses an engine that speaks protocol 1. `sourceXxh3(bytes)` computes the hash.
