---
"@scientific-method/standard-checker": patch
---

The range-end check no longer fails a range whose end is the byte of a one-byte function, or of a one-byte range of a function's body. That byte is both its first and its last, so a correct range that stops where such a function starts was failed with an end that would claim the function's byte. A range ending there still fails when a longer range of another function ends on the same byte.
