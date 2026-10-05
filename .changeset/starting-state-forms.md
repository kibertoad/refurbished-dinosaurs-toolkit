---
"@scientific-method/standard-checker": major
---

An experiment whose `starting_state` is none of the forms the documentation standard defines (a save or save patch in `saves/`, `new-game`, `emulated-call` or null) now fails with `starting_state <value> is none of the forms the standard defines`, which lists them. Before, any other value was accepted and the run reported only the missing save hash, so a typo such as `new_game` or a value the standard has no word for, such as `cold-boot`, read as a missing hash, and with a hash given it passed. Such an experiment no longer gets the save hash problems, since which hashes the fixture needs depends on the form.
