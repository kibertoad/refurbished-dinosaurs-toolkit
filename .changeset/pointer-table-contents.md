---
"@scientific-method/executable-reader": minor
---

Add the `table` command and the `table-contents` export. It reads the entries of one pointer table from the bytes of an `mz` or `pe32` source, gives each entry's raw pointer, relocation, mapped address and file range, and reads each string to a named terminator under a byte limit, continuing into the zeros the loader fills past a PE section's raw data. A null pointer (by a value and the test the query names), an empty string, a read with no terminator, a target in memory the build does not initialize and a target with no address (including a word a declared MZ relocation shows to be a segment) are separate results, and the last three are errors. An analyzer listing is compared with the bytes entry by entry, and a positive control at an entry other than the first rejects the report when it misses.
