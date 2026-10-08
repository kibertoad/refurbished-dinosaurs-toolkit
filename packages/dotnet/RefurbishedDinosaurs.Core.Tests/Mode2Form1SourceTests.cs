using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

// Cue/bin images whose data track is MODE2/2352, read as CD-XA Form 1.
public sealed class Mode2Form1SourceTests
{
    private const int CookedSector = 2048;
    private const int RawSector = CueBinSheet.RawSectorSize;
    private const int DescriptorSector = 16;
    private const int PayloadSector = OriginalContentSourceTests.IsoPayloadSector;
    private const int IsoSectors = OriginalContentSourceTests.IsoSectors;
    private const byte DataSubmode = 0x08;
    private const byte Form2Submode = 0x20;
    private const string Mode1Cue = "FILE \"game.bin\" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n";
    private const string Mode2Cue = "FILE \"game.bin\" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:00:00\n";

    [Fact]
    public async Task Mode2Form1AndMode1ImagesOfOneVolumeReadTheSame()
    {
        var payload = new byte[] { 6, 1, 8, 0, 3 };
        var iso = OriginalContentSourceTests.BuildIso(payload);
        var mode1 = CreateTemporaryDirectory();
        var mode2 = CreateTemporaryDirectory();
        try
        {
            await WriteAsync(mode1, CueBinSourceTests.ToRaw(iso), Mode1Cue);
            await WriteAsync(mode2, ToMode2Form1(iso), Mode2Cue);
            using var expected = OriginalContentSource.OpenCueBin(mode1);
            using var actual = OriginalContentSource.OpenCueBin(mode2);

            Assert.Equal("MODE2/2352", actual.Cue?.Tracks[0].Type);
            Assert.Equal(ContentSourceKinds.CueBin, actual.Kind);
            Assert.Equal(expected.Label, actual.Label);
            Assert.Equal(expected.VolumeBlocks, actual.VolumeBlocks);
            Assert.Equal(expected.Files, actual.Files);
            Assert.Equal(new ContentSourceEntry("EI/TEST.BIN", payload.Length), Assert.Single(actual.Files));
            Assert.Equal(payload, await ReadAllAsync(actual.OpenRead("ei/test.bin")));
            var volume = await ReadAllAsync(actual.OpenVolume());
            Assert.Equal(iso, volume);
            Assert.Equal(await ReadAllAsync(expected.OpenVolume()), volume);
        }
        finally
        {
            Directory.Delete(mode1, true);
            Directory.Delete(mode2, true);
        }
    }

    [Fact]
    public async Task ReadsThatCrossASectorBoundaryJoinTheUserDataOfBothSectors()
    {
        var iso = OriginalContentSourceTests.BuildIso([1]);
        var root = CreateTemporaryDirectory();
        try
        {
            await WriteAsync(root, ToMode2Form1(iso), Mode2Cue);
            using var source = OriginalContentSource.OpenCueBin(root);
            await using var volume = source.OpenVolume();
            // From 10 bytes before the descriptor sector to 10 bytes into the one after it: three
            // sectors' user data, none of their subheaders, EDC or ECC.
            var start = DescriptorSector * CookedSector - 10;
            volume.Position = start;
            var actual = new byte[CookedSector + 20];
            await volume.ReadExactlyAsync(actual, TestContext.Current.CancellationToken);
            Assert.Equal(iso.AsSpan(start, actual.Length).ToArray(), actual);

            volume.Position = (DescriptorSector + 1) * CookedSector - 1;
            Assert.Equal(iso[(DescriptorSector + 1) * CookedSector - 1], volume.ReadByte());
            Assert.Equal(iso[(DescriptorSector + 1) * CookedSector], volume.ReadByte());
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task ATruncatedImageIsRejected()
    {
        var image = ToMode2Form1(OriginalContentSourceTests.BuildIso([1]));
        var root = CreateTemporaryDirectory();
        try
        {
            // A partial last sector fails the sheet's length check.
            await WriteAsync(root, image[..^1], Mode2Cue);
            Assert.Contains("multiple of 2352", Assert.Throws<InvalidDataException>(
                () => OriginalContentSource.OpenCueBin(root)).Message, StringComparison.Ordinal);

            // Whole sectors that stop short of the declared volume fail the volume size check.
            await WriteAsync(root, image[..^RawSector], Mode2Cue);
            Assert.Contains("declared volume exceeds the image", Assert.Throws<InvalidDataException>(
                () => OriginalContentSource.OpenCueBin(root)).Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData("sync", "has no MODE2/2352 sync pattern")]
    [InlineData("mode 1", "is mode 1, but the cue sheet declares MODE2/2352")]
    [InlineData("mode 0", "is mode 0, but the cue sheet declares MODE2/2352")]
    [InlineData("subheader copies", "its two subheader copies differ")]
    [InlineData("form 2", "is a MODE2 Form 2 sector")]
    public async Task ADescriptorSectorOutsideTheForm1LayoutFailsTheOpen(string damage, string message)
    {
        var image = ToMode2Form1(OriginalContentSourceTests.BuildIso([1]));
        Damage(image, DescriptorSector, damage);
        var root = CreateTemporaryDirectory();
        try
        {
            await WriteAsync(root, image, Mode2Cue);
            var failure = Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(root));
            Assert.Contains($"Sector {DescriptorSector} ", failure.Message, StringComparison.Ordinal);
            Assert.Contains(message, failure.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData("sync", "has no MODE2/2352 sync pattern")]
    [InlineData("mode 1", "is mode 1, but the cue sheet declares MODE2/2352")]
    [InlineData("subheader copies", "its two subheader copies differ")]
    [InlineData("form 2", "is a MODE2 Form 2 sector")]
    public async Task AFileSectorOutsideTheForm1LayoutFailsTheRead(string damage, string message)
    {
        var image = ToMode2Form1(OriginalContentSourceTests.BuildIso([4, 4]));
        Damage(image, PayloadSector, damage);
        var root = CreateTemporaryDirectory();
        try
        {
            await WriteAsync(root, image, Mode2Cue);
            // Opening reads only the descriptors and directories, so the file is still listed, and
            // reading it or the whole volume stops at its sector.
            using var source = OriginalContentSource.OpenCueBin(root);
            Assert.Equal("EI/TEST.BIN", Assert.Single(source.Files).Path);
            foreach (var open in new Func<Stream>[] { () => source.OpenRead("EI/TEST.BIN"), source.OpenVolume })
            {
                await using var stream = open();
                var failure = await Assert.ThrowsAsync<InvalidDataException>(
                    () => stream.CopyToAsync(Stream.Null, TestContext.Current.CancellationToken));
                Assert.Contains($"Sector {PayloadSector} ", failure.Message, StringComparison.Ordinal);
                Assert.Contains(message, failure.Message, StringComparison.Ordinal);
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task AMode1SheetRejectsMode2Form1Sectors()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            await WriteAsync(root, ToMode2Form1(OriginalContentSourceTests.BuildIso([1])), Mode1Cue);
            var failure = Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(root));
            Assert.Contains("is mode 2, but the cue sheet declares MODE1/2352", failure.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task ExtentsAndTheDataTrackStayInsideTheirBounds()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            // TEST.BIN's record, after the "." and ".." records of the EI directory, points past the volume.
            var iso = OriginalContentSourceTests.BuildIso([1]);
            OriginalContentSourceTests.WriteBothEndianUInt32(
                iso.AsSpan(OriginalContentSourceTests.IsoGameDirectorySector * CookedSector), 68 + 2, IsoSectors);
            await WriteAsync(root, ToMode2Form1(iso), Mode2Cue);
            Assert.Contains("outside the declared volume", Assert.Throws<InvalidDataException>(
                () => OriginalContentSource.OpenCueBin(root)).Message, StringComparison.Ordinal);

            // A second track starting at sector 10 leaves no room for the volume descriptor.
            var image = ToMode2Form1(OriginalContentSourceTests.BuildIso([1]));
            await WriteAsync(root, image, Mode2Cue + "TRACK 02 AUDIO\nINDEX 01 00:00:10\n");
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(root));

            // A second track starting past the image.
            await WriteAsync(root, image, Mode2Cue + "TRACK 02 AUDIO\nINDEX 01 01:00:00\n");
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(root));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task TheDataTrackEndsWhereTheAudioPregapBegins()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var payload = new byte[] { 9, 9 };
            var image = ToMode2Form1(OriginalContentSourceTests.BuildIso(payload));
            // The pregap and audio are zero-filled, which no MODE2 sector check would pass.
            Array.Resize(ref image, (IsoSectors + 150 + 75) * RawSector);
            await WriteAsync(root, image,
                Mode2Cue + $"TRACK 02 AUDIO\nINDEX 00 00:00:{IsoSectors}\nINDEX 01 00:02:{IsoSectors}\n");
            using var source = OriginalContentSource.OpenCueBin(root);
            Assert.Equal(IsoSectors, source.Cue?.DataTrackSectors);
            Assert.Equal(payload, await ReadAllAsync(source.OpenRead("EI/TEST.BIN")));
            Assert.Equal(IsoSectors * CookedSector, (await ReadAllAsync(source.OpenVolume())).Length);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task StreamsOwnTheirFileAndOutliveTheSource()
    {
        var payload = new byte[] { 2, 7, 1 };
        var root = CreateTemporaryDirectory();
        try
        {
            await WriteAsync(root, ToMode2Form1(OriginalContentSourceTests.BuildIso(payload)), Mode2Cue);
            Stream file;
            Stream volume;
            using (var source = OriginalContentSource.OpenCueBin(root))
            {
                // Disposing one stream leaves the source and the others readable.
                await source.OpenRead("EI/TEST.BIN").DisposeAsync();
                file = source.OpenRead("EI/TEST.BIN");
                volume = source.OpenVolume();
            }
            await using (file) Assert.Equal(payload, await ReadAllAsync(file));
            await using (volume) Assert.Equal(IsoSectors * CookedSector, (await ReadAllAsync(volume)).Length);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void TheParserAcceptsAMode2DataTrackFollowedByAudio()
    {
        var sheet = CueBinSheet.Parse(
            "FILE game.bin BINARY\nTRACK 01 mode2/2352\nINDEX 01 00:00:00\nTRACK 02 AUDIO\nINDEX 00 00:30:00\nINDEX 01 00:32:00\n");
        Assert.Equal("MODE2/2352", sheet.Tracks[0].Type);
        Assert.Equal(2250, sheet.DataTrackSectors);
        Assert.Contains("MODE1/2352 or MODE2/2352", Assert.Throws<InvalidDataException>(() =>
            CueBinSheet.Parse("FILE a.bin BINARY\nTRACK 01 MODE2/2336\nINDEX 01 00:00:00\n")).Message, StringComparison.Ordinal);
    }

    // Wraps each 2048-byte sector in the MODE2/2352 sync pattern, address, mode byte 2 and a CD-XA
    // Form 1 subheader written twice. The EDC and ECC are left zero, since the reader does not check them.
    internal static byte[] ToMode2Form1(byte[] cooked)
    {
        var raw = CueBinSourceTests.ToRaw(cooked);
        for (var lba = 0; lba < cooked.Length / CookedSector; lba++)
        {
            var sector = raw.AsSpan(lba * RawSector, RawSector);
            sector[15] = 2;
            sector[16..2072].Clear();
            byte[] subheader = [0, 0, DataSubmode, 0];
            subheader.CopyTo(sector[16..]);
            subheader.CopyTo(sector[20..]);
            cooked.AsSpan(lba * CookedSector, CookedSector).CopyTo(sector[24..]);
        }
        return raw;
    }

    private static void Damage(byte[] image, int lba, string damage)
    {
        var sector = image.AsSpan(lba * RawSector, RawSector);
        switch (damage)
        {
            case "sync": sector[5] = 0; break;
            case "mode 1": sector[15] = 1; break;
            case "mode 0": sector[15] = 0; break;
            case "subheader copies": sector[20 + 2] ^= 0x01; break;
            case "form 2": sector[18] |= Form2Submode; sector[22] |= Form2Submode; break;
            default: throw new ArgumentOutOfRangeException(nameof(damage));
        }
    }

    private static async Task<byte[]> ReadAllAsync(Stream stream)
    {
        await using (stream)
        {
            using var copy = new MemoryStream();
            await stream.CopyToAsync(copy, TestContext.Current.CancellationToken);
            return copy.ToArray();
        }
    }

    private static async Task WriteAsync(string root, byte[] bin, string cue)
    {
        await File.WriteAllBytesAsync(Path.Combine(root, "game.bin"), bin, TestContext.Current.CancellationToken);
        await File.WriteAllTextAsync(Path.Combine(root, "game.cue"), cue, TestContext.Current.CancellationToken);
    }

    private static string CreateTemporaryDirectory()
    {
        var root = Path.Combine(Path.GetTempPath(), "mode2-form1-source-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }
}
