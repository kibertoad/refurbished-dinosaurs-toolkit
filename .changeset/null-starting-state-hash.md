---
"@scientific-method/standard-checker": patch
---

An experiment whose fixture gives no save hash now gets a problem that says where the hash goes (`starting_state.xxh3`). With `starting_state: null`, the problem also says that null names a save kept with the captures, and that an experiment starting without a save has `starting_state` new-game or emulated-call. Which experiments need the hash is unchanged. A fixture's `starting_state.xxh3` and `starting_state.base_xxh3` must now be 32 lower-case hex digits, as a build's file hashes already are.
