---
"@scientific-method/standard-checker": patch
---

The checker reads each file once per run instead of up to four times, and lists directories without a `stat` call per entry. On a spec of 3,000 files it takes about 40% less CPU time, with the same output.
