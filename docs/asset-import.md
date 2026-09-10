# Original-content import contract

The importer is a product feature, not a developer-only script.

## Source identification

Model source discovery as adapters. Candidates may come from explicit CLI/UI paths,
platform uninstall records, known storefront metadata, mounted media, or common
installation folders. Discovery only proposes candidates; fingerprints decide whether
an edition is supported. Always allow manual selection.

Use several stable fingerprints: archive/executable size and SHA-256, required file
set, volume/container metadata, and (when needed) a small internal signature. Give a
precise unsupported-edition error. Do not modify the source installation.

## Transactional pipeline

```text
discover -> identify edition -> verify source -> extract/decode -> verify output
         -> write manifest -> swap staged pack into place
```

An interrupted or failed import must leave the last verified pack intact. A complete
matching destination can be a no-op unless `--force` is supplied. Support separate
`--verify-source`, `--verify-output`, and catalog/report modes so diagnosis never
requires rewriting content.

Use `InstalledContentWriter` when an extractor emits files incrementally: it
atomically replaces changed output and reuses byte-identical output. If imported
content can be removed separately from the remake, use
`InstalledContentUninstaller`; it deletes only paths declared by the installed
manifest and preserves any unlisted user files.

The output manifest should include importer version, source edition (not purchase
channel unless relevant), source fingerprints, every generated relative path, length,
and hash. Never copy executables when decoded data is sufficient. Never place imported
content in build output by default; packaging must explicitly prove it is absent.

Exit codes should distinguish success, unsupported/missing source, invalid output,
and unexpected failure. Installers need that distinction for retry and skip flows.
