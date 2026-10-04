using System.IO.Compression;
using System.Text.Json;
using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

// ContentOverlay.OpenZip(Stream) and OriginalContentSource.OpenIso9660(Stream) against the same bytes
// opened from a path.
public sealed class StreamOpenedSourceTests : IDisposable
{
    private static readonly byte[] Base = "base bytes"u8.ToArray();
    private static readonly byte[] Patched = "patched bytes, longer"u8.ToArray();
    private static readonly byte[] Added = "added"u8.ToArray();

    private readonly string _work = Directory.CreateTempSubdirectory("stream-source-tests-").FullName;

    public void Dispose() => Directory.Delete(_work, recursive: true);

    private static CancellationToken Token => TestContext.Current.CancellationToken;

    private static object Record(string path, byte[] payload, string? baseXxh3, long? bytes = null) => new
    {
        path,
        bytes = bytes ?? payload.Length,
        baseXxh3,
        xxh3 = FileFingerprint.Xxh3(payload)
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

    private static object StandardManifest() => Manifest(
        Record("data/main.bin", Patched, FileFingerprint.Xxh3(Base)),
        Record("extra/added.dat", Added, null));

    private static readonly (string Path, byte[] Payload)[] StandardPayloads =
        [("data/main.bin", Patched), ("extra/added.dat", Added)];

    private static byte[] ZipBytes(object manifest, params (string Path, byte[] Payload)[] payloads)
    {
        using var buffer = new MemoryStream();
        using (var archive = new ZipArchive(buffer, ZipArchiveMode.Create, leaveOpen: true))
        {
            using (var stream = archive.CreateEntry(ContentOverlay.ManifestFileName).Open())
                JsonSerializer.Serialize(stream, manifest);
            foreach (var (name, payload) in payloads)
            {
                using var stream = archive.CreateEntry($"{ContentOverlay.PayloadDirectory}/{name}").Open();
                stream.Write(payload);
            }
        }
        return buffer.ToArray();
    }

    private string Content(string name)
    {
        var root = Path.Combine(_work, name);
        Directory.CreateDirectory(Path.Combine(root, "DATA"));
        File.WriteAllBytes(Path.Combine(root, "DATA", "MAIN.BIN"), Base);
        return root;
    }

    [Fact]
    public async Task AnOverlayFromAStreamGivesTheRecordsAndResultOfTheSameZipFromAPath()
    {
        var bytes = ZipBytes(StandardManifest(), StandardPayloads);
        var path = Path.Combine(_work, "overlay.zip");
        await File.WriteAllBytesAsync(path, bytes, Token);
        var fromPathContent = Content("from-path");
        var fromStreamContent = Content("from-stream");

        ContentOverlayResult fromPath;
        IReadOnlyList<ContentOverlayFile> pathRecords;
        using (var overlay = ContentOverlay.OpenZip(path))
        {
            pathRecords = overlay.Manifest.Files;
            fromPath = await overlay.ApplyAsync(fromPathContent, Token);
        }

        using var stream = new MemoryStream(bytes, writable: false);
        ContentOverlayResult fromStream;
        using (var overlay = ContentOverlay.OpenZip(stream))
        {
            Assert.Equal(pathRecords, overlay.Manifest.Files);
            fromStream = await overlay.ApplyAsync(fromStreamContent, Token);
        }

        Assert.Equal(fromPath.Name, fromStream.Name);
        Assert.Equal(fromPath.Outputs, fromStream.Outputs);
        Assert.Equal(2, fromStream.Written);
        Assert.Equal(Patched, File.ReadAllBytes(Path.Combine(fromStreamContent, "DATA", "MAIN.BIN")));
        Assert.Equal(Added, File.ReadAllBytes(Path.Combine(fromStreamContent, "extra", "added.dat")));
        // The caller keeps the stream: disposing the overlay leaves it open.
        Assert.True(stream.CanRead);
        stream.Position = 0;
        Assert.Equal(bytes.Length, stream.Length);
    }

    [Fact]
    public void AnOverlayFromAStreamIsCheckedAgainstTheSameLimitsAndRules()
    {
        var two = Manifest(Record("a.bin", Added, null), Record("b.bin", Added, null));
        var twoPayloads = new[] { ("a.bin", Added), ("b.bin", Added) };
        Assert.Throws<InvalidDataException>(() => Open(ZipBytes(two, twoPayloads), new(MaximumFiles: 1)));
        Assert.Throws<InvalidDataException>(() => Open(ZipBytes(two, twoPayloads), new(MaximumFileBytes: Added.Length - 1)));
        Assert.Throws<InvalidDataException>(() => Open(ZipBytes(two, twoPayloads), new(MaximumTotalBytes: Added.Length * 2 - 1)));
        Assert.Throws<InvalidDataException>(() => Open(ZipBytes(two, twoPayloads), new(MaximumManifestBytes: 16)));
        using (var accepted = Open(ZipBytes(two, twoPayloads), new(MaximumFiles: 2)))
            Assert.Equal(2, accepted.Manifest.Files.Count);

        var one = Manifest(Record("a.bin", Added, null));
        Assert.Throws<InvalidDataException>(() => Open(ZipBytes(one)));
        Assert.Throws<InvalidDataException>(() => Open(ZipBytes(one, ("a.bin", Added), ("b.bin", Added))));
        Assert.Throws<InvalidDataException>(() => Open(ZipBytes(one, ("a.bin", Added), ("A.BIN", Added))));
        Assert.Throws<InvalidDataException>(() => Open(ZipBytes(Manifest(Record("a.bin", Added, null, bytes: 4)), ("a.bin", Added))));
        Assert.Throws<InvalidDataException>(() => Open("not a zip archive"u8.ToArray()));
    }

    [Fact]
    public void AnOverlayStreamThatCannotSeekOrReadIsRejected()
    {
        var bytes = ZipBytes(StandardManifest(), StandardPayloads);
        Assert.Throws<ArgumentException>(() => ContentOverlay.OpenZip(new ForwardOnlyStream(bytes)));
        Assert.Throws<ArgumentException>(() => ContentOverlay.OpenZip(new UnreadableStream()));
        Assert.Throws<ArgumentNullException>(() => ContentOverlay.OpenZip((Stream)null!));
    }

    [Fact]
    public async Task AVolumeFromAStreamMatchesTheSameIsoFromAPath()
    {
        var payload = new byte[] { 1, 2, 3, 4, 5 };
        var image = OriginalContentSourceTests.BuildIso(payload);
        var path = Path.Combine(_work, "volume.iso");
        await File.WriteAllBytesAsync(path, image, Token);

        using var fromPath = OriginalContentSource.OpenIso9660(path);
        using var stream = new MemoryStream(image, writable: false);
        using (var fromStream = OriginalContentSource.OpenIso9660(stream))
        {
            Assert.Equal(ContentSourceKinds.Iso9660, fromStream.Kind);
            Assert.Equal(fromPath.Files, fromStream.Files);
            Assert.Equal(fromPath.Label, fromStream.Label);
            Assert.Equal(fromPath.VolumeBlocks, fromStream.VolumeBlocks);
            Assert.Equal(await ReadAllAsync(fromPath.OpenVolume()), await ReadAllAsync(fromStream.OpenVolume()));
            Assert.Equal(payload, await ReadAllAsync(fromStream.OpenRead("ei/test.bin")));

            // Streams opened from the source keep their own positions over the one caller stream.
            await using var file = fromStream.OpenRead("EI/TEST.BIN");
            await using var volume = fromStream.OpenVolume();
            Assert.Equal(1, file.ReadByte());
            volume.Position = 16 * 2048 + 1;
            Assert.Equal((byte)'C', volume.ReadByte());
            Assert.Equal(2, file.ReadByte());
            var rest = new byte[3];
            await file.ReadExactlyAsync(rest, Token);
            Assert.Equal(payload[2..], rest);
        }

        // The caller keeps the stream: disposing the source and its streams leaves it open.
        Assert.True(stream.CanRead);
        Assert.Equal(image.Length, stream.Length);
    }

    [Fact]
    public void AVolumeFromAStreamIsCheckedLikeOneFromAPath()
    {
        var image = OriginalContentSourceTests.BuildIso([1]);
        image[(16 * 2048) + 156 + 6] ^= 1;
        Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenIso9660(new MemoryStream(image)));
        Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenIso9660(new MemoryStream(new byte[16 * 2048])));
    }

    [Fact]
    public void AVolumeStreamThatCannotSeekOrReadIsRejected()
    {
        var image = OriginalContentSourceTests.BuildIso([1]);
        Assert.Throws<ArgumentException>(() => OriginalContentSource.OpenIso9660(new ForwardOnlyStream(image)));
        Assert.Throws<ArgumentException>(() => OriginalContentSource.OpenIso9660(new UnreadableStream()));
        Assert.Throws<ArgumentNullException>(() => OriginalContentSource.OpenIso9660((Stream)null!));
    }

    private static ContentOverlay Open(byte[] zip, ContentOverlayLimits? limits = null) =>
        ContentOverlay.OpenZip(new MemoryStream(zip, writable: false), limits);

    private static async Task<byte[]> ReadAllAsync(Stream stream)
    {
        await using (stream)
        {
            using var buffer = new MemoryStream();
            await stream.CopyToAsync(buffer, Token);
            return buffer.ToArray();
        }
    }

    private sealed class ForwardOnlyStream(byte[] bytes) : MemoryStream(bytes, writable: false)
    {
        public override bool CanSeek => false;
    }

    private sealed class UnreadableStream() : MemoryStream(new byte[16])
    {
        public override bool CanRead => false;
    }
}
