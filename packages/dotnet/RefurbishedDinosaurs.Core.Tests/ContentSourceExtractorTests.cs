using System.Text;
using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class ContentSourceExtractorTests : IDisposable
{
    private const int SectorSize = 2048;

    private static readonly byte[] Readme = "synthetic readme"u8.ToArray();
    private static readonly byte[] Map = Enumerable.Range(0, 300).Select(value => (byte)value).ToArray();
    // Spans three sectors, so the copy crosses sector boundaries.
    private static readonly byte[] Unit = Enumerable.Range(0, 5000).Select(value => (byte)(value * 7)).ToArray();

    private readonly string _work = Directory.CreateTempSubdirectory("extract-tests-").FullName;

    public void Dispose() => Directory.Delete(_work, recursive: true);

    private static CancellationToken Token => TestContext.Current.CancellationToken;

    private static Dictionary<string, byte[]> DiscFiles() => new()
    {
        ["README.TXT"] = Readme,
        ["DATA/MAP.BIN"] = Map,
        ["DATA/SUB/UNIT.DAT"] = Unit
    };

    private OriginalContentSource OpenDisc()
    {
        var path = Path.Combine(_work, "disc.iso");
        File.WriteAllBytes(path, BuildTreeIso(DiscFiles()));
        return OriginalContentSource.Open(path);
    }

    [Fact]
    public async Task CopiesANestedIsoBelowThePrefixWithRecordsTheVerifierAccepts()
    {
        using var source = OpenDisc();
        var destination = Path.Combine(_work, "content");
        using (var stage = StagedAssetPack.Create(destination))
        {
            var records = await ContentSourceExtractor.ExtractAsync(source, stage.StagingDirectory,
                new ContentExtractionOptions(Prefix: "cd", Conversion: new AssetConversion("iso-extract")), Token);

            Assert.Equal(["cd/DATA/MAP.BIN", "cd/DATA/SUB/UNIT.DAT", "cd/README.TXT"], records.Select(record => record.Path));
            foreach (var record in records)
            {
                var bytes = DiscFiles()[record.SourcePath];
                Assert.Equal(bytes.Length, record.Bytes);
                Assert.Equal(FileFingerprint.Xxh3(bytes), record.Xxh3);
                Assert.Equal("iso-extract", record.Conversion?.Method);
                Assert.Equal("application/octet-stream", record.MediaType);
                Assert.Equal(bytes, await File.ReadAllBytesAsync(Path.Combine(stage.StagingDirectory, record.Path), Token));
            }

            var manifest = new InstalledAssetManifest(1, "synthetic", "edition", FileFingerprint.Xxh3("edition"u8),
                DateTimeOffset.UnixEpoch, records, "1.0");
            manifest.Write(Path.Combine(stage.StagingDirectory, "manifest.json"));
            stage.Commit();
        }

        var verification = await InstalledAssetVerifier.VerifyDirectoryAsync(destination,
            new InstalledAssetExpectations(1, "synthetic", RejectUnlistedFiles: true), Token);
        Assert.True(verification.IsValid, string.Join("; ", verification.Issues.Select(issue => issue.Detail)));
        Assert.Equal(3, verification.VerifiedFiles);
    }

    [Fact]
    public async Task CopiesOnlyTheSelectedFilesIntoAnEmptyRoot()
    {
        using var source = OpenDisc();
        var root = Directory.CreateDirectory(Path.Combine(_work, "root")).FullName;

        var records = await ContentSourceExtractor.ExtractAsync(source, root,
            new ContentExtractionOptions(Include: entry => entry.Path.StartsWith("DATA/", StringComparison.Ordinal)), Token);

        Assert.Equal(["DATA/MAP.BIN", "DATA/SUB/UNIT.DAT"], records.Select(record => record.Path));
        Assert.All(records, record => Assert.Null(record.Conversion));
        Assert.False(File.Exists(Path.Combine(root, "README.TXT")));
    }

    [Fact]
    public async Task CopiesTheMembersOfAnInstallShieldCabinet()
    {
        var cabinet = Path.Combine(_work, "cabinet");
        SyntheticInstallShieldCabinet.WriteTo(cabinet, SyntheticInstallShieldCabinet.Build(6,
            [new("Data", "levels.bin", Unit), new("", "setup.ini", Readme, Compressed: false)]));
        using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(cabinet, "data1.hdr"));
        var root = Directory.CreateDirectory(Path.Combine(_work, "root")).FullName;

        var records = await ContentSourceExtractor.ExtractAsync(source, root, null, Token);

        Assert.Equal(
            [("Data/levels.bin", FileFingerprint.Xxh3(Unit)), ("setup.ini", FileFingerprint.Xxh3(Readme))],
            records.Select(record => (record.Path, record.Xxh3)));
    }

    [Fact]
    public async Task CancellationMidwayRemovesWhatWasWrittenAndLeavesTheStageUncommitted()
    {
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(Token);
        using var source = new ListedSource(DiscFiles(), onOpen: path =>
        {
            if (path == "DATA/SUB/UNIT.DAT") cancellation.Cancel();
        });
        var destination = Path.Combine(_work, "content");
        using (var stage = StagedAssetPack.Create(destination))
        {
            await Assert.ThrowsAnyAsync<OperationCanceledException>(() => ContentSourceExtractor.ExtractAsync(
                source, stage.StagingDirectory, new ContentExtractionOptions(Prefix: "game/cd"), cancellation.Token));

            Assert.Empty(Directory.EnumerateFileSystemEntries(stage.StagingDirectory));
        }
        Assert.False(Directory.Exists(destination));
    }

    [Fact]
    public async Task CancellationInsideAnExistingEmptyPrefixKeepsThePrefixAndEmptiesIt()
    {
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(Token);
        using var source = new ListedSource(DiscFiles(), onOpen: path =>
        {
            if (path == "DATA/SUB/UNIT.DAT") cancellation.Cancel();
        });
        var root = Directory.CreateDirectory(Path.Combine(_work, "root")).FullName;
        var prefix = Directory.CreateDirectory(Path.Combine(root, "cd")).FullName;
        File.WriteAllBytes(Path.Combine(root, "other.bin"), [1]);

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => ContentSourceExtractor.ExtractAsync(
            source, root, new ContentExtractionOptions(Prefix: "cd"), cancellation.Token));

        Assert.True(Directory.Exists(prefix));
        Assert.Empty(Directory.EnumerateFileSystemEntries(prefix));
        Assert.True(File.Exists(Path.Combine(root, "other.bin")));
    }

    [Theory]
    [InlineData(2, long.MaxValue)]
    [InlineData(int.MaxValue, 5315L)]
    public async Task ASelectionOverALimitStopsBeforeWritingAnything(int maximumFiles, long maximumTotalBytes)
    {
        var opened = new List<string>();
        using var source = new ListedSource(DiscFiles(), onOpen: opened.Add);
        var root = Directory.CreateDirectory(Path.Combine(_work, "root")).FullName;

        var exception = await Assert.ThrowsAsync<InvalidDataException>(() => ContentSourceExtractor.ExtractAsync(source, root,
            new ContentExtractionOptions(MaximumFiles: maximumFiles, MaximumTotalBytes: maximumTotalBytes), Token));

        Assert.Contains("more than", exception.Message);
        Assert.Empty(opened);
        Assert.Empty(Directory.EnumerateFileSystemEntries(root));
    }

    [Fact]
    public async Task ASelectionAtTheLimitsIsCopied()
    {
        using var source = new ListedSource(DiscFiles());
        var root = Directory.CreateDirectory(Path.Combine(_work, "root")).FullName;
        var total = DiscFiles().Values.Sum(bytes => (long)bytes.Length);

        var records = await ContentSourceExtractor.ExtractAsync(source, root,
            new ContentExtractionOptions(MaximumFiles: 3, MaximumTotalBytes: total), Token);

        Assert.Equal(3, records.Count);
    }

    [Theory]
    [InlineData(-1)]
    [InlineData(1)]
    public async Task AFileThatYieldsAnotherSizeThanListedIsRejectedAndRemoved(int difference)
    {
        using var source = new ListedSource(DiscFiles(),
            sizes: new() { ["DATA/SUB/UNIT.DAT"] = Unit.Length + difference });
        var root = Directory.CreateDirectory(Path.Combine(_work, "root")).FullName;

        var exception = await Assert.ThrowsAsync<InvalidDataException>(() =>
            ContentSourceExtractor.ExtractAsync(source, root, null, Token));

        Assert.Contains("DATA/SUB/UNIT.DAT", exception.Message);
        Assert.Empty(Directory.EnumerateFileSystemEntries(root));
    }

    [Fact]
    public async Task ADirectorySpelledTwoWaysIsWrittenOnceWithTheFirstSpelling()
    {
        using var source = new ListedSource(new()
        {
            ["Data/a.bin"] = [1],
            ["DATA/b.bin"] = [2],
            ["data/Sub/c.bin"] = [3]
        });
        var root = Directory.CreateDirectory(Path.Combine(_work, "root")).FullName;

        var records = await ContentSourceExtractor.ExtractAsync(source, root, null, Token);

        Assert.Equal(["Data/a.bin", "Data/b.bin", "Data/Sub/c.bin"], records.Select(record => record.Path));
        Assert.Equal(["Data/a.bin", "DATA/b.bin", "data/Sub/c.bin"], records.Select(record => record.SourcePath));
        Assert.Equal(["Data"], Directory.EnumerateDirectories(root).Select(Path.GetFileName));
    }

    [Theory]
    [InlineData("a", "A/b")]
    [InlineData("A/b", "a")]
    public async Task AFilePathThatIsAlsoADirectoryIsRejectedBeforeWriting(string first, string second)
    {
        using var source = new ListedSource(new() { [first] = [1], [second] = [2] });
        var root = Directory.CreateDirectory(Path.Combine(_work, "root")).FullName;

        await Assert.ThrowsAsync<InvalidDataException>(() => ContentSourceExtractor.ExtractAsync(source, root, null, Token));

        Assert.Empty(Directory.EnumerateFileSystemEntries(root));
    }

    [Fact]
    public async Task RejectsADestinationThatIsNotEmptyOrNotADirectory()
    {
        using var source = new ListedSource(DiscFiles());
        var root = Directory.CreateDirectory(Path.Combine(_work, "root")).FullName;
        Directory.CreateDirectory(Path.Combine(root, "cd"));
        File.WriteAllBytes(Path.Combine(root, "cd", "old.bin"), [1]);
        File.WriteAllBytes(Path.Combine(root, "file"), [1]);

        await Assert.ThrowsAsync<IOException>(() => ContentSourceExtractor.ExtractAsync(source, root, null, Token));
        await Assert.ThrowsAsync<IOException>(() => ContentSourceExtractor.ExtractAsync(source, root,
            new ContentExtractionOptions(Prefix: "CD"), Token));
        await Assert.ThrowsAsync<IOException>(() => ContentSourceExtractor.ExtractAsync(source, root,
            new ContentExtractionOptions(Prefix: "file/cd"), Token));
        await Assert.ThrowsAsync<InvalidDataException>(() => ContentSourceExtractor.ExtractAsync(source, root,
            new ContentExtractionOptions(Prefix: "../cd"), Token));
        await Assert.ThrowsAsync<DirectoryNotFoundException>(() => ContentSourceExtractor.ExtractAsync(source,
            Path.Combine(_work, "missing"), null, Token));
        await Assert.ThrowsAsync<ArgumentOutOfRangeException>(() => ContentSourceExtractor.ExtractAsync(source, root,
            new ContentExtractionOptions(MaximumFiles: -1), Token));
        Assert.Equal(["old.bin"], Directory.EnumerateFileSystemEntries(Path.Combine(root, "cd")).Select(Path.GetFileName));
    }

    [Fact]
    public async Task APrefixThatSpellsAnExistingDirectoryWithDifferentCaseIsRejected()
    {
        using var source = new ListedSource(DiscFiles());
        var root = Directory.CreateDirectory(Path.Combine(_work, "root")).FullName;
        Directory.CreateDirectory(Path.Combine(root, "stage", "cd"));

        await Assert.ThrowsAsync<IOException>(() => ContentSourceExtractor.ExtractAsync(source, root,
            new ContentExtractionOptions(Prefix: "Stage/cd"), Token));
        await Assert.ThrowsAsync<IOException>(() => ContentSourceExtractor.ExtractAsync(source, root,
            new ContentExtractionOptions(Prefix: "stage/CD"), Token));

        Assert.Equal(["stage"], Directory.EnumerateFileSystemEntries(root).Select(Path.GetFileName));
        Assert.Equal(["cd"],
            Directory.EnumerateFileSystemEntries(Path.Combine(root, "stage")).Select(Path.GetFileName));
        Assert.Empty(Directory.EnumerateFileSystemEntries(Path.Combine(root, "stage", "cd")));
    }

    [Fact]
    public async Task AnUnsafeSourcePathIsRejectedBeforeWriting()
    {
        using var source = new ListedSource(new() { ["ok.bin"] = [1], ["bad./x.bin"] = [2] });
        var root = Directory.CreateDirectory(Path.Combine(_work, "root")).FullName;

        await Assert.ThrowsAsync<InvalidDataException>(() => ContentSourceExtractor.ExtractAsync(source, root, null, Token));

        Assert.Empty(Directory.EnumerateFileSystemEntries(root));
    }

    // Builds an ISO 9660 image of the files, one directory sector per directory, files after them.
    private static byte[] BuildTreeIso(IReadOnlyDictionary<string, byte[]> files)
    {
        var directories = new List<string> { string.Empty };
        foreach (var path in files.Keys)
        {
            var parts = path.Split('/');
            for (var index = 1; index < parts.Length; index++)
            {
                var directory = string.Join('/', parts[..index]);
                if (!directories.Contains(directory)) directories.Add(directory);
            }
        }
        var sectors = new Dictionary<string, uint>();
        uint next = 18;
        foreach (var directory in directories) sectors[directory] = next++;
        var fileSectors = new Dictionary<string, uint>();
        foreach (var (path, bytes) in files)
        {
            fileSectors[path] = next;
            next += (uint)Math.Max(1, (bytes.Length + SectorSize - 1) / SectorSize);
        }

        var image = new byte[next * SectorSize];
        var pvd = image.AsSpan(16 * SectorSize, SectorSize);
        pvd[0] = 1;
        "CD001"u8.CopyTo(pvd[1..]);
        pvd[6] = 1;
        OriginalContentSourceTests.WritePaddedAscii(pvd[40..72], "SYNTHETIC_TREE");
        OriginalContentSourceTests.WriteBothEndianUInt32(pvd, 80, next);
        OriginalContentSourceTests.WriteBothEndianUInt16(pvd, 128, SectorSize);
        OriginalContentSourceTests.WriteDirectoryRecord(pvd, 156, sectors[string.Empty], SectorSize, true, [0]);
        var terminator = image.AsSpan(17 * SectorSize, SectorSize);
        terminator[0] = 255;
        "CD001"u8.CopyTo(terminator[1..]);
        terminator[6] = 1;

        foreach (var directory in directories)
        {
            var parent = directory.Contains('/') ? directory[..directory.LastIndexOf('/')] : string.Empty;
            var block = image.AsSpan((int)sectors[directory] * SectorSize, SectorSize);
            var offset = OriginalContentSourceTests.WriteDirectoryRecord(block, 0, sectors[directory], SectorSize, true, [0]);
            offset += OriginalContentSourceTests.WriteDirectoryRecord(block, offset, sectors[parent], SectorSize, true, [1]);
            foreach (var child in directories.Where(child => child.Length > 0 && Parent(child) == directory))
                offset += OriginalContentSourceTests.WriteDirectoryRecord(block, offset, sectors[child], SectorSize, true,
                    Encoding.ASCII.GetBytes(Name(child)));
            foreach (var (path, bytes) in files.Where(file => Parent(file.Key) == directory))
                offset += OriginalContentSourceTests.WriteDirectoryRecord(block, offset, fileSectors[path], bytes.Length, false,
                    Encoding.ASCII.GetBytes(Name(path) + ";1"));
        }
        foreach (var (path, bytes) in files) bytes.CopyTo(image.AsSpan((int)fileSectors[path] * SectorSize));
        return image;

        static string Parent(string path) => path.Contains('/') ? path[..path.LastIndexOf('/')] : string.Empty;
        static string Name(string path) => path[(path.LastIndexOf('/') + 1)..];
    }

    // A source over bytes in memory that can list a size other than its bytes and observe each open.
    private sealed class ListedSource(
        Dictionary<string, byte[]> files,
        Dictionary<string, long>? sizes = null,
        Action<string>? onOpen = null) : OriginalContentSource
    {
        public override string Kind => ContentSourceKinds.Directory;
        public override string? Label => null;

        public override IReadOnlyList<ContentSourceEntry> Files { get; } = files
            .Select(file => new ContentSourceEntry(file.Key,
                sizes is not null && sizes.TryGetValue(file.Key, out var size) ? size : file.Value.Length))
            .OrderBy(entry => entry.Path, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        public override bool TryGetFile(string relativePath, out ContentSourceEntry? entry)
        {
            entry = Files.FirstOrDefault(file => file.Path.Equals(relativePath, StringComparison.OrdinalIgnoreCase));
            return entry is not null;
        }

        public override Stream OpenRead(string relativePath)
        {
            onOpen?.Invoke(relativePath);
            return new MemoryStream(files[relativePath], writable: false);
        }

        public override void Dispose() { }
    }
}
