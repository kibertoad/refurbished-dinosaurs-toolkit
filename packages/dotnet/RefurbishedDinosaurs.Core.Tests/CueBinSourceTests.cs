using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class CueBinSourceTests
{
    private const int CookedSector = 2048;
    private const int RawSector = CueBinSheet.RawSectorSize;
    private const string SingleTrackCue = "FILE \"game.bin\" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n";

    [Fact]
    public async Task CueBinSourceListsAndReadsTheDataTrack()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var payload = new byte[] { 1, 2, 3, 4 };
            await WriteAsync(root, ToRaw(OriginalContentSourceTests.BuildIso(payload)), SingleTrackCue);
            var cue = Path.Combine(root, "game.cue");

            foreach (var open in new Func<OriginalContentSource>[]
            {
                () => OriginalContentSource.Open(cue),
                () => OriginalContentSource.Open(cue, ContentSourceKinds.CueBin),
                () => OriginalContentSource.OpenCueBin(Path.Combine(root, "game.bin")),
                () => OriginalContentSource.OpenCueBin(root),
            })
            {
                using var source = open();
                Assert.Equal(ContentSourceKinds.CueBin, source.Kind);
                Assert.Equal("SYNTHETIC_EI", source.Label);
                Assert.Equal("game.bin", source.Cue?.ReferencedFile);
                Assert.Equal(Path.GetFullPath(cue), source.CuePath);
                Assert.Equal(Path.GetFullPath(Path.Combine(root, "game.bin")), source.BinPath);
                Assert.Equal(new ContentSourceEntry("EI/TEST.BIN", payload.Length), Assert.Single(source.Files));
                await using var stream = source.OpenRead("ei/test.bin");
                var actual = new byte[payload.Length];
                await stream.ReadExactlyAsync(actual, TestContext.Current.CancellationToken);
                Assert.Equal(payload, actual);
                Assert.Equal(0, await stream.ReadAsync(new byte[1], TestContext.Current.CancellationToken));
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task DataTrackEndsAtTheFollowingPregap()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            // 23 data sectors, a 150-sector pregap, then a second of audio.
            var image = ToRaw(OriginalContentSourceTests.BuildIso([7]));
            Array.Resize(ref image, (23 + 150 + 75) * RawSector);
            string[] lines = ["FILE \"game.bin\" BINARY", "  TRACK 01 MODE1/2352", "    INDEX 01 00:00:00",
                "  TRACK 02 AUDIO", "    INDEX 00 00:00:23", "    INDEX 01 00:02:23", ""];
            await WriteAsync(root, image, string.Join('\n', lines));

            var sheet = CueBinSheet.Load(Path.Combine(root, "game.cue"));
            Assert.Equal(23, sheet.DataTrackSectors);
            Assert.Equal(23, CueSheet.DataTrackSectors(lines));
            Assert.Equal(23, CueSheet.Tracks(lines)[1].PregapSector);
            Assert.Equal(173, CueSheet.Tracks(lines)[1].StartSector);
            using var source = OriginalContentSource.OpenCueBin(root);
            Assert.Equal("EI/TEST.BIN", Assert.Single(source.Files).Path);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task DataTrackMayRunPastTheVolumeIntoRecordsWithoutUserData()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var payload = new byte[] { 5, 3, 9 };
            await WriteAsync(root, WithRecordsPastTheVolume(OriginalContentSourceTests.BuildIso(payload)),
                PastVolumeCue);
            using var source = OriginalContentSource.OpenCueBin(root);
            Assert.Equal(PastVolumeDataTrack, source.Cue?.DataTrackSectors);
            Assert.Equal(IsoSectors, source.VolumeBlocks);
            Assert.Equal(new ContentSourceEntry("EI/TEST.BIN", payload.Length), Assert.Single(source.Files));
            await using (var stream = source.OpenRead("EI/TEST.BIN"))
            {
                var actual = new byte[payload.Length];
                await stream.ReadExactlyAsync(actual, TestContext.Current.CancellationToken);
                Assert.Equal(payload, actual);
            }
            // The volume stops at its declared size, so the records past it are never read.
            await using (var volume = source.OpenVolume())
            {
                Assert.Equal(IsoSectors * CookedSector, volume.Length);
                await volume.CopyToAsync(Stream.Null, TestContext.Current.CancellationToken);
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task StoredSectorAddressesAreNotComparedWithThePhysicalPosition()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var payload = new byte[] { 2, 4 };
            var image = WithRecordsPastTheVolume(OriginalContentSourceTests.BuildIso(payload));
            // The payload sector stores the address of sector 0. Only the sync pattern and mode are checked.
            image[PayloadSector * RawSector + 12] = 0x00;
            image[PayloadSector * RawSector + 13] = 0x02;
            image[PayloadSector * RawSector + 14] = 0x00;
            await WriteAsync(root, image, PastVolumeCue);
            using var source = OriginalContentSource.OpenCueBin(root);
            await using var stream = source.OpenRead("EI/TEST.BIN");
            var actual = new byte[payload.Length];
            await stream.ReadExactlyAsync(actual, TestContext.Current.CancellationToken);
            Assert.Equal(payload, actual);
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData(RootDirectorySector)] // the EI directory's record in the root directory
    [InlineData(GameDirectorySector)] // TEST.BIN's record in the EI directory
    public async Task ExtentsIntoTheRecordsPastTheVolumeStillFail(int directorySector)
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var iso = OriginalContentSourceTests.BuildIso([1]);
            // Both records follow the "." and ".." records, 34 bytes each; the extent sits 2 bytes in.
            // Sector 24 holds a repeated record and sector 27 a zero-filled one, both inside the data track.
            foreach (var extent in new uint[] { 24, 27 })
            {
                OriginalContentSourceTests.WriteBothEndianUInt32(
                    iso.AsSpan(directorySector * CookedSector), 68 + 2, extent);
                await WriteAsync(root, WithRecordsPastTheVolume(iso), PastVolumeCue);
                var failure = Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(root));
                Assert.Contains("outside the declared volume", failure.Message, StringComparison.Ordinal);
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task AnExtentInsideTheVolumeOnAZeroFilledRecordFailsWithItsSector()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var image = WithRecordsPastTheVolume(OriginalContentSourceTests.BuildIso([1, 2, 3]));
            image.AsSpan(PayloadSector * RawSector, RawSector).Clear();
            await WriteAsync(root, image, PastVolumeCue);
            // Opening reads only the descriptors and directories, so the zero-filled payload sector
            // surfaces when the file or the volume is read.
            using var source = OriginalContentSource.OpenCueBin(root);
            foreach (var open in new Func<Stream>[] { () => source.OpenRead("EI/TEST.BIN"), source.OpenVolume })
            {
                await using var stream = open();
                var failure = await Assert.ThrowsAsync<InvalidDataException>(
                    () => stream.CopyToAsync(Stream.Null, TestContext.Current.CancellationToken));
                Assert.Contains($"Sector {PayloadSector} has no MODE1/2352 sync pattern", failure.Message,
                    StringComparison.Ordinal);
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task CueBinSourceRejectsSectorsThatAreNotMode1()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var image = ToRaw(OriginalContentSourceTests.BuildIso([1]));
            image[16 * RawSector + 15] = 2; // the primary volume descriptor's sector claims MODE2
            await WriteAsync(root, image, SingleTrackCue);
            var failure = Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(root));
            Assert.Contains("MODE1/2352", failure.Message, StringComparison.Ordinal);

            image[16 * RawSector + 15] = 1;
            image[16 * RawSector + 1] = 0; // broken sync pattern
            await WriteAsync(root, image, SingleTrackCue);
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(root));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task CueBinSourceRejectsImagesThatDoNotFitTheSheet()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var image = ToRaw(OriginalContentSourceTests.BuildIso([1]));
            await WriteAsync(root, image[..^1], SingleTrackCue);
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(root));

            await WriteAsync(root, image, SingleTrackCue + "TRACK 02 AUDIO\nINDEX 01 01:00:00\n");
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(root));

            // A second track starting at sector 10 leaves no room for the volume descriptor.
            await WriteAsync(root, image, SingleTrackCue + "TRACK 02 AUDIO\nINDEX 01 00:00:10\n");
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(root));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task CueBinSourceRejectsAmbiguousOrMissingFiles()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            await File.WriteAllTextAsync(Path.Combine(root, "one.cue"), SingleTrackCue, TestContext.Current.CancellationToken);
            await File.WriteAllTextAsync(Path.Combine(root, "two.cue"), SingleTrackCue, TestContext.Current.CancellationToken);
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(root));
            File.Delete(Path.Combine(root, "two.cue"));
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(root)); // no BIN at all
            Assert.Throws<FileNotFoundException>(() => OriginalContentSource.OpenCueBin(Path.Combine(root, "missing")));
            await File.WriteAllTextAsync(Path.Combine(root, "game.txt"), "", TestContext.Current.CancellationToken);
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(Path.Combine(root, "game.txt")));
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData("FILE \"../game.bin\" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n")]
    [InlineData("FILE \"/game.bin\" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n")]
    [InlineData("FILE \"C:game.bin\" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n")]
    [InlineData("FILE \"CON.bin\" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n")]
    [InlineData("FILE a.bin BINARY\nFILE b.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n")]
    [InlineData("TRACK 01 MODE1/2352\nINDEX 01 00:00:00\n")]
    [InlineData("FILE a.bin BINARY\nINDEX 01 00:00:00\nTRACK 01 MODE1/2352\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:00:00\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:60:00\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:75\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\nINDEX 01 00:00:01\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\nTRACK 03 AUDIO\nINDEX 01 00:01:00\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\nTRACK 02 MODE1/2352\nINDEX 01 00:01:00\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\nTRACK 02 AUDIO\nINDEX 01 00:02:00\nTRACK 03 AUDIO\nINDEX 01 00:01:00\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 00 00:00:00\nINDEX 01 00:02:00\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\nINDEX 02 00:05:00\nTRACK 02 AUDIO\nINDEX 00 00:03:00\nINDEX 01 00:06:00\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\nTRACK 02 AUDIO\nINDEX 01 00:04:00\nINDEX 02 00:03:00\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\nFILE track 2.bin BINARY\nTRACK 02 AUDIO\nINDEX 01 00:01:00\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\nTRACK 02 AUDIO COPY\nINDEX 02 00:01:00\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\nINDEX 02 00:01\n")]
    [InlineData("FILE a.wav WAVE\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n")]
    [InlineData("FILE a.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\nTRACK 02 AUDIO\nINDEX 00 00:04:00\nINDEX 01 00:02:00\n")]
    public void CueParserRejectsUnsupportedSheets(string text) =>
        Assert.Throws<InvalidDataException>(() => CueBinSheet.Parse(text));

    [Fact]
    public void CueParserNamesTheRuleAFileReferenceBreaks() =>
        Assert.Contains("'?'", Assert.Throws<InvalidDataException>(() =>
            CueBinSheet.Parse("FILE \"a?.bin\" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n")).Message);

    [Fact]
    public void CueParserKeepsEveryIndex()
    {
        var sheet = CueBinSheet.Parse(
            "REM comment\r\nFILE \"Game Disc.bin\" BINARY\r\n  TRACK 01 mode1/2352\r\n    INDEX 01 00:00:00\r\n" +
            "  TRACK 02 AUDIO\r\n    INDEX 00 00:30:00\r\n    INDEX 01 00:32:00\r\n");
        Assert.Equal("Game Disc.bin", sheet.ReferencedFile);
        Assert.Equal("MODE1/2352", sheet.Tracks[0].Type);
        Assert.Equal(2250, sheet.Tracks[1].Indices[0]);
        Assert.Equal(2400, sheet.Tracks[1].Indices[1]);
        Assert.Equal(2250, sheet.DataTrackSectors);
        Assert.Null(CueBinSheet.Parse(SingleTrackCue).DataTrackSectors);
        Assert.Throws<InvalidDataException>(() => CueBinSheet.Parse(new string(' ', CueBinSheet.MaximumCueLength + 1)));
    }

    [Fact]
    public void CueParserAcceptsADataTrackWithAZeroLengthPregap()
    {
        var sheet = CueBinSheet.Parse(
            "FILE game.bin binary\nTRACK 01 MODE1/2352\nINDEX 00 00:00:00\nINDEX 01 00:00:00\nPREGAP 00:02:00\n");
        Assert.Equal(0, sheet.Tracks[0].Indices[0]);
    }

    [Fact]
    public async Task CueBinSourceRejectsASheetThatDescribesAnotherBin()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var image = ToRaw(OriginalContentSourceTests.BuildIso([1]));
            await File.WriteAllBytesAsync(Path.Combine(root, "disc1.bin"), image, TestContext.Current.CancellationToken);
            await File.WriteAllBytesAsync(Path.Combine(root, "disc2.bin"), image, TestContext.Current.CancellationToken);
            await File.WriteAllTextAsync(Path.Combine(root, "disc2.cue"),
                SingleTrackCue.Replace("game.bin", "disc2.bin", StringComparison.Ordinal), TestContext.Current.CancellationToken);

            var failure = Assert.Throws<InvalidDataException>(
                () => OriginalContentSource.OpenCueBin(Path.Combine(root, "disc1.bin")));
            Assert.Contains("disc2.bin", failure.Message, StringComparison.Ordinal);
            using var source = OriginalContentSource.OpenCueBin(Path.Combine(root, "disc2.bin"));
            Assert.Equal("disc2.bin", source.Cue?.ReferencedFile);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task CueBinSourceReportsTheFilesItChose()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            // The sheet names a BIN with another stem, and a decoy BIN shares the sheet's stem: the
            // source reads the named one and reports it, for a directory, a .cue or the named .bin input.
            var payload = new byte[] { 4, 5, 6 };
            await File.WriteAllBytesAsync(Path.Combine(root, "track.bin"),
                ToRaw(OriginalContentSourceTests.BuildIso(payload)), TestContext.Current.CancellationToken);
            await File.WriteAllBytesAsync(Path.Combine(root, "disc.bin"),
                ToRaw(OriginalContentSourceTests.BuildIso([9, 9, 9])), TestContext.Current.CancellationToken);
            await File.WriteAllTextAsync(Path.Combine(root, "disc.cue"),
                SingleTrackCue.Replace("game.bin", "track.bin", StringComparison.Ordinal), TestContext.Current.CancellationToken);
            var cue = Path.GetFullPath(Path.Combine(root, "disc.cue"));
            var bin = Path.GetFullPath(Path.Combine(root, "track.bin"));

            foreach (var input in new[] { root, cue, bin })
            {
                using var source = OriginalContentSource.OpenCueBin(input);
                Assert.Equal(cue, source.CuePath);
                Assert.Equal(bin, source.BinPath);
                await using var stream = source.OpenRead("EI/TEST.BIN");
                var actual = new byte[payload.Length];
                await stream.ReadExactlyAsync(actual, TestContext.Current.CancellationToken);
                Assert.Equal(payload, actual);
            }

            // Given the decoy, the sheet found by its stem names another BIN that is present.
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(Path.Combine(root, "disc.bin")));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task CueBinSourceReportsTheBinNameAsTheDirectoryListsIt()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            // The sheet spells the BIN in upper case. Where the file system ignores case the sheet's
            // FILE finds it, and elsewhere the shared stem does; either way the path names the file.
            await WriteAsync(root, ToRaw(OriginalContentSourceTests.BuildIso([1])),
                SingleTrackCue.Replace("game.bin", "GAME.BIN", StringComparison.Ordinal));
            using var source = OriginalContentSource.OpenCueBin(Path.Combine(root, "game.cue"));
            Assert.Equal(Path.GetFullPath(Path.Combine(root, "game.bin")), source.BinPath);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void OnlyCueBinSourcesReportCueAndBinPaths()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            File.WriteAllBytes(Path.Combine(root, "game.iso"), OriginalContentSourceTests.BuildIso([1]));
            var setup = Path.Combine(root, "setup");
            Directory.CreateDirectory(setup);
            SyntheticInstallShieldCabinet.WriteTo(setup,
                SyntheticInstallShieldCabinet.Build(6, [new CabinetFile("", "a.dat", [1, 2, 3])]));
            using var directory = OriginalContentSource.OpenDirectory(root);
            using var iso = OriginalContentSource.OpenIso9660(Path.Combine(root, "game.iso"));
            using var cabinet = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(setup, "data1.hdr"));
            foreach (var source in new[] { directory, iso, cabinet })
            {
                Assert.Null(source.CuePath);
                Assert.Null(source.CueSheetBytes);
                Assert.Null(source.BinPath);
                Assert.Throws<NotSupportedException>(source.OpenBin);
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task CueBinSourceKeepsTheSheetItParsed()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            await WriteAsync(root, ToRaw(OriginalContentSourceTests.BuildIso([1])), SingleTrackCue);
            var cue = Path.Combine(root, "game.cue");
            var parsed = await File.ReadAllBytesAsync(cue, TestContext.Current.CancellationToken);
            using var source = OriginalContentSource.OpenCueBin(cue);

            // The sheet on disk is replaced after the source opened; the bytes it parsed stay with it.
            var replacement = "REM replaced\n" + SingleTrackCue;
            await File.WriteAllTextAsync(cue, replacement, TestContext.Current.CancellationToken);
            var bytes = Assert.NotNull(source.CueSheetBytes);
            Assert.Equal(parsed, bytes.ToArray());
            Assert.Equal(FileFingerprint.Xxh3(parsed), FileFingerprint.Xxh3(bytes.Span));
            Assert.NotEqual(FileFingerprint.Xxh3(cue), FileFingerprint.Xxh3(bytes.Span));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task CueBinSourceReadsAnUnchangedBinAgain()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var payload = new byte[] { 3, 1, 4 };
            var image = ToRaw(OriginalContentSourceTests.BuildIso(payload));
            await WriteAsync(root, image, SingleTrackCue);
            using var source = OriginalContentSource.OpenCueBin(root);
            for (var attempt = 0; attempt < 2; attempt++)
            {
                await using (var stream = source.OpenRead("EI/TEST.BIN"))
                {
                    var actual = new byte[payload.Length];
                    await stream.ReadExactlyAsync(actual, TestContext.Current.CancellationToken);
                    Assert.Equal(payload, actual);
                }
                await using (var volume = source.OpenVolume())
                    Assert.Equal(source.VolumeBlocks * 2048, volume.Length);
                await using (var bin = source.OpenBin())
                {
                    var actual = new byte[image.Length];
                    await bin.ReadExactlyAsync(actual, TestContext.Current.CancellationToken);
                    Assert.Equal(image, actual);
                    Assert.Equal(0, await bin.ReadAsync(new byte[1], TestContext.Current.CancellationToken));
                }
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData("truncated")]
    [InlineData("grown")]
    [InlineData("rewritten")]
    public async Task CueBinSourceRefusesABinThatChangedAfterItOpened(string change)
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var image = ToRaw(OriginalContentSourceTests.BuildIso([2, 7]));
            await WriteAsync(root, image, SingleTrackCue);
            var binPath = Path.Combine(root, "game.bin");
            var written = File.GetLastWriteTimeUtc(binPath);
            using var source = OriginalContentSource.OpenCueBin(root);

            var replacement = image.ToArray();
            if (change == "truncated") Array.Resize(ref replacement, image.Length - RawSector);
            else if (change == "grown") Array.Resize(ref replacement, image.Length + RawSector);
            else replacement[^1] ^= 0xFF;
            await File.WriteAllBytesAsync(binPath, replacement, TestContext.Current.CancellationToken);
            // A rewrite of the same length is told apart by its last-write time. Set it apart
            // explicitly so the test does not rely on the file system's timestamp resolution.
            if (change == "rewritten") File.SetLastWriteTimeUtc(binPath, written.AddMinutes(1));

            foreach (var read in new Func<Stream>[]
            {
                () => source.OpenRead("EI/TEST.BIN"),
                source.OpenVolume,
                source.OpenBin,
            })
            {
                var failure = Assert.Throws<IOException>(read);
                Assert.Contains("changed after the source was opened", failure.Message, StringComparison.Ordinal);
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task CueBinSourceReadsAFileOneByteAtATime()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var payload = new byte[] { 9, 8, 7, 6, 5 };
            await WriteAsync(root, ToRaw(OriginalContentSourceTests.BuildIso(payload)), SingleTrackCue);
            using var source = OriginalContentSource.OpenCueBin(root);
            using var stream = source.OpenRead("EI/TEST.BIN");
            var actual = new List<byte>();
            for (var value = stream.ReadByte(); value >= 0; value = stream.ReadByte()) actual.Add((byte)value);
            Assert.Equal(payload, actual);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void OpenByKindRejectsUnknownKindsAndMissingPaths()
    {
        var missing = Path.Combine(Path.GetTempPath(), $"missing-{Guid.NewGuid():N}");
        Assert.Throws<InvalidDataException>(() => OriginalContentSource.Open(missing, "zip"));
        Assert.Throws<FileNotFoundException>(() => OriginalContentSource.Open(missing, ContentSourceKinds.Directory));
        Assert.Throws<FileNotFoundException>(() => OriginalContentSource.Open(missing, ContentSourceKinds.Iso9660));
        Assert.True(ContentSourceKinds.IsSupported("cue-bin"));
        Assert.False(ContentSourceKinds.IsSupported("zip"));
    }

    private static async Task WriteAsync(string root, byte[] bin, string cue)
    {
        await File.WriteAllBytesAsync(Path.Combine(root, "game.bin"), bin, TestContext.Current.CancellationToken);
        await File.WriteAllTextAsync(Path.Combine(root, "game.cue"), cue, TestContext.Current.CancellationToken);
    }

    // Wraps each 2048-byte sector in the MODE1/2352 sync pattern, address and mode header.
    internal static byte[] ToRaw(byte[] cooked)
    {
        var sectors = cooked.Length / CookedSector;
        var raw = new byte[sectors * RawSector];
        for (var lba = 0; lba < sectors; lba++)
        {
            var sector = raw.AsSpan(lba * RawSector, RawSector);
            sector[1..11].Fill(0xFF);
            var address = lba + 150;
            sector[12] = Bcd(address / (60 * 75));
            sector[13] = Bcd(address / 75 % 60);
            sector[14] = Bcd(address % 75);
            sector[15] = 1;
            cooked.AsSpan(lba * CookedSector, CookedSector).CopyTo(sector[16..]);
        }
        return raw;
    }

    // The synthetic volume from BuildIso declares 23 sectors. The data track below runs 7 sectors
    // past it: its last 3 sectors repeated with their stored addresses, then 4 zero-filled records.
    // Track 02's INDEX 00 ends the data track, followed by a pregap and a second of audio.
    private const int IsoSectors = 23;
    private const int RootDirectorySector = 20;
    private const int GameDirectorySector = 21;
    private const int PayloadSector = 22;
    private const int PastVolumeDataTrack = IsoSectors + 3 + 4;
    private const string PastVolumeCue =
        "FILE \"game.bin\" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n" +
        "TRACK 02 AUDIO\nINDEX 00 00:00:30\nINDEX 01 00:02:30\n";

    private static byte[] WithRecordsPastTheVolume(byte[] iso)
    {
        Assert.Equal(IsoSectors * CookedSector, iso.Length);
        var volume = ToRaw(iso);
        var image = new byte[(PastVolumeDataTrack + 150 + 75) * RawSector];
        volume.CopyTo(image, 0);
        volume.AsSpan((IsoSectors - 3) * RawSector, 3 * RawSector).CopyTo(image.AsSpan(IsoSectors * RawSector));
        return image;
    }

    private static byte Bcd(int value) => checked((byte)(((value / 10) << 4) | (value % 10)));

    private static string CreateTemporaryDirectory()
    {
        var root = Path.Combine(Path.GetTempPath(), "cue-bin-source-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }
}
