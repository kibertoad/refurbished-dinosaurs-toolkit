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
    private static byte[] ToRaw(byte[] cooked)
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

    private static byte Bcd(int value) => checked((byte)(((value / 10) << 4) | (value % 10)));

    private static string CreateTemporaryDirectory()
    {
        var root = Path.Combine(Path.GetTempPath(), "cue-bin-source-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }
}
