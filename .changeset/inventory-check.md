---
"@scientific-method/executable-reader": minor
---

Adds the `inventory-check` command, which runs in the engine and lists the resolved direct call targets a function inventory does not list as starts. `prepare` resolves a config's `inventory` path against the config file's directory, as it does `source`, and `ReportConfig` gains the `inventory` field. It needs a `scientific-method-engine` release that has the command.
