using System.Buffers.Binary;
using RefurbishedDinosaurs.Core.IO;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;
using Kind = RefurbishedDinosaurs.LegacyFormats.InstallShieldFileGroupMembershipKind;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class InstallShieldMemberMetadataTests
{
    private static readonly byte[] Text = "plain stored member\r\n"u8.ToArray();

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public async Task ReportsEachMembersEntryInAGroupLessCabinet(int major)
    {
        CabinetFile[] files =
        [
            new(@"Data\Maps", "map.bin", Bytes(5000, 1)),
            new("", "readme.txt", Text, Compressed: false),
            new("Data", "gone.bin", Text, Invalid: true)
        ];
        using var source = Open(major, files);

        Assert.Empty(source.FileGroups);
        Assert.Null(source.FileGroupProblem);
        Assert.Equal(source.Files, source.Members.Select(member => member.Entry));
        Assert.Equal(
            [(0, 0, "Data/Maps", "map.bin"), (1, 1, "", "readme.txt")],
            source.Members.Select(member => (member.Metadata.Index, member.Metadata.DirectoryIndex, member.Metadata.Directory, member.Metadata.Name)));
        Assert.All(source.Members, member =>
        {
            Assert.Equal(Kind.NoFileGroups, member.Metadata.FileGroups.Kind);
            Assert.Empty(member.Metadata.FileGroups.Groups);
            Assert.Empty(member.SharedBy);
        });
        // The payload and the skipped entry are as without metadata.
        Assert.Equal(2, Assert.Single(source.SkippedFiles).Index);
        Assert.True(source.TryGetMember("DATA/maps/MAP.BIN", out var found));
        Assert.Same(source.Members[0], found);
        Assert.False(source.TryGetMember("Data/gone.bin", out _));
        await using var stream = source.OpenRead(found!.Entry.Path);
        Assert.Equal(files[0].Data, await ReadAll(stream));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public void ReportsNoneOneAndSeveralGroupsWithoutChoosingOne(int major)
    {
        using var source = Open(major,
            [new("", "a.bin", Text), new("", "b.bin", Text), new("", "c.bin", Text), new("", "d.bin", Text)],
            [new("Program", 0, 1, List: 3), new("Help", 1, 2, List: 3), new("Extra", 2, 2, List: 40)]);

        Assert.Null(source.FileGroupProblem);
        Assert.Equal(
            [(0, "Program", 0, 1), (1, "Help", 1, 2), (2, "Extra", 2, 2)],
            source.FileGroups.Select(group => (group.Index, group.Name, group.FirstFile!.Value, group.LastFile!.Value)));
        Assert.All(source.FileGroups, group => Assert.Null(group.Problem));
        Assert.Equal(
            [(Kind.One, new[] { 0 }), (Kind.Several, [0, 1]), (Kind.Several, [1, 2]), (Kind.None, [])],
            source.Members.Select(member => (member.Metadata.FileGroups.Kind, member.Metadata.FileGroups.Groups.ToArray())));
        Assert.All(source.Members, member => Assert.Empty(member.Metadata.FileGroups.MalformedGroups));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public void ReportsMalformedRangesAndLeavesTheEntriesTheyMayHoldUndetermined(int major)
    {
        CabinetFile[] files = [new("", "a.bin", Text), new("", "b.bin", Text), new("", "c.bin", Text)];

        // A range that reaches past the table may hold the entries from its first file on.
        using (var source = Open(major, files, [new("Low", 0, 0), new("Past", 1, 99)]))
        {
            Assert.Null(source.FileGroups[0].Problem);
            Assert.Equal("Its range, files 1 to 99, reaches past the 3-entry file table.", source.FileGroups[1].Problem);
            Assert.Equal((1, 99), (source.FileGroups[1].FirstFile, source.FileGroups[1].LastFile));
            Assert.Equal(
                [(Kind.One, new[] { 0 }, Array.Empty<int>()), (Kind.Undetermined, [], [1]), (Kind.Undetermined, [], [1])],
                source.Members.Select(Membership));
        }

        // A reversed range or a negative index may hold any entry; the well-formed group still counts.
        foreach (var (group, problem) in new[]
                 {
                     (new CabinetGroup("Reversed", 2, 1), "Its range, files 2 to 1, is reversed."),
                     (new CabinetGroup("Negative", -1, 1), "Its range, files -1 to 1, holds a negative index.")
                 })
        {
            using var source = Open(major, files, [new("Low", 0, 0), group]);
            Assert.Equal(problem, source.FileGroups[1].Problem);
            Assert.Equal(
                [(Kind.Undetermined, new[] { 0 }, new[] { 1 }), (Kind.Undetermined, [], [1]), (Kind.Undetermined, [], [1])],
                source.Members.Select(Membership));
        }
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public async Task ReportsGroupsWhoseDescriptorOrNameDoesNotReadAndStillOpens(int major)
    {
        CabinetFile[] files = [new("", "a.bin", Text), new("", "b.bin", Text)];
        var set = SyntheticInstallShieldCabinet.Build(major, files,
            groups: [new("Named", 0, 0), new("Unnamed", 1, 1, List: 1)]);
        var header = set["data1.hdr"];

        // The second group's name lies past the header: its range still gives its member.
        var second = GroupDescriptor(header, 1);
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(second), 0x7fff_0000);
        using (var source = Open(set))
        {
            Assert.Null(source.FileGroups[1].Name);
            Assert.StartsWith("The group's name does not read. InstallShield header is truncated", source.FileGroups[1].Problem);
            Assert.Equal([(Kind.One, new[] { 0 }), (Kind.One, [1])],
                source.Members.Select(member => (member.Metadata.FileGroups.Kind, member.Metadata.FileGroups.Groups.ToArray())));
        }

        // A list entry naming no descriptor, or one past the header, leaves a group with no range.
        foreach (var descriptorOffset in new uint[] { 0, 0x7fff_0000 })
        {
            BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(ListEntry(header, 1) + 4), descriptorOffset);
            using var source = Open(set);
            Assert.Equal((null, null, null), (source.FileGroups[1].Name, source.FileGroups[1].FirstFile, source.FileGroups[1].LastFile));
            Assert.StartsWith(descriptorOffset == 0 ? "The group's list entry names no group descriptor." : "The group's descriptor does not read.",
                source.FileGroups[1].Problem);
            Assert.Equal(
                [(Kind.Undetermined, new[] { 0 }, new[] { 1 }), (Kind.Undetermined, [], [1])],
                source.Members.Select(Membership));
            await using var stream = source.OpenRead("b.bin");
            Assert.Equal(Text, await ReadAll(stream));
        }
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public void ReportsAGroupListThatDoesNotReadWholeAndStillOpens(int major)
    {
        CabinetFile[] files = [new("", "a.bin", Text), new("", "b.bin", Text)];
        CabinetGroup[] groups = [new("First", 0, 0), new("Second", 1, 1)];

        // A list head past the header.
        var set = SyntheticInstallShieldCabinet.Build(major, files, groups: groups);
        var header = set["data1.hdr"];
        BinaryPrimitives.WriteUInt32LittleEndian(
            header.AsSpan(SyntheticInstallShieldCabinet.DescriptorOffset + SyntheticInstallShieldCabinet.GroupListsOffset + 4 * 70), 0x7fff_0000);
        using (var source = Open(set))
        {
            Assert.StartsWith("An entry of file group list 70 does not read.", source.FileGroupProblem);
            Assert.Equal(2, source.FileGroups.Count);
            AssertAllUndetermined(source);
        }

        // A list that returns to an entry already read.
        set = SyntheticInstallShieldCabinet.Build(major, files, groups: groups);
        header = set["data1.hdr"];
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(ListEntry(header, 1) + 8), (uint)(ListEntry(header, 0) - SyntheticInstallShieldCabinet.DescriptorOffset));
        using (var source = Open(set))
        {
            Assert.Contains("which was already read", source.FileGroupProblem);
            Assert.Equal(["First", "Second"], source.FileGroups.Select(group => group.Name));
            // The groups read give what they hold, but the list did not end, so no membership is final.
            Assert.Equal([0], source.Members[0].Metadata.FileGroups.Groups);
            AssertAllUndetermined(source);
        }
    }

    [Fact]
    public void BoundsTheGroupCountAndTheirMembershipsByTheFileLimit()
    {
        CabinetFile[] files = [new("", "a.bin", Text), new("", "b.bin", Text)];
        var limits = new InstallShieldCabinetLimits(MaximumFiles: 2);

        using (var source = Open(6, files, [new("One", 0, 0), new("Two", 1, 1), new("Three", 0, 0)], limits))
        {
            Assert.Equal("The cabinet lists more than 2 file groups, the file limit; the rest are not read.", source.FileGroupProblem);
            Assert.Equal(2, source.FileGroups.Count);
            AssertAllUndetermined(source);
        }

        using (var source = Open(6, files, [new("All", 0, 1), new("Again", 0, 1)], limits))
        {
            Assert.Contains("more than 2 entries with their groups", source.FileGroupProblem);
            Assert.All(source.Members, member => Assert.Empty(member.Metadata.FileGroups.Groups));
            AssertAllUndetermined(source);
        }

        // At the limit, the memberships are read.
        using (var source = Open(6, files, [new("One", 0, 0), new("Two", 1, 1)], limits))
            Assert.All(source.Members, member => Assert.Equal(Kind.One, member.Metadata.FileGroups.Kind));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public void ReportsGroupsPastTheHeaderLimitOfACabinetThatHoldsTheHeader(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            var set = SyntheticInstallShieldCabinet.Build(major, [new("", "a.bin", Text)], headerInCabinet: true,
                groups: [new("Group", 0, 0)]);
            var cabinet = set["data1.cab"];
            SyntheticInstallShieldCabinet.WriteTo(root, set);
            // The limit ends where the group structures start, after the file table and names.
            var limit = ListEntry(cabinet, 0);
            using var source = OriginalContentSource.OpenInstallShieldCabinet(
                Path.Combine(root, "data1.cab"), new InstallShieldCabinetLimits(MaximumHeaderBytes: limit));
            Assert.Equal(["a.bin"], source.Files.Select(entry => entry.Path));
            Assert.Contains($"past the header limit of {limit} bytes", source.FileGroupProblem);
            AssertAllUndetermined(source);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task ReportsEntriesSharingAListedMembersPathWithTheirOwnGroups()
    {
        // The same name in two groups: a version 6 copy with the same size and MD5, and a link.
        var shared = Bytes(4000, 3);
        using var source = Open(6,
            [
                new("", "shared.dll", shared),
                new("", "SHARED.DLL", shared),
                new("", "shared.dll", [], LinkTo: 0),
                new("Docs", "shared.dll", Text)
            ],
            [new("Program", 0, 0), new("System", 1, 1), new("Tools", 2, 2), new("Help", 3, 3)]);

        Assert.Equal(["Docs/shared.dll", "shared.dll"], source.Files.Select(entry => entry.Path));
        Assert.True(source.TryGetMember("shared.dll", out var member));
        Assert.Equal(0, member!.Metadata.Index);
        Assert.Equal([0], member.Metadata.FileGroups.Groups);
        Assert.Equal(
            [(1, "SHARED.DLL", new[] { 1 }), (2, "shared.dll", [2])],
            member.SharedBy.Select(entry => (entry.Index, entry.Name, entry.FileGroups.Groups.ToArray())));
        // Each is still reported as skipped, with the reason it always had.
        Assert.Equal([1, 2], source.SkippedFiles.Select(file => file.Index));
        await using var stream = source.OpenRead("shared.dll");
        Assert.Equal(shared, await ReadAll(stream));
    }

    // An adapter that places members as an installer that installs each group below its own name
    // would, from the metadata alone: no header parsing, no path or content matching.
    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public async Task MapsInstalledPathsFromTheMetadataAlone(int major)
    {
        var dll = Bytes(3000, 5);
        var files = new List<CabinetFile>
        {
            new("Bin", "game.exe", Bytes(6000, 4)),
            new("Bin", "readme.txt", Text, Compressed: false),
            new("Docs", "readme.txt", "help text\r\n"u8.ToArray(), Compressed: false),
            new("", "setup.ini", "[setup]\r\n"u8.ToArray())
        };
        var groups = new List<CabinetGroup> { new("Program Files", 0, 1), new("Help", 2, 2, List: 9), new("Support", 3, 3, List: 9) };
        if (major == 6)
        {
            // The same file installed by two groups, stored twice.
            files.AddRange([new("Common", "shared.dll", dll), new("Common", "shared.dll", dll)]);
            groups.AddRange([new("Program Files", 4, 4, List: 1), new("System", 5, 5, List: 2)]);
        }
        using var source = Open(major, files, groups);

        var installed = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase);
        foreach (var member in source.Members)
        {
            foreach (var entry in member.SharedBy.Prepend(member.Metadata))
            {
                var group = Assert.Single(entry.FileGroups.Groups);
                Assert.Equal(Kind.One, entry.FileGroups.Kind);
                var name = source.FileGroups[group].Name!;
                var parts = new[] { name, entry.Directory, entry.Name }.Where(part => part.Length > 0);
                await using var stream = source.OpenRead(member.Entry.Path);
                installed.Add(PortableAssetPath.Relative(string.Join('/', parts)), await ReadAll(stream));
            }
        }

        var expected = new Dictionary<string, byte[]>
        {
            ["Program Files/Bin/game.exe"] = files[0].Data,
            ["Program Files/Bin/readme.txt"] = Text,
            ["Help/Docs/readme.txt"] = files[2].Data,
            ["Support/setup.ini"] = files[3].Data
        };
        if (major == 6)
        {
            expected["Program Files/Common/shared.dll"] = dll;
            expected["System/Common/shared.dll"] = dll;
        }
        Assert.Equal(expected.Keys.Order(), installed.Keys.Order());
        foreach (var (path, data) in expected) Assert.Equal(data, installed[path]);
    }

    [Fact]
    public async Task ReportsInstallShield3ArchiveMembersWithNoFileGroups()
    {
        using var archive = new MemoryStream(SyntheticInstallShieldArchive.Build(
            [new("", "setup.ini", Text, Stored: true), new(@"Data\Maps", "map.bin", Bytes(2000, 6)), new("Data", "gone.bin", Text, Invalid: true)]));
        using var source = OriginalContentSource.OpenInstallShieldArchive(archive);

        Assert.Equal(source.Files, source.Members.Select(member => member.Entry));
        Assert.Equal(
            [(1, 1, "Data/Maps", "map.bin"), (0, 0, "", "setup.ini")],
            source.Members.Select(member => (member.Metadata.Index, member.Metadata.DirectoryIndex, member.Metadata.Directory, member.Metadata.Name)));
        Assert.All(source.Members, member =>
        {
            Assert.Equal(Kind.NoFileGroups, member.Metadata.FileGroups.Kind);
            Assert.Empty(member.SharedBy);
        });
        Assert.True(source.TryGetMember("data/maps/MAP.BIN", out var found));
        Assert.False(source.TryGetMember("Data/gone.bin", out _));
        await using var stream = source.OpenRead(found!.Entry.Path);
        Assert.Equal(2000, (await ReadAll(stream)).Length);
    }

    private static (Kind, int[], int[]) Membership(InstallShieldMember member) =>
        (member.Metadata.FileGroups.Kind, member.Metadata.FileGroups.Groups.ToArray(), member.Metadata.FileGroups.MalformedGroups.ToArray());

    private static void AssertAllUndetermined(InstallShieldCabinetSource source) =>
        Assert.All(source.Members, member => Assert.Equal(Kind.Undetermined, member.Metadata.FileGroups.Kind));

    // Opens a set held in memory through another source, as a set on a disc image is opened.
    private static InstallShieldCabinetSource Open(
        int major, IReadOnlyList<CabinetFile> files, IReadOnlyList<CabinetGroup>? groups = null, InstallShieldCabinetLimits? limits = null) =>
        Open(SyntheticInstallShieldCabinet.Build(major, files, groups: groups), limits);

    private static InstallShieldCabinetSource Open(Dictionary<string, byte[]> set, InstallShieldCabinetLimits? limits = null) =>
        OriginalContentSource.OpenInstallShieldCabinet(new MemorySource(set), "data1.hdr", limits);

    // The offset in a header of the list entry of the nth group the writer wrote. The writer places
    // each entry, with its descriptor and name after it, in the order the groups are given, so the
    // entries' offsets, gathered from the lists, sort into that order.
    private static int ListEntry(byte[] header, int group)
    {
        var descriptor = SyntheticInstallShieldCabinet.DescriptorOffset;
        var offsets = new List<int>();
        for (var list = 0; list < 71; list++)
        {
            var next = BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(descriptor + SyntheticInstallShieldCabinet.GroupListsOffset + 4 * list));
            var guard = 0;
            while (next != 0 && next < header.Length - descriptor && guard++ < 16)
            {
                offsets.Add(descriptor + next);
                next = BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(descriptor + next + 8));
            }
        }
        return offsets.Order().ElementAt(group);
    }

    // The offset in a header of a group's descriptor.
    private static int GroupDescriptor(byte[] header, int group) => ListEntry(header, group) + 12;

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
