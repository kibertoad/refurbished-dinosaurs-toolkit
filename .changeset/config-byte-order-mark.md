---
"@scientific-method/executable-reader": patch
---

A config file with a leading UTF-8 byte order mark, as Windows PowerShell 5.1 writes with `-Encoding utf8`, now reads the same as one without. A config starting with a UTF-16 or UTF-32 byte order mark, one starting with more than one UTF-8 byte order mark, or one whose bytes are not UTF-8, fails with an error naming the cause, where before these failed as a JSON syntax error and bytes that are not UTF-8 were replaced with U+FFFD. Non-ASCII text in a config, such as a source path in a folder with an accented name, now reaches the engine intact on Windows, once `scientific-method-engine` is the release from the same change, which reads the reader's pipe as UTF-8.
