---
"@scientific-method/executable-reader": patch
---

A config file with a leading UTF-8 byte order mark, as Windows PowerShell 5.1 writes with `-Encoding utf8`, now reads the same as one without. A UTF-16 config, or one whose bytes are not UTF-8, fails with an error naming the encoding, where before UTF-16 failed as a JSON syntax error and bytes that are not UTF-8 were replaced with U+FFFD.
