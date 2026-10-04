using System.IO.Compression;
using System.Text;
using System.Text.Json;
using RefurbishedDinosaurs.Core.Assets;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class ContentOverlayTests : IDisposable
{
    private static readonly byte[] Base = "base bytes"u8.ToArray();
    private static readonly byte[] Patched = "patched bytes, longer"u8.ToArray();
    private static readonly byte[] Added = "added"u8.ToArray();

    private readonly string _work = Directory.CreateTempSubdirectory("overlay-tests-").FullName;
    private readonly string _content;

    public ContentOverlayTests()
    {
        _content = Path.Combine(_work, "content");
        Directory.CreateDirectory(Path.Combine(_content, "DATA"));
        File.WriteAllBytes(Path.Combine(_content, "DATA", "MAIN.BIN"), Base);
        File.WriteAllBytes(Path.Combine(_content, "keep.txt"), "untouched"u8.ToArray());
    }

    public void Dispose() => Directory.Delete(_work, recursive: true);

    private static CancellationToken Token => TestContext.Current.CancellationToken;

    private static object Record(string path, byte[] payload, string? baseXxh3, string? xxh3 = null, long? bytes = null) => new
    {
        path,
        bytes = bytes ?? payload.Length,
        baseXxh3,
        xxh3 = xxh3 ?? FileFingerprint.Xxh3(payload)
    };

    private static object Manifest(params object[] files) => new
    {
        formatVersion = 1,
        name = "synthetic-overlay-1",
        gameId = "synthetic-game",
        fromVersion = "1.0",
        toVersion = "1.1",
        files
    };

    private static object StandardManifest(string? patchedXxh3 = null) => Manifest(
        Record("data/main.bin", Patched, FileFingerprint.Xxh3(Base), patchedXxh3),
        Record("extra/new/added.dat", Added, null));

    private static (string Path, byte[] Payload)[] StandardPayloads =>
        [("data/main.bin", Patched), ("extra/new/added.dat", Added)];

    private string Zip(object manifest, params (string Path, byte[] Payload)[] payloads)
    {
        var path = Path.Combine(_work, $"overlay-{Guid.NewGuid():N}.zip");
        using var archive = ZipFile.Open(path, ZipArchiveMode.Create);
        using (var stream = archive.CreateEntry(ContentOverlay.ManifestFileName).Open())
            JsonSerializer.Serialize(stream, manifest);
        foreach (var (name, payload) in payloads)
        {
            using var stream = archive.CreateEntry($"{ContentOverlay.PayloadDirectory}/{name}").Open();
            stream.Write(payload);
        }
        return path;
    }

    private string Folder(object manifest, params (string Path, byte[] Payload)[] payloads)
    {
        var path = Path.Combine(_work, $"overlay-{Guid.NewGuid():N}");
        Directory.CreateDirectory(path);
        File.WriteAllText(Path.Combine(path, ContentOverlay.ManifestFileName), JsonSerializer.Serialize(manifest));
        foreach (var (name, payload) in payloads)
        {
            var target = Path.Combine(path, ContentOverlay.PayloadDirectory, name);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.WriteAllBytes(target, payload);
        }
        return path;
    }

    private string[] ContentEntries() => Directory
        .EnumerateFileSystemEntries(_content, "*", SearchOption.AllDirectories)
        .Select(path => Path.GetRelativePath(_content, path).Replace('\\', '/'))
        .Order(StringComparer.Ordinal)
        .ToArray();

    [Fact]
    public async Task ReplacesAndAddsFilesAndReportsThePatchedHashes()
    {
        using var overlay = ContentOverlay.OpenZip(Zip(StandardManifest(), StandardPayloads));
        var result = await overlay.ApplyAsync(_content, Token);

        Assert.Equal(2, result.Written);
        Assert.Equal(
        [
            new ContentOverlayOutput("DATA/MAIN.BIN", Patched.Length, FileFingerprint.Xxh3(Patched),
                FileFingerprint.Xxh3(Base), ContentOverlayAction.Replaced),
            new ContentOverlayOutput("extra/new/added.dat", Added.Length, FileFingerprint.Xxh3(Added),
                null, ContentOverlayAction.Added)
        ], result.Outputs);
        Assert.Equal(Patched, File.ReadAllBytes(Path.Combine(_content, "DATA", "MAIN.BIN")));
        Assert.Equal(Added, File.ReadAllBytes(Path.Combine(_content, "extra", "new", "added.dat")));
        Assert.Equal(["DATA", "DATA/MAIN.BIN", "extra", "extra/new", "extra/new/added.dat", "keep.txt"], ContentEntries());
    }

    [Fact]
    public async Task ARerunWritesNothingAndReportsEveryRecordAsApplied()
    {
        var archive = Zip(StandardManifest(), StandardPayloads);
        using (var first = ContentOverlay.OpenZip(archive)) await first.ApplyAsync(_content, Token);
        var main = Path.Combine(_content, "DATA", "MAIN.BIN");
        var written = new DateTime(2001, 1, 1, 0, 0, 0, DateTimeKind.Utc);
        File.SetLastWriteTimeUtc(main, written);

        using var overlay = ContentOverlay.OpenZip(archive);
        var result = await overlay.ApplyAsync(_content, Token);

        Assert.Equal(0, result.Written);
        Assert.All(result.Outputs, output => Assert.Equal(ContentOverlayAction.AlreadyApplied, output.Action));
        Assert.Equal(written, File.GetLastWriteTimeUtc(main));
        Assert.Equal(["DATA", "DATA/MAIN.BIN", "extra", "extra/new", "extra/new/added.dat", "keep.txt"], ContentEntries());
    }

    [Fact]
    public async Task AppliesAnOverlayLaidOutAsADirectory()
    {
        using var overlay = ContentOverlay.OpenDirectory(Folder(StandardManifest(), StandardPayloads));
        var result = await overlay.ApplyAsync(_content, Token);

        Assert.Equal(2, result.Written);
        Assert.Equal(Patched, File.ReadAllBytes(Path.Combine(_content, "DATA", "MAIN.BIN")));
    }

    [Fact]
    public async Task ATargetWithNeitherHashStopsBeforeAnythingIsWritten()
    {
        File.WriteAllBytes(Path.Combine(_content, "DATA", "MAIN.BIN"), "edited by the player"u8.ToArray());
        using var overlay = ContentOverlay.OpenZip(Zip(StandardManifest(), StandardPayloads));

        var error = await Assert.ThrowsAsync<ContentOverlayException>(() => overlay.ApplyAsync(_content, Token));

        Assert.Equal(ContentOverlayProblem.TargetChanged, error.Problem);
        Assert.Equal("DATA/MAIN.BIN", error.Path);
        Assert.Equal(FileFingerprint.Xxh3("edited by the player"u8), error.FoundXxh3);
        Assert.Equal(["DATA", "DATA/MAIN.BIN", "keep.txt"], ContentEntries());
    }

    [Fact]
    public async Task AnAddedFileMustNotExistAndAReplacedFileMust()
    {
        Directory.CreateDirectory(Path.Combine(_content, "extra", "new"));
        File.WriteAllBytes(Path.Combine(_content, "extra", "new", "added.dat"), "other"u8.ToArray());
        using (var overlay = ContentOverlay.OpenZip(Zip(StandardManifest(), StandardPayloads)))
        {
            var exists = await Assert.ThrowsAsync<ContentOverlayException>(() => overlay.ApplyAsync(_content, Token));
            Assert.Equal(ContentOverlayProblem.TargetExists, exists.Problem);
        }
        Assert.Equal(Base, File.ReadAllBytes(Path.Combine(_content, "DATA", "MAIN.BIN")));

        File.Delete(Path.Combine(_content, "DATA", "MAIN.BIN"));
        File.Delete(Path.Combine(_content, "extra", "new", "added.dat"));
        using (var overlay = ContentOverlay.OpenZip(Zip(StandardManifest(), StandardPayloads)))
        {
            var missing = await Assert.ThrowsAsync<ContentOverlayException>(() => overlay.ApplyAsync(_content, Token));
            Assert.Equal(ContentOverlayProblem.TargetMissing, missing.Problem);
            Assert.Null(missing.FoundXxh3);
        }
        Assert.False(File.Exists(Path.Combine(_content, "extra", "new", "added.dat")));
    }

    [Fact]
    public async Task APayloadWithTheWrongHashLeavesEveryTargetAndNoTemporaryFile()
    {
        var wrong = FileFingerprint.Xxh3("something else"u8);
        using var overlay = ContentOverlay.OpenZip(Zip(StandardManifest(wrong), StandardPayloads));

        var error = await Assert.ThrowsAsync<ContentOverlayException>(() => overlay.ApplyAsync(_content, Token));

        Assert.Equal(ContentOverlayProblem.PayloadWrongHash, error.Problem);
        Assert.Equal(FileFingerprint.Xxh3(Patched), error.FoundXxh3);
        Assert.Equal(Base, File.ReadAllBytes(Path.Combine(_content, "DATA", "MAIN.BIN")));
        Assert.Equal(["DATA", "DATA/MAIN.BIN", "keep.txt"], ContentEntries());
    }

    [Fact]
    public async Task APayloadThatChangedSizeAfterOpeningLeavesEveryTargetAndNoTemporaryFile()
    {
        var folder = Folder(StandardManifest(), StandardPayloads);
        using var overlay = ContentOverlay.OpenDirectory(folder);
        File.AppendAllText(Path.Combine(folder, ContentOverlay.PayloadDirectory, "data", "main.bin"), "!");

        var error = await Assert.ThrowsAsync<ContentOverlayException>(() => overlay.ApplyAsync(_content, Token));

        Assert.Equal(ContentOverlayProblem.PayloadWrongSize, error.Problem);
        Assert.Equal(Base, File.ReadAllBytes(Path.Combine(_content, "DATA", "MAIN.BIN")));
        Assert.Equal(["DATA", "DATA/MAIN.BIN", "keep.txt"], ContentEntries());
    }

    [Fact]
    public void APayloadWhoseSizeDiffersFromItsRecordIsRejectedWhenOpened()
    {
        var manifest = Manifest(Record("data/main.bin", Patched, FileFingerprint.Xxh3(Base), bytes: Patched.Length + 1));
        Assert.Throws<InvalidDataException>(() => ContentOverlay.OpenZip(Zip(manifest, ("data/main.bin", Patched))));
        Assert.Throws<InvalidDataException>(() => ContentOverlay.OpenDirectory(Folder(manifest, ("data/main.bin", Patched))));
    }

    public static TheoryData<string> InvalidManifests => new()
    {
        // Duplicate paths, ignoring case.
        """{"formatVersion":1,"name":"o","gameId":"g","fromVersion":"1","toVersion":"2","files":[{"path":"a.bin","bytes":1,"baseXxh3":null,"xxh3":"00000000000000000000000000000001"},{"path":"A.BIN","bytes":1,"baseXxh3":null,"xxh3":"00000000000000000000000000000001"}]}""",
        // A path that escapes the root.
        """{"formatVersion":1,"name":"o","gameId":"g","fromVersion":"1","toVersion":"2","files":[{"path":"../a.bin","bytes":1,"baseXxh3":null,"xxh3":"00000000000000000000000000000001"}]}""",
        // A path that is a directory of another record.
        """{"formatVersion":1,"name":"o","gameId":"g","fromVersion":"1","toVersion":"2","files":[{"path":"a","bytes":1,"baseXxh3":null,"xxh3":"00000000000000000000000000000001"},{"path":"a/b","bytes":1,"baseXxh3":null,"xxh3":"00000000000000000000000000000001"}]}""",
        // baseXxh3 left out, instead of null for an added file.
        """{"formatVersion":1,"name":"o","gameId":"g","fromVersion":"1","toVersion":"2","files":[{"path":"a.bin","bytes":1,"xxh3":"00000000000000000000000000000001"}]}""",
        // A record that would change nothing.
        """{"formatVersion":1,"name":"o","gameId":"g","fromVersion":"1","toVersion":"2","files":[{"path":"a.bin","bytes":1,"baseXxh3":"00000000000000000000000000000001","xxh3":"00000000000000000000000000000001"}]}""",
        // An upper-case fingerprint.
        """{"formatVersion":1,"name":"o","gameId":"g","fromVersion":"1","toVersion":"2","files":[{"path":"a.bin","bytes":1,"baseXxh3":null,"xxh3":"0000000000000000000000000000000A"}]}""",
        // An unknown field.
        """{"formatVersion":1,"name":"o","gameId":"g","fromVersion":"1","toVersion":"2","files":[],"extra":true}""",
        // Another format version.
        """{"formatVersion":2,"name":"o","gameId":"g","fromVersion":"1","toVersion":"2","files":[{"path":"a.bin","bytes":1,"baseXxh3":null,"xxh3":"00000000000000000000000000000001"}]}""",
        // No files.
        """{"formatVersion":1,"name":"o","gameId":"g","fromVersion":"1","toVersion":"2","files":[]}""",
        // A blank name.
        """{"formatVersion":1,"name":" ","gameId":"g","fromVersion":"1","toVersion":"2","files":[{"path":"a.bin","bytes":1,"baseXxh3":null,"xxh3":"00000000000000000000000000000001"}]}""",
    };

    [Theory]
    [MemberData(nameof(InvalidManifests))]
    public void InvalidManifestsAreRejectedWhenRead(string json) =>
        Assert.Throws<InvalidDataException>(() => ContentOverlayManifest.Parse(Encoding.UTF8.GetBytes(json)));

    [Fact]
    public void OverlaysOverAnyLimitAreRejectedWhenRead()
    {
        var json = JsonSerializer.SerializeToUtf8Bytes(Manifest(
            Record("a.bin", Added, null), Record("b.bin", Added, null)));
        Assert.Throws<InvalidDataException>(() => ContentOverlayManifest.Parse(json, new(MaximumFiles: 1)));
        Assert.Throws<InvalidDataException>(() => ContentOverlayManifest.Parse(json, new(MaximumFileBytes: Added.Length - 1)));
        Assert.Throws<InvalidDataException>(() => ContentOverlayManifest.Parse(json, new(MaximumTotalBytes: Added.Length * 2 - 1)));
        Assert.Throws<InvalidDataException>(() => ContentOverlayManifest.Parse(json, new(MaximumManifestBytes: json.Length - 1)));
        Assert.Equal(2, ContentOverlayManifest.Parse(json, new(MaximumFiles: 2, MaximumFileBytes: Added.Length,
            MaximumTotalBytes: Added.Length * 2, MaximumManifestBytes: json.Length)).Files.Count);

        var archive = Zip(Manifest(Record("a.bin", Added, null), Record("b.bin", Added, null)),
            ("a.bin", Added), ("b.bin", Added));
        Assert.Throws<InvalidDataException>(() => ContentOverlay.OpenZip(archive, new(MaximumFiles: 1)));
    }

    [Fact]
    public void PayloadsMustMatchTheRecordsOneToOne()
    {
        var manifest = Manifest(Record("a.bin", Added, null));
        Assert.Throws<InvalidDataException>(() => ContentOverlay.OpenZip(Zip(manifest)));
        Assert.Throws<InvalidDataException>(() => ContentOverlay.OpenZip(Zip(manifest, ("a.bin", Added), ("b.bin", Added))));
        Assert.Throws<InvalidDataException>(() => ContentOverlay.OpenZip(Zip(manifest, ("a.bin", Added), ("A.BIN", Added))));
        Assert.Throws<InvalidDataException>(() => ContentOverlay.OpenDirectory(Folder(manifest)));
        Assert.Throws<InvalidDataException>(() => ContentOverlay.OpenDirectory(Folder(manifest, ("a.bin", Added), ("b.bin", Added))));
    }

    [Fact]
    public async Task OutputsReplaceTheirInstalledRecordsAndNameTheOverlay()
    {
        using var overlay = ContentOverlay.OpenZip(Zip(StandardManifest(), StandardPayloads));
        var result = await overlay.ApplyAsync(_content, Token);
        InstalledAsset[] imported =
        [
            new("DATA/MAIN.BIN", Base.Length, FileFingerprint.Xxh3(Base), "DATA/MAIN.BIN", "application/x-synthetic"),
            new("keep.txt", 9, FileFingerprint.Xxh3("untouched"u8), "keep.txt", "text/plain")
        ];

        var files = result.UpdateInstalledFiles(imported);

        Assert.Equal(
        [
            new InstalledAsset("DATA/MAIN.BIN", Patched.Length, FileFingerprint.Xxh3(Patched), "DATA/MAIN.BIN",
                "application/x-synthetic", new AssetConversion("synthetic-overlay-1")),
            imported[1],
            new InstalledAsset("extra/new/added.dat", Added.Length, FileFingerprint.Xxh3(Added), "extra/new/added.dat",
                "application/octet-stream", new AssetConversion("synthetic-overlay-1"))
        ], files);
    }
}
