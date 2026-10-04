using System.Buffers.Binary;
using System.Text;
using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class VolumePinTests
{
    private const int SectorSize = 2048;
    private const int DescriptorOffset = 16 * SectorSize;
    private const string Cue = "FILE \"game.bin\" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n";
    private static readonly byte[] Payload = "abc"u8.ToArray();

    [Fact]
    public async Task SourcesExposeTheVolumeAndAnIsoAndCueBinOfOneDiscReadTheSameBytes()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var cooked = OriginalContentSourceTests.BuildIso(Payload);
            // OpenVolume stops at the declared volume's end and leaves this padding out.
            var padded = cooked.Concat(new byte[SectorSize]).ToArray();
            var iso = await WriteAsync(root, "game.iso", padded);
            await WriteAsync(root, "game.bin", CueBinSourceTests.ToRaw(cooked));
            var cue = Path.Combine(root, "game.cue");
            await File.WriteAllTextAsync(cue, Cue, TestContext.Current.CancellationToken);

            using var isoSource = OriginalContentSource.OpenIso9660(iso);
            using var cueSource = OriginalContentSource.OpenCueBin(cue);
            using var directory = OriginalContentSource.OpenDirectory(root);
            var expected = FileFingerprint.Xxh3(cooked);
            foreach (var source in new[] { isoSource, cueSource })
            {
                Assert.Equal(23, source.VolumeBlocks);
                await using var volume = source.OpenVolume();
                Assert.Equal(23 * SectorSize, volume.Length);
                Assert.Equal(expected, await FileFingerprint.Xxh3Async(volume, TestContext.Current.CancellationToken));
            }
            Assert.Null(directory.VolumeBlocks);
            Assert.Throws<NotSupportedException>(() => directory.OpenVolume());
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task IdentifyTellsApartImagesWithTheSameFilesAndAnotherVolumeIdentifier()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var first = await WriteAsync(root, "first.iso", WithIdentifier(OriginalContentSourceTests.BuildIso(Payload), "PRESSING_A"));
            var second = await WriteAsync(root, "second.iso", WithIdentifier(OriginalContentSourceTests.BuildIso(Payload), "PRESSING_B"));
            var a = Edition("a") with { VolumeIdentifier = "PRESSING_A" };
            var b = Edition("b") with { VolumeIdentifier = "PRESSING_B" };

            var found = await AssetVerifier.IdentifyAsync(first, [b, a], TestContext.Current.CancellationToken);
            Assert.Same(a, found.Edition);
            var issue = Assert.Single(Assert.Single(found.Mismatches).Issues);
            Assert.Equal(AssetProblem.WrongVolumeIdentifier, issue.Problem);
            Assert.Null(issue.Path);
            Assert.Contains("'PRESSING_A'", issue.Detail);
            Assert.Same(b, (await AssetVerifier.IdentifyAsync(second, [a, b], TestContext.Current.CancellationToken)).Edition);

            // Without the pin the files alone cannot tell the pressings apart.
            var unpinned = await AssetVerifier.IdentifyAsync(first, [Edition("a"), Edition("b")],
                TestContext.Current.CancellationToken);
            Assert.True(unpinned.IsAmbiguous);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task IdentifyTellsApartImagesThatDifferOnlyInVolumeSize()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var shorter = await WriteAsync(root, "shorter.iso", OriginalContentSourceTests.BuildIso(Payload));
            var longer = await WriteAsync(root, "longer.iso", WithBlocks(OriginalContentSourceTests.BuildIso(Payload), 24));
            var small = Edition("small") with { VolumeBlocks = 23 };
            var large = Edition("large") with { VolumeBlocks = 24 };

            var found = await AssetVerifier.IdentifyAsync(longer, [small, large], TestContext.Current.CancellationToken);
            Assert.Same(large, found.Edition);
            var issue = Assert.Single(Assert.Single(found.Mismatches).Issues);
            Assert.Equal(AssetProblem.WrongVolumeSize, issue.Problem);
            Assert.Equal("Expected 23 logical blocks; found 24.", issue.Detail);
            Assert.Same(small, (await AssetVerifier.IdentifyAsync(shorter, [small, large], TestContext.Current.CancellationToken)).Edition);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task VerifierChecksTheVolumeHashOfACueBinImageBeforeItsFiles()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var cooked = OriginalContentSourceTests.BuildIso(Payload);
            await WriteAsync(root, "game.bin", CueBinSourceTests.ToRaw(cooked));
            var cue = Path.Combine(root, "game.cue");
            await File.WriteAllTextAsync(cue, Cue, TestContext.Current.CancellationToken);
            var manifest = Edition("retail", ContentSourceKinds.CueBin) with
            {
                VolumeIdentifier = "SYNTHETIC_EI",
                VolumeBlocks = 23,
                VolumeXxh3 = FileFingerprint.Xxh3(cooked)
            };
            Assert.True((await AssetVerifier.VerifyAsync(cue, manifest, TestContext.Current.CancellationToken)).IsValid);

            // A volume with other bytes fails its hash, and the files are still checked after it.
            var changed = manifest with { VolumeXxh3 = FileFingerprint.Xxh3("other"u8.ToArray()) };
            var spec = Assert.Single(manifest.Files);
            var withWrongFile = changed with { Files = [spec with { Xxh3 = FileFingerprint.Xxh3("abd"u8.ToArray()) }] };
            var result = await AssetVerifier.VerifyAsync(cue, withWrongFile, TestContext.Current.CancellationToken);
            Assert.Equal([AssetProblem.WrongVolumeHash, AssetProblem.WrongHash], result.Issues.Select(issue => issue.Problem));
            Assert.Null(result.Issues[0].Path);
            Assert.Equal("EI/TEST.BIN", result.Issues[1].Path);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task VerifierSkipsTheVolumeHashWhenTheIdentifierOrSizeAlreadyDiffers()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var iso = await WriteAsync(root, "game.iso", OriginalContentSourceTests.BuildIso(Payload));
            var manifest = Edition("retail") with
            {
                VolumeIdentifier = "OTHER",
                VolumeBlocks = 30,
                VolumeXxh3 = FileFingerprint.Xxh3("other"u8.ToArray())
            };
            var result = await AssetVerifier.VerifyAsync(iso, manifest, TestContext.Current.CancellationToken);
            Assert.Equal([AssetProblem.WrongVolumeIdentifier, AssetProblem.WrongVolumeSize],
                result.Issues.Select(issue => issue.Problem));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task VerifierReportsASourceWithoutAVolumeAndAVolumeItCannotRead()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            Directory.CreateDirectory(Path.Combine(root, "EI"));
            await File.WriteAllBytesAsync(Path.Combine(root, "EI", "TEST.BIN"), Payload, TestContext.Current.CancellationToken);
            using var directory = OriginalContentSource.OpenDirectory(root);
            var manifest = Edition("retail") with { VolumeBlocks = 23 };
            var issue = Assert.Single((await AssetVerifier.VerifyAsync(directory, manifest,
                TestContext.Current.CancellationToken)).Issues);
            Assert.Equal(AssetProblem.Unreadable, issue.Problem);
            Assert.Null(issue.Path);

            using var broken = new BrokenVolumeSource(directory);
            var hashed = Edition("retail") with { VolumeXxh3 = FileFingerprint.Xxh3(Payload) };
            issue = Assert.Single((await AssetVerifier.VerifyAsync(broken, hashed,
                TestContext.Current.CancellationToken)).Issues);
            Assert.Equal(AssetProblem.Unreadable, issue.Problem);
            Assert.Contains("volume could not be read", issue.Detail);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task VerifierReportsAnIsoImageThatShrankAfterOpeningAsUnreadable()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var cooked = OriginalContentSourceTests.BuildIso(Payload);
            var iso = await WriteAsync(root, "game.iso", cooked);
            using var source = OriginalContentSource.OpenIso9660(iso);
            await using (var file = new FileStream(iso, FileMode.Open, FileAccess.Write))
                file.SetLength(cooked.Length - SectorSize);

            var manifest = Edition("retail") with { VolumeXxh3 = FileFingerprint.Xxh3(cooked) };
            // The file sits in the volume's last block, so it is cut short as well.
            var issues = (await AssetVerifier.VerifyAsync(source, manifest, TestContext.Current.CancellationToken)).Issues;
            Assert.Equal([AssetProblem.Unreadable, AssetProblem.Unreadable], issues.Select(issue => issue.Problem));
            Assert.Equal([null, "EI/TEST.BIN"], issues.Select(issue => issue.Path));
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData("""{"gameId":"g","sourceEdition":"e","files":[{"path":"A","size":1}],"volumeIdentifier":"DISC"}""")]
    [InlineData("""{"gameId":"g","sourceEdition":"e","sourceKind":"directory","files":[{"path":"A","size":1}],"volumeBlocks":23}""")]
    [InlineData("""{"gameId":"g","sourceEdition":"e","files":[{"path":"A","size":1}],"volumeXxh3":"99aa06d3014798d86001c324468d497f"}""")]
    [InlineData("""{"gameId":"g","sourceEdition":"e","sourceKind":"iso9660","files":[{"path":"A","size":1}],"volumeIdentifier":""}""")]
    [InlineData("""{"gameId":"g","sourceEdition":"e","sourceKind":"iso9660","files":[{"path":"A","size":1}],"volumeIdentifier":"DISC "}""")]
    [InlineData("""{"gameId":"g","sourceEdition":"e","sourceKind":"iso9660","files":[{"path":"A","size":1}],"volumeIdentifier":"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"}""")]
    [InlineData("""{"gameId":"g","sourceEdition":"e","sourceKind":"iso9660","files":[{"path":"A","size":1}],"volumeIdentifier":"DIÉSC"}""")]
    [InlineData("""{"gameId":"g","sourceEdition":"e","sourceKind":"iso9660","files":[{"path":"A","size":1}],"volumeBlocks":17}""")]
    [InlineData("""{"gameId":"g","sourceEdition":"e","sourceKind":"cue-bin","files":[{"path":"A","size":1}],"volumeXxh3":"ABC"}""")]
    public void ManifestRejectsVolumePinsOutsideADiscImageAndInvalidValues(string json) =>
        Assert.Throws<InvalidDataException>(() => AssetManifest.Load(new MemoryStream(Encoding.UTF8.GetBytes(json))));

    [Fact]
    public void ManifestReadsVolumePinsAndTheyEnterTheFingerprint()
    {
        const string json = """
            {"gameId":"g","sourceEdition":"e","sourceKind":"cue-bin","files":[{"path":"A","size":1}],
             "volumeIdentifier":"DISC","volumeBlocks":23,"volumeXxh3":"99aa06d3014798d86001c324468d497f"}
            """;
        var manifest = AssetManifest.Load(new MemoryStream(Encoding.UTF8.GetBytes(json)));
        Assert.Equal("DISC", manifest.VolumeIdentifier);
        Assert.Equal(23, manifest.VolumeBlocks);
        Assert.Equal("99aa06d3014798d86001c324468d497f", manifest.VolumeXxh3);

        var unpinned = new AssetManifest("g", "e", [new("A", 1)]);
        Assert.NotEqual(unpinned.Fingerprint(), manifest.Fingerprint());
        Assert.NotEqual(manifest.Fingerprint(), (manifest with { VolumeIdentifier = "DISC2" }).Fingerprint());
        Assert.NotEqual(manifest.Fingerprint(), (manifest with { VolumeBlocks = 24 }).Fingerprint());
        Assert.NotEqual(manifest.Fingerprint(),
            (manifest with { VolumeXxh3 = FileFingerprint.Xxh3("other"u8.ToArray()) }).Fingerprint());
        // A manifest without pins keeps the fingerprint it had before pins existed.
        Assert.Equal(FileFingerprint.Xxh3(Encoding.UTF8.GetBytes("A\0" + "1\0")), unpinned.Fingerprint());
    }

    private static AssetManifest Edition(string name, string kind = ContentSourceKinds.Iso9660) =>
        new("game", name, [new("EI/TEST.BIN", Payload.Length, FileFingerprint.Xxh3(Payload))], kind);

    private static byte[] WithIdentifier(byte[] image, string identifier)
    {
        var field = image.AsSpan(DescriptorOffset + 40, 32);
        field.Fill((byte)' ');
        Encoding.ASCII.GetBytes(identifier).CopyTo(field);
        return image;
    }

    private static byte[] WithBlocks(byte[] image, uint blocks)
    {
        Array.Resize(ref image, checked((int)blocks * SectorSize));
        BinaryPrimitives.WriteUInt32LittleEndian(image.AsSpan(DescriptorOffset + 80), blocks);
        BinaryPrimitives.WriteUInt32BigEndian(image.AsSpan(DescriptorOffset + 84), blocks);
        return image;
    }

    private static async Task<string> WriteAsync(string root, string name, byte[] bytes)
    {
        var path = Path.Combine(root, name);
        await File.WriteAllBytesAsync(path, bytes, TestContext.Current.CancellationToken);
        return path;
    }

    private static string CreateTemporaryDirectory()
    {
        var path = Path.Combine(Path.GetTempPath(), "volume-pin-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        return path;
    }

    // Declares a volume whose bytes fail to read, over the files of another source.
    private sealed class BrokenVolumeSource(OriginalContentSource inner) : OriginalContentSource
    {
        public override string Kind => ContentSourceKinds.Iso9660;
        public override string? Label => "SYNTHETIC_EI";
        public override long? VolumeBlocks => 23;
        public override IReadOnlyList<ContentSourceEntry> Files => inner.Files;
        public override bool TryGetFile(string relativePath, out ContentSourceEntry? entry) =>
            inner.TryGetFile(relativePath, out entry);
        public override Stream OpenRead(string relativePath) => inner.OpenRead(relativePath);
        public override Stream OpenVolume() => throw new IOException("Synthetic read failure.");
        public override void Dispose() { }
    }
}
