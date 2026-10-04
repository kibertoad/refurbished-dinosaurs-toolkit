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
    public async Task Iso9660SourceRejectsFileNamesUnsafeOnDisk(byte character)
    {
        var path = Path.Combine(Path.GetTempPath(), $"toad-iso-name-{Guid.NewGuid():N}.iso");
        var image = BuildIso([1]);
        image[image.AsSpan().IndexOf("TEST.BIN;1"u8) + 2] = character;
        await File.WriteAllBytesAsync(path, image, TestContext.Current.CancellationToken);
        try { Assert.Throws<InvalidDataException>(() => OriginalContentSource.Open(path)); }
        finally { File.Delete(path); }
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

    private static int WriteDirectoryRecord(
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

    private static void WriteBothEndianUInt32(Span<byte> destination, int offset, uint value)
    {
        BinaryPrimitives.WriteUInt32LittleEndian(destination[offset..], value);
        BinaryPrimitives.WriteUInt32BigEndian(destination[(offset + 4)..], value);
    }

    private static void WriteBothEndianUInt16(Span<byte> destination, int offset, ushort value)
    {
        BinaryPrimitives.WriteUInt16LittleEndian(destination[offset..], value);
        BinaryPrimitives.WriteUInt16BigEndian(destination[(offset + 2)..], value);
    }

    private static void WritePaddedAscii(Span<byte> destination, string value)
    {
        destination.Fill((byte)' ');
        System.Text.Encoding.ASCII.GetBytes(value).CopyTo(destination);
    }
}
