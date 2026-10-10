---
"@scientific-method/executable-reader": minor
---

The `imports` report ends the import directory at the first descriptor whose Name or FirstThunk is zero, where the NT loader, Wine and ReactOS end it. A file whose last descriptor is followed by one with a stray time stamp or lookup table RVA, which some linkers and packers write, used to fail with `Import descriptor N has no DLL name or address table` and is now reported. The new `directoryEnd` field gives the descriptor the directory ended at and its nonzero fields. When any field is nonzero, `pastEnd` lists each later descriptor that is not all zero, up to an all-zero one, with its DLL name and nonzero fields, since a loader reading past the end would use them, and says where that read stopped. A descriptor in the zeros the loader fills past a section's raw data reads as zero. `exclusions` names the Windows 9x loader's directory end, which the report does not check.
