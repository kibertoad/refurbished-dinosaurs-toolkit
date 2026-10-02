# Legacy format packages

`RefurbishedDinosaurs.LegacyFormats` contains format knowledge that is independent of a
specific game and defensively rejects truncated, excessive, or out-of-bounds data.

- `RawIndexedImageDecoder` and `PcxDecoder` produce indexed images and RGBA buffers.
- `Rle8BitmapDecoder` normalizes BI_RLE8/BI_RGB payloads into uncompressed 8-bit BMPs.
- Smacker now lives in `RefurbishedDinosaurs.Media.Smacker`. It includes bounded container/video
  decoding and packed 8/16-bit mono/stereo audio. AVI and FLI have separate optional libraries.
  See the [runtime package reference](../packages/dotnet/README.md#media-packages).
- `CueSheet` parses track/index boundaries, `CddaWave` wraps raw CD audio as PCM WAV,
  and `RawMode1Image`/`Iso9660` provide bounded access to Mode 1 disc images.
- `OriginalContentSource` reads an installed directory, an ISO 9660 image, or the data track
  of a cue/bin image through one interface. `CueBinSheet` checks the sheet first: one `BINARY`
  `FILE` by a safe relative path, a `MODE1/2352` first track at `00:00:00` followed only by audio
  tracks, and every index in order and inside the BIN. A `FILE`, `TRACK` or `INDEX` line it cannot
  read fails rather than being skipped, and a sheet found for a given `.bin` must not name a
  different BIN that is present. The data track ends at the second track's `INDEX 00`
  when it declares a pregap, and every raw sector read must carry the MODE1 sync pattern and
  mode byte, so a MODE2 image or a BIN that does not match its sheet fails as such.

These codecs were moved intact from validated restoration code, then renamed and
packaged. Continue using synthetic fixtures in this repository. Any game-specific
container semantics, role names, heuristics, or source fingerprints remain downstream.
