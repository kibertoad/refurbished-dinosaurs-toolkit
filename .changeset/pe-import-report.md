---
"@scientific-method/executable-reader": minor
---

Add the `imports` command and the `pe-imports` export (`importReport`). It reads a PE32 or PE32+ file's import directory and lists, for each import address table slot by address, the DLL and the name and hint or the ordinal that the import lookup table (or, for a descriptor without one, the stored import address table) puts there. A slot holding a bound address with no lookup table behind it gets no import. The query names at least one positive control slot, and a control that maps to anything else rejects the report. `sourceKind: "pe32+"` is new and is read only by `imports`.
