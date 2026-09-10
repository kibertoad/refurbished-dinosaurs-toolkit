# Source project inventory

This kit was distilled from two working restorations rather than designed in a vacuum.
The table records the recurring capability, what each project demonstrated, and where
the generalized result belongs.

| Capability | Chaos Overlords restoration | Conqueror A.D. 1086 restoration | Generalized home |
|---|---|---|---|
| Project split | Core, extractor, game, state-diff tools, xUnit | Core, Resources, game, importer, inspector, executable specs, xUnit | Template Core/Resources/Game/Import/Inspect/Tests shape |
| Source ownership | Verified installed asset pack, repaired graphics, catalog generation | Verified mixed-mode disc/archive content, decoded media, read-only inspector | Core manifests/verifier/staging plus template importer and read-only inspector |
| Runtime content | Adjacent assets with per-user fallback | Package/user-content/current-directory resolution | `RestorationPaths` |
| Failure UX | Console, Windows dialog, local startup log, import guidance | Same pattern with separate state/content roots | `StartupFailure` |
| Image formats | Header repair, indexed/RLE resources, original font atlas | PCX, indexed palettes, scenes, textures, movies | Indexed palette primitive; game-specific codecs remain downstream |
| Determinism | Commands, events, seeded resolution, replay, JSON state diff | Platform-independent campaign and battle rules, executable specs | Playbook and template Core boundary |
| Legal boundary | Denied asset roots/extensions and large-file gate | Denied UserContent/analysis roots with a broader legacy-extension set | Policy schema, merged defaults, verification action |
| Packaging | Self-contained Windows/Linux/macOS packages; Inno import/retry flow | Same with source-management shortcuts | Packaging guide and Windows Inno Setup template |
| CI/release | Matrix smoke tests, installer and assetless proof, pinned actions, zizmor, manual release | Same pattern with installed-layout checks | Shared actions and template workflows |
| Static analysis | Reproducible headless Ghidra scripts and evidence notes | Ghidra log plus separate read-only inspection utilities | `/ghidra` and Ghidra workflow |

Game-specific fingerprints, file formats, balance tables, UI layouts, rules, executable
addresses, and evidence remain in their game repositories. Promote code here only after
the second real consumer proves the abstraction.
