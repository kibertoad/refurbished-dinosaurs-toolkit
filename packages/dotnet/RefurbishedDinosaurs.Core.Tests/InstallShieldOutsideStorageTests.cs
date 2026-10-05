using System.Buffers.Binary;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

// Members stored outside the cabinet: as Unshield tells them, a member whose data offset is exactly
// the length of the volume where its data starts, split or not. They are skipped with their own kind, and an
// extent that runs past a volume any other way is still reported as damage.
public sealed class InstallShieldOutsideStorageTests
{
    private static readonly byte[] Noise = Bytes(50_000, 7);

    [Theory]
    [InlineData(0, false)]
    [InlineData(0, true)]
    [InlineData(5, false)]
    [InlineData(5, true)]
    [InlineData(6, false)]
    [InlineData(6, true)]
    public async Task SkipsAMemberStoredOutsideTheCabinetAndOpensTheRest(int major, bool headerInCabinet)
    {
        var root = TemporaryDirectory();
        try
        {
            CabinetFile[] files =
            [
                new("Setup", "outside.bin", Bytes(4000, 1), Outside: true),
                new("Data", "inside.bin", Noise),
                new("Data", "stored-outside.txt", Bytes(300, 2), Compressed: false, Outside: true),
                new("Data", "inside.txt", Bytes(200, 3), Compressed: false)
            ];
            var set = SyntheticInstallShieldCabinet.Build(major, files, headerInCabinet: headerInCabinet);
            SyntheticInstallShieldCabinet.WriteTo(root, set);
            var volumeLength = set["data1.cab"].Length;

            using var source = OriginalContentSource.OpenInstallShieldCabinet(
                Path.Combine(root, headerInCabinet ? "data1.cab" : "data1.hdr"));
            Assert.Equal(["Data/inside.bin", "Data/inside.txt"], source.Files.Select(entry => entry.Path));
            Assert.Equal(["Data/inside.bin", "Data/inside.txt"], source.Members.Select(member => member.Entry.Path));
            Assert.Collection(source.SkippedFiles,
                skipped =>
                {
                    Assert.Equal((0, "Setup/outside.bin", InstallShieldSkippedFileKind.StoredOutsideCabinet),
                        (skipped.Index, skipped.Path, skipped.Kind));
                    Assert.Equal(
                        $"The file is stored outside the cabinet: its data offset, {volumeLength}, is the length of volume 1, which would hold it.",
                        skipped.Reason);
                },
                skipped => Assert.Equal((2, "Data/stored-outside.txt", InstallShieldSkippedFileKind.StoredOutsideCabinet),
                    (skipped.Index, skipped.Path, skipped.Kind)));
            Assert.False(source.TryGetFile("Setup/outside.bin", out _));
            Assert.False(source.TryGetMember("Setup/outside.bin", out _));
            Assert.Throws<FileNotFoundException>(() => source.OpenRead("Setup/outside.bin"));
            await using var stream = source.OpenRead("Data/inside.bin");
            Assert.Equal(Noise, await ReadAll(stream));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public void ReportsAnExtentPastTheVolumeAsDamage(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            var set = SyntheticInstallShieldCabinet.Build(major,
                [new("", "inside.bin", Noise), new("", "outside.bin", Bytes(4000, 1), Outside: true)]);
            var header = set["data1.hdr"];
            var outside = DataOffsetField(header, major, 1);
            var volumeLength = set["data1.cab"].Length;
            SyntheticInstallShieldCabinet.WriteTo(root, set);

            // An offset one past the volume's end, and one inside it whose data runs past the end.
            foreach (var offset in new[] { volumeLength + 1, volumeLength - 1 })
            {
                SetDataOffset(header, major, outside, offset);
                File.WriteAllBytes(Path.Combine(root, "data1.hdr"), header);
                Assert.Contains("lies past the end of volume 1, which is shorter than the header claims",
                    Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"))).Message);
            }
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task SkipsAVersion5MemberStoredOutsideThatTheVolumeHeaderMakesSplit()
    {
        var root = TemporaryDirectory();
        try
        {
            // The outside member is the last one volume 1 records, and the volume's record of its
            // compressed size differs from the descriptor's, so the version 5 rule infers it split.
            // Unshield compares the data offset with the volume's length all the same.
            var first = Bytes(3000, 4);
            var set = SyntheticInstallShieldCabinet.Build(5,
                [new("", "first.bin", first, Compressed: false), new("", "outside.bin", Bytes(4000, 1), Outside: true),
                 new("", "next.bin", Noise)],
                volumeCapacity: first.Length);
            var volume = set["data1.cab"];
            // The last file's compressed size in a version 5 volume header.
            var lastCompressed = BinaryPrimitives.ReadUInt32LittleEndian(volume.AsSpan(56));
            BinaryPrimitives.WriteUInt32LittleEndian(volume.AsSpan(56), lastCompressed - 1);
            SyntheticInstallShieldCabinet.WriteTo(root, set);

            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            var skipped = Assert.Single(source.SkippedFiles);
            Assert.Equal((1, InstallShieldSkippedFileKind.StoredOutsideCabinet), (skipped.Index, skipped.Kind));
            Assert.Contains($"its data offset, {volume.Length}, is the length of volume 1", skipped.Reason);
            Assert.Equal(["first.bin", "next.bin"], source.Files.Select(entry => entry.Path));
            await using var stream = source.OpenRead("next.bin");
            Assert.Equal(Noise, await ReadAll(stream));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public async Task ListsAnEmptyMemberWhoseDataOffsetIsTheVolumeEnd(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            // A member with no stored bytes reads as empty wherever it points, so it is not outside.
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(major,
                [new("", "inside.bin", Noise), new("", "empty.bin", [], Compressed: false)]));
            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            Assert.Empty(source.SkippedFiles);
            await using var stream = source.OpenRead("empty.bin");
            Assert.Empty(await ReadAll(stream));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public void ReadsAVolumeCutAtTheStartOfItsLastMembersDataAsStorageOutside(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            // Unshield's rule cannot tell this cut from a member stored outside: the skip reason gives
            // the offset and volume, so a caller can compare the volume with its source media.
            var set = SyntheticInstallShieldCabinet.Build(major,
                [new("", "first.bin", Noise), new("", "last.bin", Bytes(4000, 1))]);
            SyntheticInstallShieldCabinet.WriteTo(root, set);
            var lastOffset = (int)BinaryPrimitives.ReadUInt32LittleEndian(
                set["data1.hdr"].AsSpan(DataOffsetField(set["data1.hdr"], major, 1)));
            File.WriteAllBytes(Path.Combine(root, "data1.cab"), set["data1.cab"][..lastOffset]);

            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            var skipped = Assert.Single(source.SkippedFiles);
            Assert.Equal((1, InstallShieldSkippedFileKind.StoredOutsideCabinet), (skipped.Index, skipped.Kind));
            Assert.Contains($"its data offset, {lastOffset}, is the length of volume 1", skipped.Reason);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task SkipsVersion6EntriesThatShareOrLinkToDataStoredOutside()
    {
        var root = TemporaryDirectory();
        try
        {
            var outside = Bytes(4000, 1);
            CabinetFile[] files =
            [
                new("", "outside.bin", outside, Outside: true),
                new("", "outside.bin", outside, LinkTo: 0),
                new("Other", "link.bin", outside, LinkTo: 0),
                new("", "inside.bin", Noise)
            ];
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(6, files));
            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            Assert.Equal("inside.bin", Assert.Single(source.Files).Path);
            Assert.All(source.SkippedFiles, skipped => Assert.Equal(InstallShieldSkippedFileKind.StoredOutsideCabinet, skipped.Kind));
            Assert.Equal([0, 1, 2], source.SkippedFiles.Select(skipped => skipped.Index));
            Assert.Equal("The file shares the data of file 0 at 'outside.bin', which is stored outside the cabinet.",
                source.SkippedFiles[1].Reason);
            Assert.StartsWith("The file links to file 0, which is stored outside the cabinet: its data offset",
                source.SkippedFiles[2].Reason);
            await using var stream = source.OpenRead("inside.bin");
            Assert.Equal(Noise, await ReadAll(stream));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task ListsAVersion6CopyStoredInsideOfAFileStoredOutside()
    {
        var root = TemporaryDirectory();
        try
        {
            var data = Bytes(4000, 1);
            CabinetFile[] files =
            [
                new("", "same.bin", data, Outside: true),
                new("", "same.bin", data),
                new("", "same.bin", data, Outside: true)
            ];
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(6, files));
            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            var member = Assert.Single(source.Members);
            Assert.Equal(1, member.Metadata.Index);
            // Both copies stored outside hold the listed member's bytes, whichever side of it they lie.
            Assert.Equal([0, 2], member.SharedBy.Select(entry => entry.Index));
            Assert.Equal([0, 2], source.SkippedFiles.Select(skipped => skipped.Index));
            Assert.Equal(InstallShieldSkippedFileKind.StoredOutsideCabinet, source.SkippedFiles[0].Kind);
            // The later copy is a duplicate of the listed one, which is checked when read.
            Assert.Equal(InstallShieldSkippedFileKind.DuplicatesListedMember, source.SkippedFiles[1].Kind);
            await using (var stream = source.OpenRead("same.bin"))
                Assert.Equal(data, await ReadAll(stream));

            // Two copies stored outside: the second is skipped with the first.
            var second = Path.Combine(root, "both");
            SyntheticInstallShieldCabinet.WriteTo(second, SyntheticInstallShieldCabinet.Build(6,
                [new("", "same.bin", data, Outside: true), new("", "same.bin", data, Outside: true), new("", "inside.bin", Noise)]));
            using var both = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(second, "data1.hdr"));
            Assert.Equal("inside.bin", Assert.Single(both.Files).Path);
            Assert.StartsWith("The file duplicates file 0 at 'same.bin' (same expanded size and MD5), and is also stored outside the cabinet",
                both.SkippedFiles[1].Reason);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    // The offset of a file descriptor's data offset field in a header of major version 0, 5 or 6.
    private static int DataOffsetField(byte[] header, int major, int index)
    {
        var descriptor = SyntheticInstallShieldCabinet.DescriptorOffset;
        var table = descriptor + BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(descriptor + 0x0c));
        if (major == 6)
            return table + BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(descriptor + 0x2c)) + index * 0x57 + 0x12;
        var directories = BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(descriptor + 0x1c));
        return table + BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(table + 4 * (directories + index))) + 0x26;
    }

    private static void SetDataOffset(byte[] header, int major, int field, long offset)
    {
        if (major == 6)
            BinaryPrimitives.WriteUInt64LittleEndian(header.AsSpan(field), (ulong)offset);
        else
            BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(field), (uint)offset);
    }

    private static async Task<byte[]> ReadAll(Stream stream)
    {
        using var copy = new MemoryStream();
        await stream.CopyToAsync(copy, TestContext.Current.CancellationToken);
        return copy.ToArray();
    }

    private static byte[] Bytes(int length, int seed)
    {
        var bytes = new byte[length];
        new Random(seed).NextBytes(bytes);
        return bytes;
    }

    private static string TemporaryDirectory()
    {
        var path = Path.Combine(Path.GetTempPath(), "installshield-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        return path;
    }
}
