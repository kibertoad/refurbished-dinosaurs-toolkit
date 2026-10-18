---
"@scientific-method/standard-checker": patch
---

The checker reads each file once per run instead of up to four times, and lists directories without a `stat` call per entry. On a spec of 3,000 files it runs in about half the time, with the same output.
