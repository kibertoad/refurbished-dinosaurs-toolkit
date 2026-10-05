---
"@scientific-method/standard-checker": major
---

An experiment whose `starting_state` is none of the forms the documentation standard defines (a save or save patch in `saves/`, `new-game`, `emulated-call` or null) now fails with `starting_state <value> is none of the forms the standard defines`, which lists them. Before, any other value was accepted and the run reported only the missing save hash, so a typo such as `new_game` or a value the standard has no word for, such as `cold-boot`, read as a missing hash, and with a hash given it passed. A save or save patch is a file under `saves/`, so `saves/` alone or a path with an empty, `.` or `..` segment fails the same way. Such an experiment, and one that lacks `starting_state`, no longer gets the save hash problems, since which hashes the fixture needs depends on the form; a missing field is reported only as `front matter lacks starting_state`.
