# Source project inventory

This kit was distilled from two working restorations rather than designed in a vacuum.
The table records the recurring capability, what each project demonstrated, and where
the generalized result belongs.

| Capability | Chaos Overlords restoration | Conqueror A.D. 1086 restoration | Generalized home |
|---|---|---|---|
| Project split | Core, extractor, game, state-diff tools, xUnit | Core, Resources, game, importer, inspector, executable specs, xUnit | Template Core/Resources/Game/Import/Inspect/Tests shape |
| Source ownership | Verified installed asset pack, repaired graphics, catalog generation | Verified mixed-mode disc/archive content, decoded media, repair/uninstall, read-only inspector | Core manifests/verifier/staging/idempotent writes/manifest-scoped uninstall plus template importer and inspector |
| Runtime content | Adjacent assets with per-user fallback | Package/user-content/current-directory resolution | `RestorationPaths` |
| Failure UX | Console, Windows dialog, local startup log, import guidance | Same pattern with separate state/content roots | `StartupFailure` |
| Standard legacy formats | BMP RLE8 and Smacker assets | PCX/raw indexed images, palettes, Smacker, CUE/CDDA, raw Mode 1 and ISO-9660 | `Toad.Discovery.LegacyFormats` with bounded readers and synthetic tests |
| Engine/game formats | Chaos PX containers and header repair | Dynamix GOB/RES/CSF/HAT, executable fixups, scenes, conversations, action trees, sound banks | Kept downstream until another real consumer proves a stable shared contract |
| Determinism | Commands, events, MSVC-style seeded resolution, replay, JSON state diff | Platform-independent campaign and battle rules, executable specs | RNG/state-diff primitives, playbook, parity/rules templates, and template Core boundary |
| Save/settings | Atomic native saves and replay format | Per-user versioned settings and campaign save slots | Atomic file/settings helpers plus native-save/replay design template |
| Presentation/input | Fixed logical viewport, pixel font, keyboard/mouse navigation | Integer scaling, fixed-width text, pixel font, controller bindings | Viewport/text primitives and UI-atlas guidance; concrete MonoGame input mapping stays per game |
| Legal boundary | Denied asset roots/extensions and large-file gate | Denied UserContent/analysis roots with a broader legacy-extension set | Policy schema, merged defaults, verification action |
| Packaging | Self-contained Windows/Linux/macOS packages; Inno import/retry flow | Same with source-management shortcuts | Packaging guide and Windows Inno Setup template |
| CI/release | Matrix smoke tests, installer and assetless proof, pinned actions, zizmor, manual release | Same pattern with installed-layout checks | Shared actions and template workflows |
| Static analysis | Reproducible headless Ghidra scripts and evidence notes | Ghidra log plus separate read-only inspection utilities | `/ghidra`, Ghidra workflow, rules/evidence ledger, and template Inspect project |

Game-specific fingerprints, proprietary engine formats, balance tables, UI layouts,
rules, executable addresses, and original-derived evidence remain in their game
repositories. Generic media/container formats with stable public specifications can be
promoted with bounded readers and synthetic fixtures; proprietary formats should wait
for another real consumer to prove the abstraction.
