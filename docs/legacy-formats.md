# Legacy format packages

`Toad.Discovery.LegacyFormats` contains format knowledge that is independent of a
specific game and defensively rejects truncated, excessive, or out-of-bounds data.

- `RawIndexedImageDecoder` and `PcxDecoder` produce indexed images and RGBA buffers.
- `Rle8BitmapDecoder` normalizes BI_RLE8/BI_RGB payloads into uncompressed 8-bit BMPs.
- `SmackerMovieDecoder`, `SmackerMovieStream`, `SmackerVideoDecoder`, and
  `SmackerAudioDecoder` cover bounded index/frame parsing, palette updates, video blocks,
  and packed 8-bit mono audio. Unsupported audio profiles fail explicitly.
- `CueSheet` parses track/index boundaries, `CddaWave` wraps raw CD audio as PCM WAV,
  and `RawMode1Image`/`Iso9660` provide bounded access to Mode 1 disc images.

These codecs were moved intact from validated restoration code, then renamed and
packaged. Continue using synthetic fixtures in this repository. Any game-specific
container semantics, role names, heuristics, or source fingerprints remain downstream.
