---
"@scientific-method/standard-checker": patch
---

Stop counting an event's emitter as one of its handlers in the argument count check. A rule that an event's glossary entry names, whose own procedure emits the event and whose When it runs section does not name it, is the emitter, so its Parameters section is no longer compared with the event's `emit`s and no longer gives a skipped step while it is in prose. A handler that emits its event again names the event in When it runs and is still counted.
