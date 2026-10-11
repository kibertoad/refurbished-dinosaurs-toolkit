---
"@scientific-method/standard-checker": minor
---

The range-end check reads `ends_on_last_byte` (ENTRY-TYPES-22), the ranges an entry gives that end on an inventoried function's last byte on purpose, such as a function body cited without its one-byte return. A listed range passes wherever the entry gives it, written the same way, in a location, the text or a table. A listed range fails with `ends_on_last_byte lists <range>, which the entry does not give as a location or in its text or tables` when the entry gives it nowhere, and with `ends_on_last_byte lists <range>, which the check passes without the listing everywhere the entry gives it; leave it out` when it needs no listing. A field that is not a list, or is empty, fails. The message for a range that ends on a last byte now names the field.

An end where an inventoried function, or a range of a function's body, starts now passes whatever other function's last byte it is, as the standard's Notation states the exception. A range that ended where a function longer than one byte starts on another function's last byte, or where a one-byte function sits on a longer function's last byte, failed before.
