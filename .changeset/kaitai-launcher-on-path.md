---
"@scientific-method/standard-checker": patch
---

On Windows the checker now finds the Kaitai Struct compiler's `.bat` launcher on `PATH`. It looks for `kaitai-struct-compiler` and `ksc` with each `PATHEXT` extension, skips the extensionless Unix script that the official release puts beside the launcher, and runs the launcher by its full path. Before, it ran the bare name through `cmd.exe`, which started the launcher with `%~dp0` set to the working directory, so the launcher could not find its jars and the checker reported no compiler. A `KSC` holding a bare name is looked up on `PATH` the same way. A launcher found on `PATH` whose `--version` fails now gets a warning naming it, with its output, instead of reading as a missing compiler.
