---
"@scientific-method/executable-reader": major
---

Speak prepared protocol 2, which adds `callModels[].preservesMemory` (explicit byte scopes a modeled
service is assumed to leave unchanged). The reader now needs a scientific-method-engine release that
speaks protocol 2, and a protocol 1 engine refuses it. Upgrade both packages together; see the
migration guide.
