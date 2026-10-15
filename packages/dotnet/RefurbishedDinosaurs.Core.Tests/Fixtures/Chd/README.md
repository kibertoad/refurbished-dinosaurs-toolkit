# CHD fixtures

Synthetic CD images compressed by MAME's `chdman`, for `ChdSourceTests`. They hold no game content:
`SyntheticChdDiscs` builds the BIN images, cue sheets and `.iso` they were made from, with a
generated ISO 9660 volume and generated tones, and the tests rebuild those sources to compare each
CHD with the image it came from. The cue sheets and BIN images are not committed.

| File | Made from | chdman options |
|---|---|---|
| `mixed-cdlz.chd` | `mixed.cue`: a `MODE1/2352` data track and two audio tracks, the first with a stored pregap | `-c cdlz` |
| `mixed-cdzl.chd` | `mixed.cue` | `-c cdzl` |
| `mixed-cdfl.chd` | `mixed.cue` | `-c cdfl` |
| `data-mode2.chd` | `data-mode2.cue`: one `MODE2/2352` Form 1 data track with an empty Form 2 sector | the default codecs |
| `data-iso.chd` | `data.iso` | the default codecs |

Made with chdman 0.289 (mame0289), from `mame0289b_x64.exe` of
[MAME's release](https://github.com/mamedev/mame/releases/tag/mame0289) (SHA-256
`a1aa7912168c9d1b05e611906bc21b8b9be3935822aead36d12a1da363150b7d`).

To make them again after changing `SyntheticChdDiscs`, write the sources with the explicit test,
then run chdman on them:

```sh
CHD_FIXTURE_SOURCES=/tmp/chd-sources dotnet run --project packages/dotnet/RefurbishedDinosaurs.Core.Tests -- \
  -method "*WriteFixtureSources" -explicit only
cd /tmp/chd-sources
chdman createcd -i mixed.cue -o mixed-cdlz.chd -c cdlz
chdman createcd -i mixed.cue -o mixed-cdzl.chd -c cdzl
chdman createcd -i mixed.cue -o mixed-cdfl.chd -c cdfl
chdman createcd -i data-mode2.cue -o data-mode2.chd
chdman createcd -i data.iso -o data-iso.chd
```
