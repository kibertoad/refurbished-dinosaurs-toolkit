using System.Buffers.Binary;
using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class ChdSourceTests
{
    private const int RawSector = CueBinSheet.RawSectorSize;
    private const int Tolerance = 100;
    private const long AnchorOffset = 1000;
    private const int AnchorSamples = 588;

    private static readonly SyntheticChdTrack[] MixedTracks =
    [
        new("MODE1_RAW", SyntheticDiscImages.MixedDataSectors),
        new("AUDIO", SyntheticDiscImages.MixedTrack2Pregap + SyntheticDiscImages.MixedTrack2Sectors, SyntheticDiscImages.MixedTrack2Pregap),
        new("AUDIO", SyntheticDiscImages.MixedTrack3Sectors),
    ];

    // Writes the synthetic BIN images, cue sheets and .iso the CHD fixtures are made from into the
    // directory CHD_FIXTURE_SOURCES names. Run explicitly when making the fixtures again; see
    // Fixtures/Chd/README.md.
    [Fact(Explicit = true)]
    public void WriteFixtureSources()
    {
        var directory = Environment.GetEnvironmentVariable("CHD_FIXTURE_SOURCES");
        Assert.False(string.IsNullOrWhiteSpace(directory), "Set CHD_FIXTURE_SOURCES to the directory to write to.");
        SyntheticDiscImages.WriteSources(directory!);
    }

    [Theory]
    [InlineData("mixed-cdlz.chd")]
    [InlineData("mixed-cdzl.chd")]
    [InlineData("mixed-cdfl.chd")]
    public async Task MixedModeChdReadsAsTheCueBinItWasMadeFrom(string fixture)
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var cuePath = WriteCueBin(root, "mixed", SyntheticDiscImages.MixedBin(), SyntheticDiscImages.MixedCue);
            using var cueBin = OriginalContentSource.OpenCueBin(cuePath);
            using var chd = OriginalContentSource.OpenChd(Fixture(fixture));

            Assert.Equal(ContentSourceKinds.Chd, chd.Kind);
            await AssertSameVolumeAsync(cueBin, chd);
            Assert.Null(chd.CuePath);
            Assert.Null(chd.BinPath);
            Assert.Null(chd.CueSheetBytes);
            Assert.Equal(Path.GetFileNameWithoutExtension(fixture) + ".bin", chd.Cue!.ReferencedFile);
            Assert.Equal(cueBin.Cue!.DataTrackSectors, chd.Cue.DataTrackSectors);
            Assert.Equal(cueBin.Cue.Tracks.Select(Describe), chd.Cue.Tracks.Select(Describe));

            // The raw image equals the BIN, audio samples in the BIN's byte order and without
            // chdman's padding frames, and reading it to its end checks the CHD's SHA-1.
            var bin = await ReadAllAsync(chd.OpenBin());
            Assert.Equal(SyntheticDiscImages.MixedBin(), bin);

            var cueManifest = await RecordAsync(cuePath, cueBin);
            Assert.True((await AssetVerifier.VerifyAsync(cueBin, cueManifest, TestContext.Current.CancellationToken)).IsValid);
            var chdManifest = cueManifest with { SourceKind = ContentSourceKinds.Chd };
            var result = await AssetVerifier.VerifyAsync(Fixture(fixture), chdManifest, TestContext.Current.CancellationToken);
            Assert.True(result.IsValid, string.Join("; ", result.Issues.Select(issue => issue.Detail)));
            Assert.Equal(cueManifest.Fingerprint(), chdManifest.Fingerprint());

            await using var image = chd.OpenBin();
            foreach (var track in cueManifest.AudioTracks!)
            {
                var extent = chd.Cue.TrackExtent(track.Track, image.Length / RawSector);
                Assert.Equal(cueBin.Cue.TrackExtent(track.Track, SyntheticDiscImages.MixedBin().Length / RawSector), extent);
                var verification = await CddaTrackFingerprints.VerifyAsync(image, extent, track, TestContext.Current.CancellationToken);
                Assert.True(verification.IsMatch, verification.Detail);
                Assert.Equal(0, verification.Shift);
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData("mixed-cdlz.chd")]
    [InlineData("mixed-cdzl.chd")]
    [InlineData("data-mode2.chd")]
    public void CdCodecsRebuildTheSyncAndEccTheyRemoved(string fixture)
    {
        // chdman removes the sync pattern and ECC of every data sector whose ECC is correct, which
        // every synthetic data sector's is, and the reader rebuilds them; the comparisons with the
        // BIN show the rebuilt bytes are the original ones.
        using var file = File.OpenRead(Fixture(fixture));
        var image = ChdImage.Read(file);
        using var reader = new ChdHunkReader(image, File.OpenRead(Fixture(fixture)), new bool[image.HunkCount]);
        var hunk = new byte[image.HunkBytes];
        for (var index = 0; index < image.HunkCount; index++) reader.ReadHunk(index, hunk);
        Assert.True(reader.RegeneratedSectors > 0);
    }

    [Fact]
    public async Task DataOnlyMode2ChdReadsAsItsCueBinWithTheEmptyForm2Sector()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var cuePath = WriteCueBin(root, "data-mode2", SyntheticDiscImages.DataMode2Bin(), SyntheticDiscImages.DataMode2Cue);
            using var cueBin = OriginalContentSource.OpenCueBin(cuePath);
            using var chd = OriginalContentSource.OpenChd(Fixture("data-mode2.chd"));
            await AssertSameVolumeAsync(cueBin, chd);
            Assert.Equal("MODE2/2352", Assert.Single(chd.Cue!.Tracks).Type);
            // The empty Form 2 sector reads as zeros in the volume, as a MODE1 image holds there.
            var volume = await ReadAllAsync(chd.OpenVolume());
            Assert.False(volume.AsSpan(SyntheticDiscImages.DataMode2EmptyForm2Sector * 2048, 2048).ContainsAnyExcept((byte)0));
            Assert.Equal(SyntheticDiscImages.Iso(), volume);
            Assert.Equal(SyntheticDiscImages.DataMode2Bin(), await ReadAllAsync(chd.OpenBin()));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task ChdOfAnIsoReadsAsTheIso()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var isoPath = Path.Combine(root, "data.iso");
            await File.WriteAllBytesAsync(isoPath, SyntheticDiscImages.Iso(), TestContext.Current.CancellationToken);
            using var iso = OriginalContentSource.OpenIso9660(isoPath);
            using var chd = OriginalContentSource.OpenChd(Fixture("data-iso.chd"));
            await AssertSameVolumeAsync(iso, chd);
            Assert.Null(chd.Cue);
            Assert.Throws<NotSupportedException>(() => chd.OpenBin());
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task OpenRecognizesAChdByItsSignatureAndKind()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var renamed = Path.Combine(root, "disc.img");
            File.Copy(Fixture("mixed-cdlz.chd"), renamed);
            foreach (var path in new[] { Fixture("mixed-cdlz.chd"), renamed })
            {
                using var opened = OriginalContentSource.Open(path);
                Assert.Equal(ContentSourceKinds.Chd, opened.Kind);
                using var byKind = OriginalContentSource.Open(path, ContentSourceKinds.Chd);
                Assert.Equal(opened.Files, byKind.Files);
            }
            Assert.True(ContentSourceKinds.IsSupported("chd"));

            var payload = SyntheticDiscImages.Payload();
            var edition = new AssetManifest("game", "retail",
                [new("EI/TEST.BIN", payload.Length, FileFingerprint.Xxh3(payload))], ContentSourceKinds.Chd)
            { VolumeIdentifier = "SYNTHETIC_EI", VolumeBlocks = 23 };
            var identified = await AssetVerifier.IdentifyAsync(renamed, [edition, edition with { SourceKind = ContentSourceKinds.CueBin }],
                TestContext.Current.CancellationToken);
            Assert.Same(edition, identified.Edition);
            // A cue-bin edition opens the path as a cue sheet, which a CHD is not.
            Assert.Equal(AssetProblem.Unreadable, Assert.Single(Assert.Single(identified.Mismatches).Issues).Problem);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task UncompressedStoredAndRepeatedHunksRead()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var bin = SyntheticDiscImages.MixedBin();
            foreach (var options in new SyntheticChdOptions[]
            {
                new() { Uncompressed = true },
                new() { Deflate = false },
                new() { FramesPerHunk = 1 },
                new() { TrackTag = "CHTR", SelfReferences = false },
            })
            {
                var path = Path.Combine(root, "made.chd");
                var tracks = options.TrackTag == "CHTR"
                    // The old track metadata records no pregap, so the BIN's pregap is track 02's start.
                    ? MixedTracks.Select(track => track with { StoredPregap = 0 }).ToArray() : MixedTracks;
                await File.WriteAllBytesAsync(path, SyntheticChdWriter.Write(bin, tracks, options), TestContext.Current.CancellationToken);
                using var chd = OriginalContentSource.OpenChd(path);
                Assert.Equal("SYNTHETIC_EI", chd.Label);
                Assert.Equal(bin, await ReadAllAsync(chd.OpenBin()));
                Assert.Equal(SyntheticDiscImages.Iso(), await ReadAllAsync(chd.OpenVolume()));
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task HunkWithAWrongCrcFailsInsteadOfReading()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var bin = SyntheticDiscImages.MixedBin();
            var path = Path.Combine(root, "made.chd");

            // Hunk 2 holds sector 16, the volume descriptor the source reads when it opens.
            await File.WriteAllBytesAsync(path, SyntheticChdWriter.Write(bin, MixedTracks, new() { WrongCrcHunk = 2 }),
                TestContext.Current.CancellationToken);
            var refused = Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenChd(path));
            Assert.Contains("hunk 2 fails its CRC-16 check", refused.Message);

            // Hunk 5 holds audio, read only through OpenBin.
            await File.WriteAllBytesAsync(path, SyntheticChdWriter.Write(bin, MixedTracks, new() { WrongCrcHunk = 5 }),
                TestContext.Current.CancellationToken);
            using var chd = OriginalContentSource.OpenChd(path);
            var failed = await Assert.ThrowsAsync<InvalidDataException>(() => ReadAllAsync(chd.OpenBin()));
            Assert.Contains("hunk 5 fails its CRC-16 check", failed.Message);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task DataThatDoesNotMatchTheHeaderSha1FailsWhenReadToTheEnd()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var path = PatchedFixture(root, "mixed-cdlz.chd", bytes => bytes[64] ^= 0x01);
            using var chd = OriginalContentSource.OpenChd(path);
            // Each hunk passes its CRC, so the volume reads; the whole data's SHA-1 is checked when a
            // stream reads every hunk.
            Assert.Equal(SyntheticDiscImages.Iso(), await ReadAllAsync(chd.OpenVolume()));
            var failed = await Assert.ThrowsAsync<InvalidDataException>(() => ReadAllAsync(chd.OpenBin()));
            Assert.Contains("SHA-1", failed.Message);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void UnsupportedChdsAreRefusedWithTheReason()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            AssertRefused<NotSupportedException>(PatchedFixture(root, "mixed-cdlz.chd", bytes => bytes[104] = 0x12), "parent CHD");
            AssertRefused<NotSupportedException>(PatchedFixture(root, "mixed-cdlz.chd", bytes => "cdzs"u8.CopyTo(bytes.AsSpan(16))), "codec 'cdzs'");
            AssertRefused<NotSupportedException>(
                PatchedFixture(root, "mixed-cdlz.chd", bytes => BinaryPrimitives.WriteUInt32BigEndian(bytes.AsSpan(12), 4)), "version 4");

            var bin = SyntheticDiscImages.MixedBin();
            AssertRefused<NotSupportedException>(Made(root, bin, MixedTracks, new() { TrackTag = "CHGD" }), "GD-ROM");
            AssertRefused<NotSupportedException>(
                Made(root, bin, MixedTracks, new() { TrackTag = "CHGD", ExtraMetadata = [("CHT2", "TRACK:1 TYPE:MODE1_RAW SUBTYPE:NONE FRAMES:30 PREGAP:0 PGTYPE:MODE1 PGSUB:NONE POSTGAP:0")] }),
                "GD-ROM");
            AssertRefused<NotSupportedException>(Made(root, bin, MixedTracks, new() { TrackTag = "CHCD" }), "CHCD");
            AssertRefused<NotSupportedException>(Made(root, bin, MixedTracks, new() { TrackTag = "ABCD" }), "no CD track metadata");
            AssertRefused<NotSupportedException>(
                Made(root, bin, [new("MODE2_FORM1", 30), MixedTracks[1], MixedTracks[2]]), "MODE2_FORM1");
            AssertRefused<NotSupportedException>(
                Made(root, bin, [MixedTracks[2], new("MODE1_RAW", 30), MixedTracks[1]]), "First cue track must be MODE1/2352 or MODE2/2352");
            AssertRefused<NotSupportedException>(
                Made(root, bin, [new("MODE1", 30), MixedTracks[1], MixedTracks[2]]), "other tracks follow it");
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void DamagedChdsAreRefused()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var original = File.ReadAllBytes(Fixture("mixed-cdzl.chd"));
            var truncated = Path.Combine(root, "truncated.chd");
            File.WriteAllBytes(truncated, original[..(original.Length / 2)]);
            AssertRefused<InvalidDataException>(truncated, "truncated");
            AssertRefused<InvalidDataException>(PatchedFixture(root, "mixed-cdzl.chd", bytes => bytes[0] = (byte)'X'), "signature");

            // A map whose recorded CRC-16 differs from its entries fails.
            var mapOffset = BinaryPrimitives.ReadInt64BigEndian(original.AsSpan(40));
            AssertRefused<InvalidDataException>(
                PatchedFixture(root, "mixed-cdzl.chd", bytes => bytes[mapOffset + 10] ^= 0x10), "hunk map fails its CRC-16 check");
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task ReadingAChdThatChangedAfterOpeningFails()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var path = Path.Combine(root, "disc.chd");
            File.Copy(Fixture("mixed-cdfl.chd"), path);
            using var chd = OriginalContentSource.OpenChd(path);
            File.SetLastWriteTimeUtc(path, File.GetLastWriteTimeUtc(path).AddMinutes(1));
            Assert.Throws<IOException>(() => chd.OpenBin());
            await Assert.ThrowsAsync<IOException>(async () => await ReadAllAsync(chd.OpenRead("EI/TEST.BIN")));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void ChdManifestsMayPinTheVolumeAndListAudioTracks()
    {
        var track = new CddaTrackFingerprint(2, 5880, Tolerance, AnchorOffset, AnchorSamples,
            new string('1', FileFingerprint.Xxh3Length), new string('2', FileFingerprint.Xxh3Length));
        var manifest = new AssetManifest("game", "retail", [new("EI/TEST.BIN", 1)], ContentSourceKinds.Chd)
        {
            VolumeIdentifier = "SYNTHETIC_EI",
            VolumeBlocks = 23,
            AudioTracks = [track],
        };
        manifest.Validate();
        var directory = manifest with { SourceKind = ContentSourceKinds.Directory, VolumeIdentifier = null, VolumeBlocks = null };
        Assert.Contains("only 'cue-bin' and 'chd'", Assert.Throws<InvalidDataException>(directory.Validate).Message);
    }

    private static async Task AssertSameVolumeAsync(OriginalContentSource expected, OriginalContentSource actual)
    {
        Assert.Equal(expected.Label, actual.Label);
        Assert.Equal(expected.VolumeBlocks, actual.VolumeBlocks);
        Assert.Equal(expected.Files, actual.Files);
        foreach (var file in expected.Files)
            Assert.Equal(await ReadAllAsync(expected.OpenRead(file.Path)), await ReadAllAsync(actual.OpenRead(file.Path)));
        Assert.Equal(await VolumeXxh3Async(expected), await VolumeXxh3Async(actual));
    }

    private static async Task<AssetManifest> RecordAsync(string cuePath, OriginalContentSource source)
    {
        var tracks = new List<CddaTrackFingerprint>();
        foreach (var track in new[] { 2, 3 })
            tracks.Add(await CddaTrackFingerprints.RecordAsync(cuePath, track, Tolerance, AnchorOffset, AnchorSamples,
                TestContext.Current.CancellationToken));
        var payload = SyntheticDiscImages.Payload();
        return new AssetManifest("game", "retail",
            [new("EI/TEST.BIN", payload.Length, FileFingerprint.Xxh3(payload))], ContentSourceKinds.CueBin)
        {
            AudioTracks = tracks,
            VolumeIdentifier = source.Label,
            VolumeBlocks = source.VolumeBlocks,
            VolumeXxh3 = await VolumeXxh3Async(source),
        };
    }

    private static async Task<string> VolumeXxh3Async(OriginalContentSource source)
    {
        await using var volume = source.OpenVolume();
        return await FileFingerprint.Xxh3Async(volume, TestContext.Current.CancellationToken);
    }

    private static string Describe(CueBinTrack track) =>
        $"{track.Number} {track.Type} {string.Join(",", track.Indices.OrderBy(index => index.Key).Select(index => $"{index.Key}={index.Value}"))}";

    private static void AssertRefused<T>(string path, string reason) where T : Exception
    {
        var refused = Assert.Throws<T>(() => OriginalContentSource.OpenChd(path));
        Assert.Contains(reason, refused.Message);
    }

    private static string Made(string root, byte[] bin, IReadOnlyList<SyntheticChdTrack> tracks, SyntheticChdOptions? options = null)
    {
        var path = Path.Combine(root, $"made-{Guid.NewGuid():N}.chd");
        File.WriteAllBytes(path, SyntheticChdWriter.Write(bin, tracks, options));
        return path;
    }

    private static string PatchedFixture(string root, string fixture, Action<byte[]> patch)
    {
        var bytes = File.ReadAllBytes(Fixture(fixture));
        patch(bytes);
        var path = Path.Combine(root, $"patched-{Guid.NewGuid():N}.chd");
        File.WriteAllBytes(path, bytes);
        return path;
    }

    private static string Fixture(string name) => Path.Combine(AppContext.BaseDirectory, "Fixtures", "Chd", name);

    private static string WriteCueBin(string root, string name, byte[] bin, string cue)
    {
        File.WriteAllBytes(Path.Combine(root, name + ".bin"), bin);
        var cuePath = Path.Combine(root, name + ".cue");
        File.WriteAllText(cuePath, cue);
        return cuePath;
    }

    private static async Task<byte[]> ReadAllAsync(Stream stream)
    {
        await using (stream)
        {
            using var copy = new MemoryStream();
            await stream.CopyToAsync(copy, TestContext.Current.CancellationToken);
            return copy.ToArray();
        }
    }

    private static string CreateTemporaryDirectory()
    {
        var root = Path.Combine(Path.GetTempPath(), "chd-source-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }
}
