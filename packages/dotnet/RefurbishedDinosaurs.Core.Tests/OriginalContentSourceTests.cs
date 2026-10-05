using System.Buffers.Binary;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class OriginalContentSourceTests
{
    private const int SectorSize = 2048;

    [Fact]
    public async Task Iso9660SourceListsAndReadsFiles()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var imagePath = Path.Combine(root, "game.iso");
            var expected = new byte[] { 1, 2, 3, 4 };
            await File.WriteAllBytesAsync(imagePath, BuildIso(expected), TestContext.Current.CancellationToken);

            using var source = OriginalContentSource.Open(imagePath);
            Assert.Equal("iso9660", source.Kind);
            Assert.Equal("SYNTHETIC_EI", source.Label);
            var entry = Assert.Single(source.Files);
            Assert.Equal("EI/TEST.BIN", entry.Path);
            Assert.Equal(expected.Length, entry.Size);
            Assert.True(source.TryGetFile(@"ei\test.bin", out var found));
            Assert.Equal(entry, found);
            await using var stream = source.OpenRead("ei/test.bin");
            Assert.Equal(expected.Length, stream.Length);
            var actual = new byte[expected.Length];
            await stream.ReadExactlyAsync(actual, TestContext.Current.CancellationToken);
            Assert.Equal(expected, actual);
            Assert.Equal(0, await stream.ReadAsync(new byte[1], TestContext.Current.CancellationToken));
            Assert.Throws<InvalidDataException>(() => source.TryGetFile("../EI/TEST.BIN", out _));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task Iso9660SourceReadsAnUnchangedImageAgain()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var imagePath = Path.Combine(root, "game.iso");
            var payload = new byte[] { 5, 8 };
            await File.WriteAllBytesAsync(imagePath, BuildIso(payload), TestContext.Current.CancellationToken);
            using var source = OriginalContentSource.OpenIso9660(imagePath);
            for (var attempt = 0; attempt < 2; attempt++)
            {
                await using (var stream = source.OpenRead("EI/TEST.BIN"))
                {
                    var actual = new byte[payload.Length];
                    await stream.ReadExactlyAsync(actual, TestContext.Current.CancellationToken);
                    Assert.Equal(payload, actual);
                }
                await using (var volume = source.OpenVolume())
                    Assert.Equal(source.VolumeBlocks * SectorSize, volume.Length);
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData("truncated")]
    [InlineData("grown")]
    [InlineData("rewritten")]
    public async Task Iso9660SourceRefusesAnImageThatChangedAfterItOpened(string change)
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var imagePath = Path.Combine(root, "game.iso");
            var image = BuildIso([2, 7]);
            await File.WriteAllBytesAsync(imagePath, image, TestContext.Current.CancellationToken);
            var written = File.GetLastWriteTimeUtc(imagePath);
            using var source = OriginalContentSource.OpenIso9660(imagePath);

            var replacement = image.ToArray();
            if (change == "truncated") Array.Resize(ref replacement, image.Length - SectorSize);
            else if (change == "grown") Array.Resize(ref replacement, image.Length + SectorSize);
            else replacement[^1] ^= 0xFF;
            await File.WriteAllBytesAsync(imagePath, replacement, TestContext.Current.CancellationToken);
            // A rewrite of the same length is told apart by its last-write time. Set it apart
            // explicitly so the test does not rely on the file system's timestamp resolution.
            if (change == "rewritten") File.SetLastWriteTimeUtc(imagePath, written.AddMinutes(1));

            foreach (var read in new Func<Stream>[] { () => source.OpenRead("EI/TEST.BIN"), source.OpenVolume })
            {
                var failure = Assert.Throws<IOException>(read);
                Assert.Contains("changed after the source was opened", failure.Message, StringComparison.Ordinal);
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task DirectorySourceListsFilesCaseInsensitively()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            Directory.CreateDirectory(Path.Combine(root, "DATA"));
            await File.WriteAllBytesAsync(Path.Combine(root, "DATA", "MAP.BIN"), [9, 8, 7], TestContext.Current.CancellationToken);

            using var source = OriginalContentSource.Open(root);
            Assert.Equal("directory", source.Kind);
            Assert.Null(source.Label);
            var entry = Assert.Single(source.Files);
            Assert.Equal(new ContentSourceEntry("DATA/MAP.BIN", 3), entry);
            await using var stream = source.OpenRead("data/map.bin");
            Assert.Equal(9, stream.ReadByte());
            Assert.False(source.TryGetFile("DATA/OTHER.BIN", out _));
            Assert.Throws<InvalidDataException>(() => source.TryGetFile("C:DATA/MAP.BIN", out _));
            Assert.Throws<InvalidDataException>(() => source.TryGetFile(" ", out _));
            Assert.Throws<FileNotFoundException>(() => source.OpenRead("DATA/OTHER.BIN"));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public void OpenRejectsMissingSource() =>
        Assert.Throws<FileNotFoundException>(() =>
            OriginalContentSource.Open(Path.Combine(Path.GetTempPath(), $"missing-{Guid.NewGuid():N}")));

    private static string CreateTemporaryDirectory()
    {
        var root = Path.Combine(Path.GetTempPath(), "toad-content-source-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }

    [Fact]
    public async Task Iso9660SourceRejectsDisagreeingEndianFields()
    {
        var path = Path.Combine(Path.GetTempPath(), $"toad-iso-bad-{Guid.NewGuid():N}.iso");
        var image = BuildIso([1]);
        image[(16 * SectorSize) + 156 + 6] ^= 1;
        await File.WriteAllBytesAsync(path, image, TestContext.Current.CancellationToken);
        try
        {
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.Open(path));
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Theory]
    [InlineData(0)]
    [InlineData(1)]
    [InlineData(2)]
    [InlineData(3)]
    public async Task Iso9660SourceRejectsInvalidDeclaredVolume(int caseNumber)
    {
        var path = Path.Combine(Path.GetTempPath(),
            $"toad-iso-volume-{Guid.NewGuid():N}.iso");
        var image = BuildIso([1]);
        var descriptor = image.AsSpan(16 * SectorSize, SectorSize);
        switch (caseNumber)
        {
            case 0:
                descriptor[128 + 2] ^= 1; // disagreeing block-size byte orders
                break;
            case 1:
                WriteBothEndianUInt16(descriptor, 128, 1024);
                break;
            case 2:
                WriteBothEndianUInt32(descriptor, 80, 24); // beyond image
                break;
            case 3:
                WriteBothEndianUInt32(descriptor, 80, 22); // payload outside volume
                break;
        }
        await File.WriteAllBytesAsync(path, image, TestContext.Current.CancellationToken);
        try
        {
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.Open(path));
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Theory]
    [InlineData((byte)':')]
    [InlineData((byte)0x07)]
    [InlineData((byte)0x7F)]
    [InlineData((byte)'<')]
    [InlineData((byte)'>')]
    [InlineData((byte)'"')]
    [InlineData((byte)'|')]
    [InlineData((byte)'?')]
    [InlineData((byte)'*')]
    [InlineData((byte)'\\')]
    public async Task Iso9660SourceRejectsFileNamesUnsafeOnDisk(byte character)
    {
        var path = Path.Combine(Path.GetTempPath(), $"toad-iso-name-{Guid.NewGuid():N}.iso");
        var image = BuildIso([1]);
        image[image.AsSpan().IndexOf("TEST.BIN;1"u8) + 2] = character;
        await File.WriteAllBytesAsync(path, image, TestContext.Current.CancellationToken);
        try
        {
            var message = Assert.Throws<InvalidDataException>(() => OriginalContentSource.Open(path)).Message;
            // The identifier is shown as a JSON string, with its version suffix.
            Assert.Contains("ISO9660 identifier \"TE", message);
        }
        finally { File.Delete(path); }
    }

    [Fact]
    public async Task Iso9660SourceReadsEachNameByteAsTheLatin1CharacterOfTheSameValue()
    {
        var path = Path.Combine(Path.GetTempPath(), $"toad-iso-name-{Guid.NewGuid():N}.iso");
        var image = BuildIso([1]);
        var name = image.AsSpan().IndexOf("TEST.BIN;1"u8);
        // A Windows-1252 letter and a byte that is a letter in DOS code page 437 and a C1 control in Latin-1.
        image[name + 1] = 0xC9;
        image[name + 2] = 0x82;
        await File.WriteAllBytesAsync(path, image, TestContext.Current.CancellationToken);
        try
        {
            using var source = OriginalContentSource.Open(path);
            Assert.Equal("EI/TÉ\u0082T.BIN", Assert.Single(source.Files).Path);
            Assert.True(source.TryGetFile("ei/té\u0082t.bin", out _));
        }
        finally { File.Delete(path); }
    }

    [Fact]
    public async Task Iso9660OnARawTrackListsLatin1NamesAndReadsFiles()
    {
        var path = Path.Combine(Path.GetTempPath(), $"toad-raw-iso-{Guid.NewGuid():N}.bin");
        var cooked = BuildIso([5, 6, 7]);
        var name = cooked.AsSpan().IndexOf("TEST.BIN;1"u8);
        cooked[name + 1] = 0xC9;
        cooked[name + 2] = 0x82;
        await File.WriteAllBytesAsync(path, CueBinSourceTests.ToRaw(cooked), TestContext.Current.CancellationToken);
        try
        {
            using var image = new RawMode1Image(path, cooked.Length / SectorSize);
            var iso = new Iso9660(image);
            var file = Assert.Single(iso.Files);
            Assert.Equal("EI/TÉ\u0082T.BIN", file.Path);
            Assert.Equal(new byte[] { 5, 6, 7 }, iso.ReadFile(file));
        }
        finally { File.Delete(path); }
    }

    [Theory]
    [InlineData((byte)'?')]
    [InlineData((byte)'|')]
    [InlineData((byte)0x01)]
    public async Task Iso9660OnARawTrackRejectsANameThatIsNotPortable(byte character)
    {
        var path = Path.Combine(Path.GetTempPath(), $"toad-raw-iso-{Guid.NewGuid():N}.bin");
        var cooked = BuildIso([1]);
        cooked[cooked.AsSpan().IndexOf("TEST.BIN;1"u8) + 2] = character;
        await File.WriteAllBytesAsync(path, CueBinSourceTests.ToRaw(cooked), TestContext.Current.CancellationToken);
        try
        {
            using var image = new RawMode1Image(path, cooked.Length / SectorSize);
            var message = Assert.Throws<InvalidDataException>(() => new Iso9660(image)).Message;
            Assert.Contains("ISO9660 identifier \"TE", message);
            Assert.Contains("is not a portable name", message);
        }
        finally { File.Delete(path); }
    }

    [Fact]
    public async Task Iso9660OnARawTrackRejectsABinShorterThanTheVolume()
    {
        var path = Path.Combine(Path.GetTempPath(), $"toad-raw-iso-{Guid.NewGuid():N}.bin");
        var cooked = BuildIso([1]);
        var raw = CueBinSourceTests.ToRaw(cooked);
        // The BIN lost its last sector, which holds the file's data, but the track still claims it.
        await File.WriteAllBytesAsync(path, raw[..^2352], TestContext.Current.CancellationToken);
        try
        {
            using var image = new RawMode1Image(path, cooked.Length / SectorSize);
            Assert.Contains("declared volume exceeds the image",
                Assert.Throws<InvalidDataException>(() => new Iso9660(image)).Message);
        }
        finally { File.Delete(path); }
    }

    [Fact]
    public async Task Iso9660OnARawTrackRejectsASectorThatIsNotMode1()
    {
        var path = Path.Combine(Path.GetTempPath(), $"toad-raw-iso-{Guid.NewGuid():N}.bin");
        var cooked = BuildIso([1]);
        var raw = CueBinSourceTests.ToRaw(cooked);
        var fileSector = cooked.Length / SectorSize - 1;
        try
        {
            // A volume descriptor sector marked MODE2 fails the constructor.
            var mode2 = raw.ToArray();
            mode2[16 * 2352 + 15] = 2;
            await File.WriteAllBytesAsync(path, mode2, TestContext.Current.CancellationToken);
            using (var image = new RawMode1Image(path, cooked.Length / SectorSize))
                Assert.Contains("Sector 16 is mode 2",
                    Assert.Throws<InvalidDataException>(() => new Iso9660(image)).Message);

            // A file sector without the sync pattern fails ReadFile.
            var unsynced = raw.ToArray();
            unsynced[fileSector * 2352 + 1] = 0;
            await File.WriteAllBytesAsync(path, unsynced, TestContext.Current.CancellationToken);
            using (var image = new RawMode1Image(path, cooked.Length / SectorSize))
            {
                var iso = new Iso9660(image);
                var file = Assert.Single(iso.Files);
                Assert.Equal(fileSector, (int)file.Extent);
                Assert.Contains($"Sector {fileSector} has no MODE1/2352 sync pattern",
                    Assert.Throws<InvalidDataException>(() => iso.ReadFile(file)).Message);
            }
        }
        finally { File.Delete(path); }
    }

    [Fact]
    public async Task DirectorySourceListsOrdinaryLegacyNames()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            string[] names = ["My Data~1/#1 & 2+!'(x)[y]@z.bin", "Données/ÉCRAN Ü.PCX"];
            foreach (var name in names)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(Path.Combine(root, name))!);
                await File.WriteAllBytesAsync(Path.Combine(root, name), [1], TestContext.Current.CancellationToken);
            }
            using var source = OriginalContentSource.OpenDirectory(root);
            Assert.Equal(names.OrderBy(name => name, StringComparer.OrdinalIgnoreCase), source.Files.Select(entry => entry.Path));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData("bad?name.dat", "'?'")]
    [InlineData("Data/bad|name.dat", "'|'")]
    [InlineData("Data/bad<name.dat", "'<'")]
    [InlineData("Icon\r", "U+000D")]
    [InlineData("CON", "reserved Windows device name")]
    [InlineData("trailing.", "ends with a dot or a space")]
    [InlineData("back\\slash.dat", "'\\'")]
    public async Task DirectorySourceRejectsAFileWhoseNameIsNotPortable(string name, string rule)
    {
        // Windows refuses these names, so only Linux and macOS can hold such a directory.
        if (OperatingSystem.IsWindows()) return;
        var root = CreateTemporaryDirectory();
        try
        {
            Directory.CreateDirectory(Path.Combine(root, "Data"));
            await File.WriteAllBytesAsync(Path.Combine(root, "Data", "fine.dat"), [1], TestContext.Current.CancellationToken);
            await File.WriteAllBytesAsync(Path.Combine(root, name), [2], TestContext.Current.CancellationToken);
            var message = Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenDirectory(root)).Message;
            Assert.Contains("Source file", message);
            Assert.Contains(rule, message);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task Iso9660SourceTrimsNulPaddingFromTheLabel()
    {
        var path = Path.Combine(Path.GetTempPath(), $"toad-iso-label-{Guid.NewGuid():N}.iso");
        var image = BuildIso([1]);
        image.AsSpan(16 * SectorSize + 40 + "SYNTHETIC_EI".Length, 32 - "SYNTHETIC_EI".Length).Clear();
        await File.WriteAllBytesAsync(path, image, TestContext.Current.CancellationToken);
        try
        {
            using var source = OriginalContentSource.Open(path);
            Assert.Equal("SYNTHETIC_EI", source.Label);
        }
        finally { File.Delete(path); }
    }

    [Fact]
    public async Task Iso9660SourceReadsEachLabelByteAsTheLatin1CharacterOfTheSameValue()
    {
        var path = Path.Combine(Path.GetTempPath(), $"toad-iso-label-{Guid.NewGuid():N}.iso");
        var image = BuildIso([1]);
        var field = image.AsSpan(16 * SectorSize + 40, 32);
        field.Fill((byte)' ');
        // A high byte, a C1 control byte, NBSP and 0xFF, then a NUL before the trailing spaces.
        byte[] identifier = [(byte)'A', 0xC9, 0x85, 0xA0, 0xFF, (byte)'Z', 0x00];
        identifier.CopyTo(field);
        await File.WriteAllBytesAsync(path, image, TestContext.Current.CancellationToken);
        try
        {
            using var source = OriginalContentSource.Open(path);
            Assert.Equal("A\u00C9\u0085\u00A0\u00FFZ", source.Label);
        }
        finally { File.Delete(path); }
    }

    internal static byte[] BuildIso(byte[] payload)
    {
        const int rootSector = 20;
        const int gameSector = 21;
        const int payloadSector = 22;
        var image = new byte[23 * SectorSize];

        var pvd = image.AsSpan(16 * SectorSize, SectorSize);
        pvd[0] = 1;
        "CD001"u8.CopyTo(pvd[1..]);
        pvd[6] = 1;
        WritePaddedAscii(pvd[40..72], "SYNTHETIC_EI");
        WriteBothEndianUInt32(pvd, 80, 23);
        WriteBothEndianUInt16(pvd, 128, SectorSize);
        WriteDirectoryRecord(pvd, 156, rootSector, SectorSize, isDirectory: true, [0]);

        var terminator = image.AsSpan(17 * SectorSize, SectorSize);
        terminator[0] = 255;
        "CD001"u8.CopyTo(terminator[1..]);
        terminator[6] = 1;

        var root = image.AsSpan(rootSector * SectorSize, SectorSize);
        var rootOffset = WriteDirectoryRecord(root, 0, rootSector, SectorSize, true, [0]);
        rootOffset += WriteDirectoryRecord(root, rootOffset, rootSector, SectorSize, true, [1]);
        WriteDirectoryRecord(root, rootOffset, gameSector, SectorSize, true, "EI"u8);

        var game = image.AsSpan(gameSector * SectorSize, SectorSize);
        var gameOffset = WriteDirectoryRecord(game, 0, gameSector, SectorSize, true, [0]);
        gameOffset += WriteDirectoryRecord(game, gameOffset, rootSector, SectorSize, true, [1]);
        WriteDirectoryRecord(game, gameOffset, payloadSector, payload.Length, false, "TEST.BIN;1"u8);

        payload.CopyTo(image.AsSpan(payloadSector * SectorSize));
        return image;
    }

    internal static int WriteDirectoryRecord(
        Span<byte> destination, int offset, uint extent, int length, bool isDirectory,
        ReadOnlySpan<byte> identifier)
    {
        var recordLength = 33 + identifier.Length + (identifier.Length % 2 == 0 ? 1 : 0);
        var record = destination.Slice(offset, recordLength);
        record[0] = checked((byte)recordLength);
        WriteBothEndianUInt32(record, 2, extent);
        WriteBothEndianUInt32(record, 10, checked((uint)length));
        record[25] = isDirectory ? (byte)2 : (byte)0;
        WriteBothEndianUInt16(record, 28, 1);
        record[32] = checked((byte)identifier.Length);
        identifier.CopyTo(record[33..]);
        return recordLength;
    }

    internal static void WriteBothEndianUInt32(Span<byte> destination, int offset, uint value)
    {
        BinaryPrimitives.WriteUInt32LittleEndian(destination[offset..], value);
        BinaryPrimitives.WriteUInt32BigEndian(destination[(offset + 4)..], value);
    }

    internal static void WriteBothEndianUInt16(Span<byte> destination, int offset, ushort value)
    {
        BinaryPrimitives.WriteUInt16LittleEndian(destination[offset..], value);
        BinaryPrimitives.WriteUInt16BigEndian(destination[(offset + 2)..], value);
    }

    internal static void WritePaddedAscii(Span<byte> destination, string value)
    {
        destination.Fill((byte)' ');
        System.Text.Encoding.ASCII.GetBytes(value).CopyTo(destination);
    }
}
