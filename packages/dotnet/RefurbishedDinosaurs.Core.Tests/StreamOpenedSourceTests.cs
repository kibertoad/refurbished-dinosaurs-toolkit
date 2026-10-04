using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;
using static RefurbishedDinosaurs.Core.Tests.OverlayFixtures;

namespace RefurbishedDinosaurs.Core.Tests;

// ContentOverlay.OpenZip(Stream) and OriginalContentSource.OpenIso9660(Stream) against the same bytes
// opened from a path.
public sealed class StreamOpenedSourceTests : IDisposable
{
    private readonly string _work = Directory.CreateTempSubdirectory("stream-source-tests-").FullName;

    public void Dispose() => Directory.Delete(_work, recursive: true);

    private static CancellationToken Token => TestContext.Current.CancellationToken;

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
        // The archive starts at position 0 wherever the stream is positioned, such as at the end of a
        // zip just written to it.
        stream.Position = stream.Length;
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
        Assert.Equal(Added, File.ReadAllBytes(Path.Combine(fromStreamContent, "extra", "new", "added.dat")));
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

        // A rejected overlay leaves the caller's stream open too.
        using var rejected = new MemoryStream(ZipBytes(two, twoPayloads), writable: false);
        Assert.Throws<InvalidDataException>(() => ContentOverlay.OpenZip(rejected, new(MaximumFiles: 1)));
        Assert.True(rejected.CanRead);
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
        // Logical block 0 is at position 0 wherever the stream is positioned.
        stream.Position = stream.Length;
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
    public async Task SourcesOverOneStreamCanBeReadAtTheSameTime()
    {
        var payload = Enumerable.Range(0, 2048).Select(value => (byte)(value * 7)).ToArray();
        var image = OriginalContentSourceTests.BuildIso(payload);
        using var stream = new MemoryStream(image, writable: false);
        using var first = OriginalContentSource.OpenIso9660(stream);
        using var second = OriginalContentSource.OpenIso9660(stream);

        async Task ReadInSmallChunks(OriginalContentSource source, string path, byte[] expected)
        {
            for (var round = 0; round < 20; round++)
            {
                await using var read = path.Length == 0 ? source.OpenVolume() : source.OpenRead(path);
                var actual = new byte[expected.Length];
                var offset = 0;
                while (offset < actual.Length)
                    offset += await read.ReadAsync(actual.AsMemory(offset, Math.Min(7, actual.Length - offset)), Token);
                Assert.Equal(expected, actual);
            }
        }

        await Task.WhenAll(
            Task.Run(() => ReadInSmallChunks(first, "EI/TEST.BIN", payload), Token),
            Task.Run(() => ReadInSmallChunks(second, "EI/TEST.BIN", payload), Token),
            Task.Run(() => ReadInSmallChunks(first, "", image), Token),
            Task.Run(() => ReadInSmallChunks(second, "", image), Token));
    }

    [Fact]
    public void AStreamOpenedFromAStreamSourceCannotBeReadOnceDisposed()
    {
        using var stream = new MemoryStream(OriginalContentSourceTests.BuildIso([1, 2, 3]), writable: false);
        using var source = OriginalContentSource.OpenIso9660(stream);
        var file = source.OpenRead("EI/TEST.BIN");
        file.Dispose();
        Assert.Throws<ObjectDisposedException>(() => file.ReadByte());
        Assert.True(stream.CanRead);
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
