using System.Text.Json;
using System.Buffers.Binary;
using Toad.Discovery.Core.Assets;
using Toad.Discovery.Core.Determinism;
using Toad.Discovery.Core.Discovery;
using Toad.Discovery.Core.Imaging;
using Toad.Discovery.Core.IO;
using Toad.Discovery.Core.Paths;
using Toad.Discovery.Core.Persistence;
using Toad.Discovery.Core.Presentation;
using Toad.Discovery.Core.Validation;
using Toad.Discovery.LegacyFormats;
using Xunit;

namespace Toad.Discovery.Core.Tests;

public sealed class CoreTests
{
    [Fact]
    public void ManifestRejectsTraversal()
    {
        var manifest = new AssetManifest("game", "edition", [new("../secret.dat", 1)]);
        Assert.Throws<InvalidDataException>(manifest.Validate);
    }

    [Fact]
    public async Task VerifierReportsWrongSizeBeforeHashing()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            await File.WriteAllTextAsync(
                Path.Combine(root, "GAME.DAT"), "abc", TestContext.Current.CancellationToken);
            var manifest = new AssetManifest("game", "edition", [new("GAME.DAT", 4, new string('0', 64))]);
            var result = await AssetVerifier.VerifyAsync(
                root, manifest, TestContext.Current.CancellationToken);
            Assert.Equal(AssetProblem.WrongSize, Assert.Single(result.Issues).Problem);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void StagedPackDoesNotReplaceDestinationUntilCommit()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var destination = Path.Combine(root, "UserContent");
            Directory.CreateDirectory(destination);
            File.WriteAllText(Path.Combine(destination, "old.txt"), "old");
            using var pack = StagedAssetPack.Create(destination);
            File.WriteAllText(Path.Combine(pack.StagingDirectory, "new.txt"), "new");
            Assert.True(File.Exists(Path.Combine(destination, "old.txt")));
            pack.Commit();
            Assert.True(File.Exists(Path.Combine(destination, "new.txt")));
            Assert.False(File.Exists(Path.Combine(destination, "old.txt")));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void PathsPreferContentBesideApplication()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var app = Path.Combine(root, "Game");
            var content = Path.Combine(app, "UserContent");
            Directory.CreateDirectory(content);
            var result = RestorationPaths.ResolveImportedContent(
                new("ExampleGame"), app, root, Path.Combine(root, "Local"));
            Assert.Equal(content, result);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void SixBitPaletteExpandsChannels()
    {
        var data = new byte[IndexedPalette.ByteSize];
        data[0] = 63;
        Assert.Equal((byte)255, IndexedPaletteDecoder.Decode(data, 6)[0].R);
    }

    [Fact]
    public void SafePathRejectsSiblingPrefixAndTraversal()
    {
        var root = Path.Combine(Path.GetTempPath(), "assets");
        Assert.Throws<InvalidDataException>(() => SafePath.Below(root, "../assets-elsewhere/file.dat"));
        Assert.Equal(Path.Combine(root, "nested", "file.dat"), SafePath.Below(root, "nested/file.dat"));
    }

    [Fact]
    public void MsvcRandomHasStableStateAndSequence()
    {
        var random = new MsvcRandom(1);
        Assert.Equal([41, 18467, 6334], [random.NextRaw(), random.NextRaw(), random.NextRaw()]);
        Assert.Equal(3, random.ConsumptionCount);
        var resumed = new MsvcRandom(random.State, random.ConsumptionCount);
        Assert.Equal(random.NextRaw(), resumed.NextRaw());
    }

    [Fact]
    public void JsonStateDifferProducesStableLabeledPaths()
    {
        using var expected = JsonDocument.Parse("{\"players\":[{\"cash\":10}]}");
        using var actual = JsonDocument.Parse("{\"players\":[{\"cash\":12}],\"turn\":2}");
        var differences = JsonStateDiffer.Compare(expected.RootElement, actual.RootElement,
            new Dictionary<string, string> { ["$.players"] = "Players" });
        Assert.Equal(["$.players[0].cash", "$.turn"], differences.Select(x => x.Path));
        Assert.Equal("Players[0].cash", differences[0].Label);
    }

    [Fact]
    public void ViewportScalingLetterboxesAndMapsCoordinates()
    {
        var destination = ViewportScaler.Destination(1920, 1080, 640, 460, integerScaling: true);
        Assert.Equal(new ViewportScalerResult(320, 80, 1280, 920),
            new(destination.X, destination.Y, destination.Width, destination.Height));
        Assert.Equal((320, 230), ViewportScaler.ToLogical(960, 540, destination, 640, 460));
        Assert.Equal("one two\nthree", FixedWidthText.Wrap("one two three", 7));
    }

    [Fact]
    public void CueAndCddaHelpersPreserveDiscTiming()
    {
        string[] cue = ["TRACK 01 MODE1/2352", "INDEX 01 00:00:00", "TRACK 02 AUDIO", "INDEX 01 00:02:00"];
        Assert.Equal(150, CueSheet.DataTrackSectors(cue));
        using var source = new MemoryStream(new byte[CddaWave.BytesPerSector * 2]);
        using var output = new MemoryStream();
        CddaWave.Write(source, output, 1, 1);
        Assert.Equal(CddaWave.BytesPerSector + 44, output.Length);
    }

    [Fact]
    public void RawIndexedImageConvertsPaletteToRgba()
    {
        var palette = new byte[IndexedPalette.ByteSize]; palette[3] = 10; palette[4] = 20; palette[5] = 30;
        var image = RawIndexedImageDecoder.Decode([1, 0], palette, 2, 1);
        Assert.Equal([10, 20, 30, 255, 0, 0, 0, 255], image.ToRgba());
    }

    [Fact]
    public void SmackerIndexAndFrameSegmentsAreBounded()
    {
        var source = SyntheticSmacker();
        var movie = SmackerMovieDecoder.Decode(source);
        Assert.Equal((2, 196, 204, 2), (movie.Version, movie.Width, movie.Height, movie.Frames.Count));
        var first = SmackerMovieDecoder.DecodeFrameLayout(movie, 0, source, new byte[768]);
        Assert.True(first.PaletteChanged);
        Assert.Equal((126, 4), (first.Video.Offset, first.Video.Length));
        using var stream = new MemoryStream(source);
        using var reader = new SmackerMovieStream(stream, leaveOpen: true);
        Assert.Equal(12, reader.ReadFrame(1, new byte[reader.MaximumFrameLength]));
    }

    [Fact]
    public void SmackerPackedMonoAudioDecodesPredictiveSamples()
    {
        var packet = new byte[7];
        BinaryPrimitives.WriteUInt32LittleEndian(packet, 3);
        packet[4] = 0x29; packet[5] = 0xA0; packet[6] = 0x02;
        var decoded = SmackerAudioDecoder.Decode(packet,
            new SmackerAudioTrack(0, 22050, 4096, true, false, false));
        Assert.Equal([10, 11, 12], decoded.Samples);
        Assert.Equal([0, 0x8A, 0, 0x8B, 0, 0x8C], decoded.ToPcm16LittleEndian());
    }

    [Fact]
    public void JsonSettingsStoreFallsBackToBackup()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var path = Path.Combine(root, "settings.json");
            var store = new JsonSettingsStore<TestSettings>(path);
            store.Save(new(1, 0.5f), value => value.Version == 1);
            store.Save(new(1, 0.75f), value => value.Version == 1);
            File.WriteAllText(path, "not json");
            Assert.Equal(0.5f, store.Load(() => new(1, 1), value => value.Version == 1).Volume);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task InstalledManifestVerifierChecksGeneratedOutput()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var path = Path.Combine(root, "Decoded", "asset.bin");
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            await File.WriteAllBytesAsync(path, [1, 2, 3], TestContext.Current.CancellationToken);
            var hash = await FileFingerprint.Sha256Async(path, TestContext.Current.CancellationToken);
            var manifest = new InstalledAssetManifest(1, "game", "retail-disc",
                new string('a', 64), DateTimeOffset.UtcNow,
                [new("Decoded/asset.bin", 3, hash, "GAME.DAT", "application/octet-stream")], "1.0.0");
            var result = await InstalledAssetVerifier.VerifyAsync(
                root, manifest, 1, cancellationToken: TestContext.Current.CancellationToken);
            Assert.True(result.IsValid);
            Assert.Equal(1, result.VerifiedFiles);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void DiskPlannerAndSourceDiscoveryAreDeterministic()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            File.WriteAllBytes(Path.Combine(root, "existing.bin"), [1]);
            var plan = ImportDiskPlanner.Calculate(root,
                [new("existing.bin", 400), new("new/one.bin", 100), new("new/two.bin", 200)]);
            Assert.Equal((700, 300, 400, 700),
                (plan.InstalledBytes, plan.NewBytes, plan.ReplacementScratchBytes, plan.RequiredAvailableBytes));
            var locator = new CompositeSourceLocator([
                new KnownDirectorySourceLocator([new(root, "manual", 100)]),
                new KnownDirectorySourceLocator([new(root, "known", 10)])]);
            Assert.Equal("manual", Assert.Single(locator.FindCandidates()).Origin);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void InstalledWriterReusesFilesAndUninstallerPreservesUnlistedContent()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var first = InstalledContentWriter.WriteBytes(root, "Decoded/asset.bin", [1, 2, 3]);
            var second = InstalledContentWriter.WriteBytes(root, "Decoded/asset.bin", [1, 2, 3]);
            Assert.True(first.Changed);
            Assert.False(second.Changed);
            File.WriteAllText(Path.Combine(root, "notes.txt"), "keep me");
            File.WriteAllText(Path.Combine(root, "manifest.json"), "placeholder");
            var manifest = new InstalledAssetManifest(1, "game", "disc", new string('a', 64),
                DateTimeOffset.UtcNow,
                [new("Decoded/asset.bin", first.Bytes, first.Sha256, "SOURCE.DAT")], "1.0.0");

            Assert.Equal(1, InstalledContentUninstaller.Remove(root, manifest));
            Assert.True(File.Exists(Path.Combine(root, "notes.txt")));
            Assert.False(File.Exists(Path.Combine(root, "Decoded", "asset.bin")));
            Assert.False(File.Exists(Path.Combine(root, "manifest.json")));
        }
        finally { Directory.Delete(root, true); }
    }

    private static string CreateTemporaryDirectory()
    {
        var path = Path.Combine(Path.GetTempPath(), "toad-discovery-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        return path;
    }

    private static byte[] SyntheticSmacker()
    {
        var source = new byte[142];
        "SMK2"u8.CopyTo(source);
        BinaryPrimitives.WriteUInt32LittleEndian(source.AsSpan(4, 4), 196);
        BinaryPrimitives.WriteUInt32LittleEndian(source.AsSpan(8, 4), 204);
        BinaryPrimitives.WriteUInt32LittleEndian(source.AsSpan(12, 4), 2);
        BinaryPrimitives.WriteInt32LittleEndian(source.AsSpan(16, 4), 100);
        BinaryPrimitives.WriteUInt32LittleEndian(source.AsSpan(24, 4), 4096);
        for (var offset = 52; offset <= 68; offset += 4)
            BinaryPrimitives.WriteUInt32LittleEndian(source.AsSpan(offset, 4), 4);
        BinaryPrimitives.WriteUInt32LittleEndian(source.AsSpan(72, 4), 0xC000_0000u | 22050);
        BinaryPrimitives.WriteUInt32LittleEndian(source.AsSpan(104, 4), 13);
        BinaryPrimitives.WriteUInt32LittleEndian(source.AsSpan(108, 4), 12);
        source[112] = 1; source[113] = 2; source[118] = 2; source[122] = 0xFE; source[123] = 0xFF;
        BinaryPrimitives.WriteUInt32LittleEndian(source.AsSpan(130, 4), 8);
        BinaryPrimitives.WriteUInt32LittleEndian(source.AsSpan(134, 4), 3);
        return source;
    }

    private sealed record TestSettings(int Version, float Volume);
    private sealed record ViewportScalerResult(int X, int Y, int Width, int Height);
}
