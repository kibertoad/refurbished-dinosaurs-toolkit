using System.Buffers.Binary;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;
using Kind = RefurbishedDinosaurs.LegacyFormats.InstallShieldFileGroupMembershipKind;
using Skip = RefurbishedDinosaurs.LegacyFormats.InstallShieldSkippedFileKind;

namespace RefurbishedDinosaurs.Core.Tests;

// Two or more different files at one path: the path lists none of them, each is reported with its
// metadata and read by its entry's index, and nothing picks one of them for the path.
public sealed class InstallShieldPathConflictTests
{
    private static readonly byte[] First = Bytes(5000, 1);
    private static readonly byte[] Second = Bytes(3000, 2);
    private static readonly byte[] Other = Bytes(700, 3);

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public async Task ReportsDifferentFilesAtOnePathAndReadsEachByIndex(int major)
    {
        using var source = Open(major,
            [new("Data", "same.bin", First), new("Data", "other.bin", Other), new("data", "SAME.BIN", Second, Compressed: false)]);

        Assert.Equal(["Data/other.bin"], source.Files.Select(entry => entry.Path));
        Assert.Equal(["Data/other.bin"], source.Members.Select(member => member.Entry.Path));
        Assert.Equal(
            [(0, "Data/same.bin", Skip.PathHeldByDifferentFiles), (2, "data/SAME.BIN", Skip.PathHeldByDifferentFiles)],
            source.SkippedFiles.Select(file => (file.Index, file.Path!, file.Kind)));
        Assert.All(source.SkippedFiles, file =>
            Assert.Equal("The path holds different files: files 0 and 2, so none is listed at it.", file.Reason));

        var conflict = Assert.Single(source.PathConflicts);
        Assert.Equal("Data/same.bin", conflict.Path);
        Assert.Empty(conflict.StoredOutside);
        Assert.Equal(
            [(0, "Data/same.bin", 5000L, "Data", "same.bin"), (2, "data/SAME.BIN", 3000L, "data", "SAME.BIN")],
            conflict.Files.Select(file => (file.Metadata.Index, file.Entry.Path, file.Entry.Size, file.Metadata.Directory, file.Metadata.Name)));
        Assert.All(conflict.Files, file =>
        {
            Assert.Empty(file.SharedBy);
            Assert.Equal(Kind.NoFileGroups, file.Metadata.FileGroups.Kind);
        });

        Assert.Equal(First, await ReadAll(source.OpenEntry(0)));
        Assert.Equal(Second, await ReadAll(source.OpenEntry(2)));
        Assert.Equal(Other, await ReadAll(source.OpenEntry(1)));

        // The path-addressed API stays strict: the path names no member.
        Assert.False(source.TryGetFile("data/same.bin", out _));
        Assert.False(source.TryGetMember("data/same.bin", out _));
        Assert.Contains("holds different files at this path",
            Assert.Throws<FileNotFoundException>(() => source.OpenRead("Data/same.bin")).Message);
        Assert.Throws<FileNotFoundException>(() => source.OpenEntry(3));
        Assert.Throws<FileNotFoundException>(() => source.OpenEntry(-1));
    }

    // Versions 0 and 5 record no MD5, so equal names, sizes and even bytes do not make two entries one
    // file; in version 6 the MD5s differ.
    [Theory]
    [InlineData(0, 1)]
    [InlineData(5, 1)]
    [InlineData(6, 4)]
    public async Task DoesNotTakeEntriesWithEqualNamesAndSizesToBeOneFile(int major, int secondSeed)
    {
        var second = Bytes(First.Length, secondSeed);
        using var source = Open(major, [new("Data", "same.bin", First), new("Data", "same.bin", second)]);

        Assert.Empty(source.Files);
        var conflict = Assert.Single(source.PathConflicts);
        Assert.Equal([0, 1], conflict.Files.Select(file => file.Metadata.Index));
        Assert.Equal(First, await ReadAll(source.OpenEntry(0)));
        Assert.Equal(second, await ReadAll(source.OpenEntry(1)));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public void ReportsTheGroupsOfEachFileWithoutChoosingOne(int major)
    {
        // Entry 0 is in Program only, entry 1 in Program and Help, entry 2 in Help only, and entry 3 in no group.
        using var source = Open(major,
            [new("", "a.bin", First), new("", "a.bin", Second), new("", "a.bin", Other), new("", "a.bin", Bytes(10, 9))],
            [new("Program", 0, 1), new("Help", 1, 2)]);

        Assert.Empty(source.Files);
        var conflict = Assert.Single(source.PathConflicts);
        Assert.Equal(
            [(Kind.One, new[] { 0 }), (Kind.Several, [0, 1]), (Kind.One, [1]), (Kind.None, [])],
            conflict.Files.Select(file => (file.Metadata.FileGroups.Kind, file.Metadata.FileGroups.Groups.ToArray())));
        Assert.All(source.SkippedFiles, file =>
            Assert.Equal("The path holds different files: files 0, 1, 2 and 3, so none is listed at it.", file.Reason));
    }

    [Fact]
    public void LeavesEveryFileUndeterminedWhenAMalformedGroupMayHoldIt()
    {
        using var source = Open(6, [new("", "a.bin", First), new("", "a.bin", Second)], [new("Past", 1, 99)]);

        var conflict = Assert.Single(source.PathConflicts);
        Assert.Equal(
            [(Kind.None, Array.Empty<int>()), (Kind.Undetermined, [0])],
            conflict.Files.Select(file => (file.Metadata.FileGroups.Kind, file.Metadata.FileGroups.MalformedGroups.ToArray())));
    }

    [Fact]
    public async Task KeepsTheEntriesThatShareEachFilesDataWithIt()
    {
        // Entries 2 and 3 link to entry 0's data and entry 4 is a version 6 copy of entry 1, all at the
        // contested path; entry 5 links to entry 0's data from another path, where it is listed.
        using var source = Open(6,
        [
            new("Data", "same.bin", First),
            new("Data", "same.bin", Second),
            new("Data", "same.bin", [], LinkTo: 0),
            new("DATA", "same.bin", [], LinkTo: 0),
            new("Data", "same.bin", Second),
            new("Copy", "linked.bin", [], LinkTo: 0)
        ]);

        Assert.Equal(["Copy/linked.bin"], source.Files.Select(entry => entry.Path));
        var conflict = Assert.Single(source.PathConflicts);
        Assert.Equal(
            [(0, new[] { 2, 3 }), (1, [4])],
            conflict.Files.Select(file => (file.Metadata.Index, file.SharedBy.Select(entry => entry.Index).ToArray())));
        const string held = "The path holds different files: files 0 and 1, so none is listed at it.";
        Assert.Equal(
        [
            (0, Skip.PathHeldByDifferentFiles, held),
            (1, Skip.PathHeldByDifferentFiles, held),
            (2, Skip.PathHeldByDifferentFiles, $"{held} The file shares the data of file 0 at 'Data/same.bin'."),
            (3, Skip.PathHeldByDifferentFiles, $"{held} The file shares the data of file 0 at 'Data/same.bin'."),
            (4, Skip.PathHeldByDifferentFiles, $"{held} The file duplicates file 1 at 'Data/same.bin': same expanded size and MD5.")
        ], source.SkippedFiles.Select(file => (file.Index, file.Kind, file.Reason)));

        Assert.Equal(First, await ReadAll(source.OpenEntry(3)));
        Assert.Equal(Second, await ReadAll(source.OpenEntry(4)));
        Assert.Equal(First, await ReadAll(source.OpenEntry(5)));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public async Task ReportsAFileStoredOutsideAtAContestedPathWithoutReadingIt(int major)
    {
        using var source = Open(major,
            [new("", "same.bin", Bytes(4000, 5), Outside: true), new("", "same.bin", Second), new("", "other.bin", Other)]);

        Assert.Equal(["other.bin"], source.Files.Select(entry => entry.Path));
        var conflict = Assert.Single(source.PathConflicts);
        Assert.Equal([1], conflict.Files.Select(file => file.Metadata.Index));
        Assert.Equal([0], conflict.StoredOutside.Select(entry => entry.Index));
        Assert.Equal(
            [(0, Skip.StoredOutsideCabinet), (1, Skip.PathHeldByDifferentFiles)],
            source.SkippedFiles.Select(file => (file.Index, file.Kind)));
        Assert.StartsWith("The file is stored outside the cabinet", source.SkippedFiles[0].Reason);
        Assert.Equal("The path holds different files: files 0 and 1, so none is listed at it.", source.SkippedFiles[1].Reason);
        Assert.Equal(Second, await ReadAll(source.OpenEntry(1)));
        Assert.Throws<FileNotFoundException>(() => source.OpenEntry(0));
    }

    [Fact]
    public async Task NamesTheFirstEntryOfAFileStoredOutsideWithACopyInside()
    {
        // Entry 0 is stored outside, entry 2 is its version 6 copy inside, and entry 1 is a different file.
        var copied = Bytes(4000, 5);
        using var source = Open(6,
            [new("", "same.bin", copied, Outside: true), new("", "same.bin", Second), new("", "same.bin", copied)]);

        var conflict = Assert.Single(source.PathConflicts);
        Assert.Equal(
            [(2, new[] { 0 }), (1, [])],
            conflict.Files.Select(file => (file.Metadata.Index, file.SharedBy.Select(entry => entry.Index).ToArray())));
        Assert.Empty(conflict.StoredOutside);
        const string held = "The path holds different files: files 0 and 1, so none is listed at it.";
        Assert.Equal(
            [(0, Skip.StoredOutsideCabinet), (1, Skip.PathHeldByDifferentFiles), (2, Skip.PathHeldByDifferentFiles)],
            source.SkippedFiles.Select(file => (file.Index, file.Kind)));
        Assert.Equal(held, source.SkippedFiles[2].Reason);
        Assert.Equal(copied, await ReadAll(source.OpenEntry(0)));
        Assert.Equal(copied, await ReadAll(source.OpenEntry(2)));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public void ReportsAPathWhoseDifferentFilesAreAllStoredOutside(int major)
    {
        using var source = Open(major,
            [new("", "same.bin", First, Outside: true), new("", "same.bin", Second, Outside: true), new("", "other.bin", Other)]);

        Assert.Equal(["other.bin"], source.Files.Select(entry => entry.Path));
        var conflict = Assert.Single(source.PathConflicts);
        Assert.Empty(conflict.Files);
        Assert.Equal([0, 1], conflict.StoredOutside.Select(entry => entry.Index));
        Assert.Equal(
            [(0, Skip.StoredOutsideCabinet), (1, Skip.StoredOutsideCabinet)],
            source.SkippedFiles.Select(file => (file.Index, file.Kind)));
        Assert.Throws<FileNotFoundException>(() => source.OpenEntry(0));
        Assert.Throws<FileNotFoundException>(() => source.OpenEntry(1));
        Assert.Contains("holds different files at this path",
            Assert.Throws<FileNotFoundException>(() => source.OpenRead("same.bin")).Message);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public void StillChecksEachContestedFilesPathExtentAndSize(int major)
    {
        // An expanded total past the limit counts the contested files.
        CabinetFile[] files = [new("", "same.bin", First), new("", "same.bin", Second)];
        Assert.Contains("expands to more than the limit of 7000 bytes",
            Assert.Throws<InvalidDataException>(() => Open(major, files, limits: new(MaximumExpandedBytes: 7000))).Message);
        using (var source = Open(major, files, limits: new(MaximumExpandedBytes: 8000)))
            Assert.Single(source.PathConflicts);

        // A contested file whose data lies past its volume is damage.
        var set = SyntheticInstallShieldCabinet.Build(major, files);
        SetDataOffset(set["data1.hdr"], major, 1, set["data1.cab"].Length + 10);
        Assert.Contains("file 1 lies past the end of volume 1",
            Assert.Throws<InvalidDataException>(() => Open(set)).Message);

        // A contested entry's path must be portable.
        Assert.Contains("file 1 has a path that is not portable",
            Assert.Throws<InvalidDataException>(() => Open(major, [new("", "a.bin", First), new("", "a.bin<", Second)])).Message);
    }

    [Fact]
    public async Task ReportsDifferentEntriesAtOnePathOfAnInstallShield3Archive()
    {
        var archive = SyntheticInstallShieldArchive.Build(
            [new("Data", "same.bin", First), new("Data", "SAME.BIN", Second, Stored: true), new("Data", "other.bin", Other)]);
        using var source = OriginalContentSource.OpenInstallShieldArchive(new MemoryStream(archive));

        Assert.Equal(["Data/other.bin"], source.Files.Select(entry => entry.Path));
        var conflict = Assert.Single(source.PathConflicts);
        Assert.Equal("Data/same.bin", conflict.Path);
        Assert.Empty(conflict.StoredOutside);
        Assert.Equal([(0, "Data/same.bin"), (1, "Data/SAME.BIN")], conflict.Files.Select(file => (file.Metadata.Index, file.Entry.Path)));
        Assert.All(conflict.Files, file => Assert.Equal(Kind.NoFileGroups, file.Metadata.FileGroups.Kind));
        Assert.Equal(
            [(0, Skip.PathHeldByDifferentFiles), (1, Skip.PathHeldByDifferentFiles)],
            source.SkippedFiles.Select(file => (file.Index, file.Kind)));
        Assert.All(source.SkippedFiles, file =>
            Assert.Equal("The path holds different files: files 0 and 1, so none is listed at it.", file.Reason));

        Assert.Equal(First, await ReadAll(source.OpenEntry(0)));
        Assert.Equal(Second, await ReadAll(source.OpenEntry(1)));
        Assert.Equal(Other, await ReadAll(source.OpenEntry(2)));
        Assert.False(source.TryGetFile("Data/same.bin", out _));
        Assert.Contains("holds different files at this path",
            Assert.Throws<FileNotFoundException>(() => source.OpenRead("Data/same.bin")).Message);
        Assert.Throws<FileNotFoundException>(() => source.OpenEntry(3));
    }

    private static InstallShieldCabinetSource Open(
        int major, IReadOnlyList<CabinetFile> files, IReadOnlyList<CabinetGroup>? groups = null, InstallShieldCabinetLimits? limits = null) =>
        Open(SyntheticInstallShieldCabinet.Build(major, files, groups: groups), limits);

    // Opens a set held in memory through another source, as a set on a disc image is opened.
    private static InstallShieldCabinetSource Open(Dictionary<string, byte[]> set, InstallShieldCabinetLimits? limits = null) =>
        OriginalContentSource.OpenInstallShieldCabinet(new MemorySource(set), "data1.hdr", limits);

    // Sets a file descriptor's data offset in a header of major version 0, 5 or 6.
    private static void SetDataOffset(byte[] header, int major, int index, long offset)
    {
        var descriptor = SyntheticInstallShieldCabinet.DescriptorOffset;
        var table = descriptor + BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(descriptor + 0x0c));
        if (major == 6)
        {
            var field = table + BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(descriptor + 0x2c)) + index * 0x57 + 0x12;
            BinaryPrimitives.WriteUInt64LittleEndian(header.AsSpan(field), (ulong)offset);
            return;
        }
        var directories = BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(descriptor + 0x1c));
        var at = table + BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(table + 4 * (directories + index))) + 0x26;
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(at), (uint)offset);
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

    // A set held in memory, read through the same interface as a directory.
    private sealed class MemorySource(Dictionary<string, byte[]> set) : OriginalContentSource
    {
        public override string Kind => ContentSourceKinds.Directory;
        public override string? Label => null;
        public override IReadOnlyList<ContentSourceEntry> Files =>
            set.Select(pair => new ContentSourceEntry(pair.Key, pair.Value.Length)).ToArray();

        public override bool TryGetFile(string relativePath, out ContentSourceEntry? entry)
        {
            entry = Files.FirstOrDefault(file => file.Path.Equals(relativePath, StringComparison.OrdinalIgnoreCase));
            return entry is not null;
        }

        public override Stream OpenRead(string relativePath) =>
            new MemoryStream(set.First(pair => pair.Key.Equals(relativePath, StringComparison.OrdinalIgnoreCase)).Value, writable: false);

        public override void Dispose() { }
    }
}
