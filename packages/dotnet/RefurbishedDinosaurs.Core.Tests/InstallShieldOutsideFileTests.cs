using RefurbishedDinosaurs.LegacyFormats;
using Xunit;
using Skip = RefurbishedDinosaurs.LegacyFormats.InstallShieldSkippedFileKind;
using Status = RefurbishedDinosaurs.LegacyFormats.InstallShieldOutsideFileStatus;

namespace RefurbishedDinosaurs.Core.Tests;

// Files stored outside the cabinet are looked up beside the header, at the directory and name of the
// entry that holds their data, as Unshield's -O looks for them. Each gets a status for what was found,
// and only one found once with exactly its stored length, at a path holding no other file, is read.
public sealed class InstallShieldOutsideFileTests
{
    private static readonly byte[] Noise = Bytes(50_000, 7);

    [Theory]
    [InlineData(0, true, false)]
    [InlineData(0, false, false)]
    [InlineData(5, true, false)]
    [InlineData(6, true, false)]
    [InlineData(6, false, true)]
    public async Task ReportsWhatIsFoundBesideTheHeaderAndReadsOnlyAnAvailableFile(int major, bool markerDelimited, bool headerInCabinet)
    {
        var root = TemporaryDirectory();
        try
        {
            var found = Bytes(9000, 1);
            var plain = Bytes(300, 2);
            CabinetFile[] files =
            [
                new("Setup", "found.bin", found, Outside: true),
                new("Data", "inside.bin", Noise),
                new("Data", "plain.txt", plain, Compressed: false, Obfuscated: true, Outside: true),
                new("Data", "missing.bin", Bytes(4000, 3), Outside: true),
                new("Data", "short.bin", Bytes(4000, 4), Outside: true),
            ];
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(
                major, files, headerInCabinet: headerInCabinet, markerDelimited: markerDelimited));
            // The lookup matches each component ignoring case, as the volume lookup does.
            Write(root, "SETUP/Found.BIN", SyntheticInstallShieldCabinet.StoredBytes(files[0], markerDelimited));
            Write(root, "Data/plain.txt", SyntheticInstallShieldCabinet.StoredBytes(files[2], markerDelimited));
            var shortStored = SyntheticInstallShieldCabinet.StoredBytes(files[4], markerDelimited);
            Write(root, "Data/short.bin", shortStored[..^1]);

            using var source = OriginalContentSource.OpenInstallShieldCabinet(
                Path.Combine(root, headerInCabinet ? "data1.cab" : "data1.hdr"), compressedFormat: markerDelimited
                    ? InstallShieldCompressedFormat.MarkerDelimitedChunks
                    : InstallShieldCompressedFormat.LengthPrefixedChunks);

            // Files stored outside stay out of the path-addressed API, found or not.
            Assert.Equal(["Data/inside.bin"], source.Files.Select(entry => entry.Path));
            Assert.Equal([0, 2, 3, 4], source.SkippedFiles.Select(file => file.Index));
            Assert.All(source.SkippedFiles, file => Assert.Equal(Skip.StoredOutsideCabinet, file.Kind));
            Assert.False(source.TryGetFile("Setup/found.bin", out _));

            Assert.Equal(
            [
                (0, "Setup/found.bin", true, Status.Available, (long?)SyntheticInstallShieldCabinet.StoredBytes(files[0], markerDelimited).Length),
                (2, "Data/plain.txt", false, Status.Available, (long?)plain.Length),
                (3, "Data/missing.bin", true, Status.Missing, null),
                (4, "Data/short.bin", true, Status.LengthDiffers, (long?)shortStored.Length - 1),
            ],
            source.OutsideFiles.Select(file => (file.Member.Metadata.Index, file.LookupPath!, file.Compressed, file.Status, file.FoundLength)));
            Assert.Equal(shortStored.Length, source.OutsideFiles[3].StoredSize);
            Assert.Equal(plain.Length, source.OutsideFiles[1].StoredSize);
            Assert.Equal((found.LongLength, "Setup", "found.bin"),
                (source.OutsideFiles[0].Member.Entry.Size, source.OutsideFiles[0].Member.Metadata.Directory, source.OutsideFiles[0].Member.Metadata.Name));

            Assert.Equal(found, await ReadAll(source.OpenEntry(0)));
            Assert.Equal(plain, await ReadAll(source.OpenEntry(2)));
            Assert.Equal(Noise, await ReadAll(source.OpenEntry(1)));
            Assert.Throws<FileNotFoundException>(() => source.OpenEntry(3));
            Assert.Throws<FileNotFoundException>(() => source.OpenEntry(4));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task LooksUpBesideTheHeaderInAContainingSource()
    {
        var root = TemporaryDirectory();
        try
        {
            var found = Bytes(6000, 5);
            CabinetFile[] files = [new("", "inside.bin", Noise), new("Sub", "found.bin", found, Outside: true), new("", "gone.bin", found, Outside: true)];
            var disc = Path.Combine(root, "DISC");
            SyntheticInstallShieldCabinet.WriteTo(Path.Combine(disc, "setup"), SyntheticInstallShieldCabinet.Build(5, files));
            Write(disc, "setup/sub/found.bin", SyntheticInstallShieldCabinet.StoredBytes(files[1]));
            // A file of the same name outside the header's folder is not looked at.
            Write(disc, "gone.bin", SyntheticInstallShieldCabinet.StoredBytes(files[2]));

            using var container = OriginalContentSource.OpenDirectory(disc);
            using var source = OriginalContentSource.OpenInstallShieldCabinet(container, "setup/data1.hdr");
            Assert.Equal([Status.Available, Status.Missing], source.OutsideFiles.Select(file => file.Status));
            Assert.Equal(found, await ReadAll(source.OpenEntry(1)));
            Assert.Throws<FileNotFoundException>(() => source.OpenEntry(2));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    // Two different files at one path both stored outside: one file beside the header cannot be both,
    // and nothing tells which one it is, so neither is read.
    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public void DoesNotReadAFileFoundAtAPathThatHoldsDifferentFiles(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            var first = Bytes(4000, 6);
            CabinetFile[] files =
            [
                new("Data", "same.bin", first, Outside: true),
                new("Data", "inside.bin", Noise),
                new("DATA", "same.bin", Bytes(4000, 8), Outside: true),
                new("Data", "mixed.bin", Bytes(700, 9), Compressed: false),
                new("Data", "MIXED.bin", Bytes(800, 10), Compressed: false, Outside: true),
            ];
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(major, files));
            Write(root, "Data/same.bin", SyntheticInstallShieldCabinet.StoredBytes(files[0]));
            Write(root, "Data/mixed.bin", files[4].Data);

            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            Assert.Equal(["Data/inside.bin"], source.Files.Select(entry => entry.Path));
            Assert.Equal(
            [
                (0, Status.PathHeldByDifferentFiles, (long?)SyntheticInstallShieldCabinet.StoredBytes(files[0]).Length),
                (2, Status.PathHeldByDifferentFiles, (long?)SyntheticInstallShieldCabinet.StoredBytes(files[0]).Length),
                (4, Status.PathHeldByDifferentFiles, (long?)800),
            ],
            source.OutsideFiles.Select(file => (file.Member.Metadata.Index, file.Status, file.FoundLength)));
            Assert.Equal(["Data/mixed.bin", "Data/same.bin"], source.PathConflicts.Select(conflict => conflict.Path));
            Assert.Equal([0, 2], source.PathConflicts[1].StoredOutside.Select(entry => entry.Index));
            Assert.Throws<FileNotFoundException>(() => source.OpenEntry(0));
            Assert.Throws<FileNotFoundException>(() => source.OpenEntry(2));
            Assert.Throws<FileNotFoundException>(() => source.OpenEntry(4));
            // The file the cabinet holds inside at the contested path is still read by index.
            using var mixed = source.OpenEntry(3);
            Assert.Equal(700, mixed.Length);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    // A version 6 entry that links to a file stored outside is looked up at the linked entry's path, as
    // Unshield follows the link before it looks for the file.
    [Fact]
    public async Task LooksUpALinkAtThePathOfTheEntryItLinksTo()
    {
        var root = TemporaryDirectory();
        try
        {
            var data = Bytes(5000, 11);
            CabinetFile[] files = [new("Data", "first.bin", data, Outside: true), new("Copy", "linked.bin", [], LinkTo: 0)];
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(6, files));
            Write(root, "Data/first.bin", SyntheticInstallShieldCabinet.StoredBytes(files[0]));

            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            Assert.Equal(
                [(0, "Data/first.bin", "Data/first.bin", Status.Available), (1, "Copy/linked.bin", "Data/first.bin", Status.Available)],
                source.OutsideFiles.Select(file => (file.Member.Metadata.Index, file.Member.Entry.Path, file.LookupPath!, file.Status)));
            Assert.Equal(data, await ReadAll(source.OpenEntry(1)));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    // A file found with the right length is still decoded and checked as a member inside is.
    [Theory]
    [InlineData(5, false)]
    [InlineData(5, true)]
    [InlineData(6, false)]
    public async Task ChecksAFileFoundAsAMemberInsideIsChecked(int major, bool markerDelimited)
    {
        var root = TemporaryDirectory();
        try
        {
            CabinetFile[] files = [new("", "outside.bin", Bytes(5000, 12), Outside: true), new("", "plain.bin", Bytes(400, 13), Compressed: false, Outside: true)];
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(major, files, markerDelimited: markerDelimited));
            var stored = SyntheticInstallShieldCabinet.StoredBytes(files[0], markerDelimited);
            Write(root, "outside.bin", Bytes(stored.Length, 14));
            Write(root, "plain.bin", Bytes(400, 15));

            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"),
                compressedFormat: markerDelimited ? InstallShieldCompressedFormat.MarkerDelimitedChunks : InstallShieldCompressedFormat.LengthPrefixedChunks);
            Assert.All(source.OutsideFiles, file => Assert.Equal(Status.Available, file.Status));
            await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(source.OpenEntry(0)));
            // Version 6 records an MD5, so different bytes of the right length fail the read; version 5
            // records none, so they read as the file's bytes, as Unshield would write them.
            if (major == 6)
                Assert.Contains("does not match the MD5",
                    (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(source.OpenEntry(1)))).Message);
            else
                Assert.Equal(Bytes(400, 15), await ReadAll(source.OpenEntry(1)));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task ReportsAFileThatShrinksAfterTheOpenWhenItIsRead()
    {
        var root = TemporaryDirectory();
        try
        {
            CabinetFile[] files = [new("", "outside.bin", Bytes(5000, 16), Compressed: false, Outside: true)];
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(5, files));
            Write(root, "outside.bin", files[0].Data);

            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            Assert.Equal(Status.Available, Assert.Single(source.OutsideFiles).Status);
            Write(root, "outside.bin", files[0].Data[..100]);
            Assert.Contains("is shorter than when the set was opened",
                (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(source.OpenEntry(0)))).Message);
            File.Delete(Path.Combine(root, "outside.bin"));
            await Assert.ThrowsAsync<FileNotFoundException>(() => ReadAll(source.OpenEntry(0)));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    // A file found counts toward the expanded-size limit, since it is read; one not found does not.
    [Fact]
    public void CountsOnlyAnAvailableFileTowardTheExpandedSizeLimit()
    {
        var root = TemporaryDirectory();
        try
        {
            CabinetFile[] files = [new("", "inside.bin", Noise), new("", "outside.bin", Bytes(5000, 17), Compressed: false, Outside: true)];
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(0, files));
            var header = Path.Combine(root, "data1.hdr");
            var limits = new InstallShieldCabinetLimits(MaximumExpandedBytes: Noise.Length);

            using (var source = OriginalContentSource.OpenInstallShieldCabinet(header, limits))
                Assert.Equal(Status.Missing, Assert.Single(source.OutsideFiles).Status);

            Write(root, "outside.bin", files[1].Data);
            Assert.Contains("expands to more than the limit",
                Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(header, limits)).Message);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    // Only a case-sensitive file system can hold two names that differ by case.
    [Fact]
    public void ReadsNoneOfSeveralFilesThatMatchThePathIgnoringCase()
    {
        var root = TemporaryDirectory();
        try
        {
            CabinetFile[] files = [new("", "outside.bin", Bytes(500, 18), Compressed: false, Outside: true)];
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(5, files));
            Write(root, "outside.bin", files[0].Data);
            Write(root, "OUTSIDE.BIN", files[0].Data);
            if (Directory.GetFiles(root, "*.bin").Length < 2) Assert.Skip("The file system matches names ignoring case.");

            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            var file = Assert.Single(source.OutsideFiles);
            Assert.Equal((Status.SeveralMatches, (long?)null), (file.Status, file.FoundLength));
            Assert.Throws<FileNotFoundException>(() => source.OpenEntry(0));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    private static void Write(string root, string relativePath, byte[] bytes)
    {
        var path = Path.Combine(root, relativePath);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path, bytes);
    }

    private static async Task<byte[]> ReadAll(Stream stream)
    {
        await using (stream)
        {
            using var copy = new MemoryStream();
            await stream.CopyToAsync(copy, TestContext.Current.CancellationToken);
            return copy.ToArray();
        }
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
