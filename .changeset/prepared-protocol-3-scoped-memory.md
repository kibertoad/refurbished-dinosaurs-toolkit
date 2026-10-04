---
"@scientific-method/executable-reader": major
---

Speak prepared protocol 3, which adds `callModels[].preservesMemory` (explicit byte scopes a modeled
service is assumed to leave unchanged). The reader now needs a scientific-method-engine release that
speaks protocol 3, and a protocol 2 engine refuses it. Upgrade both packages together; see the
migration guide.
