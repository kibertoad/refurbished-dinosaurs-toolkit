---
"@scientific-method/standard-checker": patch
---

The range-end check no longer fails a range whose end is the first byte of an inventoried function or of a range of one. A one-byte function's last byte is its first, so a correct range that stops where such a function starts was failed with an end that would claim the function's byte.
