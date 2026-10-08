---
"@scientific-method/standard-checker": patch
---

Read `''` inside a single-quoted YAML value as one single quote, as YAML does. A value such as
`'C:\Users\O''Brien\Saves'` was cut at the first quote, so a listing record or spec file could not
hold a value with a single quote and also a double quote or a backslash.
