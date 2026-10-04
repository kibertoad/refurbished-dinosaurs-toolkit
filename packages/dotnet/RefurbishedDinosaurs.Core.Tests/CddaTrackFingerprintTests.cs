using System.Text;
using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class CddaTrackFingerprintTests
{
    private const int RawSector = CueBinSheet.RawSectorSize;
    private const int SamplesPerSector = CddaTrackFingerprint.SamplesPerSector;
    private const int Bytes = CddaTrackFingerprint.BytesPerSample;
    private const int DataSectors = 23;
    private const int TrackSectors = 20;
    private const int Tolerance = 700;
    private const int AnchorOffset = 1000;
    private const int AnchorSamples = SamplesPerSector;
    private const int TrackSamples = TrackSectors * SamplesPerSector;
    private static readonly byte[] Payload = [1, 2, 3, 4];

    // A data track of 23 sectors, then audio tracks 2 and 3 of 20 sectors each.
    private const string TwoTrackCue = "FILE \"game.bin\" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n" +
        "TRACK 02 AUDIO\nINDEX 01 00:00:23\nTRACK 03 AUDIO\nINDEX 01 00:00:43\n";

    // The audio both tracks hold on the disc, as seeded noise.
    private static readonly byte[] DiscAudio = Noise(2 * TrackSamples * Bytes, 158);

    [Fact]
    public async Task TracksShiftedWithinTheToleranceVerify()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var manifest = await RecordAsync(root);
            foreach (var shift in new[] { 0, 1, -1, 667, -667, Tolerance, -Tolerance })
            {
                await WriteAsync(root, Rip(shift), TwoTrackCue);
                var result = await AssetVerifier.VerifyAsync(root, manifest, TestContext.Current.CancellationToken);
                Assert.True(result.IsValid, $"shift {shift}: {string.Join("; ", result.Issues.Select(issue => issue.Detail))}");

                await using var image = File.OpenRead(Path.Combine(root, "game.bin"));
                var sheet = CueBinSheet.Parse(TwoTrackCue);
                foreach (var track in manifest.AudioTracks!)
                {
                    var verification = await CddaTrackFingerprints.VerifyAsync(image,
                        sheet.TrackExtent(track.Track, image.Length / RawSector), track, TestContext.Current.CancellationToken);
                    Assert.True(verification.IsMatch);
                    Assert.Equal(shift, verification.Shift);
                }
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData(Tolerance + 1)]
    [InlineData(-Tolerance - 1)]
    public async Task ATrackShiftedPastTheToleranceIsOutOfRange(int shift)
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var manifest = await RecordAsync(root);
            await WriteAsync(root, Rip(shift), TwoTrackCue);
            var result = await AssetVerifier.VerifyAsync(root, manifest, TestContext.Current.CancellationToken);
            Assert.Equal([(2, AssetProblem.AudioOffsetOutOfRange), (3, AssetProblem.AudioOffsetOutOfRange)],
                result.Issues.Select(issue => (issue.AudioTrack!.Value, issue.Problem)));
            Assert.All(result.Issues, issue => Assert.Null(issue.Path));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task AnAnchorThatMatchesAtTwoShiftsIsAmbiguous()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var manifest = await RecordAsync(root);
            var image = Rip(0);
            // Track 2's anchor repeats 650 samples later, inside the tolerance.
            var anchor = (DataSectors * SamplesPerSector + AnchorOffset) * Bytes;
            image.AsSpan(anchor, AnchorSamples * Bytes).CopyTo(image.AsSpan(anchor + 650 * Bytes));
            await WriteAsync(root, image, TwoTrackCue);

            var result = await AssetVerifier.VerifyAsync(root, manifest, TestContext.Current.CancellationToken);
            var issue = Assert.Single(result.Issues);
            Assert.Equal((2, AssetProblem.AudioAlignmentAmbiguous), (issue.AudioTrack!.Value, issue.Problem));
            Assert.Contains("0, 650", issue.Detail, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task ChangedCentralSamplesAreAHashMismatch()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var manifest = await RecordAsync(root);
            var image = Rip(-300);
            image[(DataSectors * SamplesPerSector + 5000) * Bytes] ^= 1;
            await WriteAsync(root, image, TwoTrackCue);

            var result = await AssetVerifier.VerifyAsync(root, manifest, TestContext.Current.CancellationToken);
            var issue = Assert.Single(result.Issues);
            Assert.Equal((2, AssetProblem.AudioHashMismatch), (issue.AudioTrack!.Value, issue.Problem));
            Assert.Contains("shift -300", issue.Detail, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task ALengthOutsideTheToleranceIsWrongSize()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var manifest = await RecordAsync(root);
            // Track 3 now starts two sectors early: track 2 loses 1176 samples and track 3 gains them.
            await WriteAsync(root, Rip(0), TwoTrackCue.Replace("00:00:43", "00:00:41", StringComparison.Ordinal));
            var result = await AssetVerifier.VerifyAsync(root, manifest, TestContext.Current.CancellationToken);
            Assert.Equal([(2, AssetProblem.WrongSize), (3, AssetProblem.WrongSize)],
                result.Issues.Select(issue => (issue.AudioTrack!.Value, issue.Problem)));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task AnEmptyTrackIsWrongSize()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var manifest = await RecordAsync(root);
            // Track 3's pregap begins at track 2's INDEX 01, so track 2 holds no sectors.
            await WriteAsync(root, Rip(0), TwoTrackCue.Replace("TRACK 03 AUDIO\n", "TRACK 03 AUDIO\nINDEX 00 00:00:23\n",
                StringComparison.Ordinal));
            var result = await AssetVerifier.VerifyAsync(root, manifest, TestContext.Current.CancellationToken);
            var issue = Assert.Single(result.Issues);
            Assert.Equal((2, AssetProblem.WrongSize), (issue.AudioTrack!.Value, issue.Problem));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task SamplesPastTheEndOfTheImageAreUnreadableAndNotAMismatch()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var manifest = await RecordAsync(root);
            // One sector shorter, so the last track is 588 samples short, inside the tolerance, and
            // its central samples at shift 667 run past the end of the image.
            await WriteAsync(root, Rip(667)[..^RawSector], TwoTrackCue);
            var result = await AssetVerifier.VerifyAsync(root, manifest, TestContext.Current.CancellationToken);
            var issue = Assert.Single(result.Issues);
            Assert.Equal((3, AssetProblem.Unreadable), (issue.AudioTrack!.Value, issue.Problem));
            Assert.Contains("not checked", issue.Detail, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task ATrackTheSheetLacksIsMissingAndADirectoryHoldsNoAudio()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var manifest = await RecordAsync(root);
            var withExtra = manifest with { AudioTracks = [.. manifest.AudioTracks!, manifest.AudioTracks![1] with { Track = 4 }] };
            var result = await AssetVerifier.VerifyAsync(root, withExtra, TestContext.Current.CancellationToken);
            var issue = Assert.Single(result.Issues);
            Assert.Equal((4, AssetProblem.Missing), (issue.AudioTrack!.Value, issue.Problem));

            var directory = Path.Combine(root, "copied");
            Directory.CreateDirectory(Path.Combine(directory, "EI"));
            await File.WriteAllBytesAsync(Path.Combine(directory, "EI", "TEST.BIN"), Payload, TestContext.Current.CancellationToken);
            using var source = OriginalContentSource.OpenDirectory(directory);
            result = await AssetVerifier.VerifyAsync(source, manifest, TestContext.Current.CancellationToken);
            Assert.Equal([(2, AssetProblem.Unreadable), (3, AssetProblem.Unreadable)],
                result.Issues.Select(found => (found.AudioTrack!.Value, found.Problem)));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task RecordingRefusesAnAnchorThatRepeatsWithinTheTolerance()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            // Silence matches at every shift.
            var image = Rip(0);
            image.AsSpan(DataSectors * RawSector).Clear();
            await WriteAsync(root, image, TwoTrackCue);
            var failure = await Assert.ThrowsAsync<ArgumentException>(() => CddaTrackFingerprints.RecordAsync(
                root, 2, Tolerance, AnchorOffset, AnchorSamples, TestContext.Current.CancellationToken));
            Assert.Contains("repeat", failure.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task RecordingRefusesAnAnchorThatRepeatsWithinTwiceTheTolerance()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            // 1000 samples later is past the tolerance, but a rip shifted by -700 would show the
            // verifier both copies, at shifts -700 and 300.
            var image = Rip(0);
            var anchor = (DataSectors * SamplesPerSector + AnchorOffset) * Bytes;
            image.AsSpan(anchor, AnchorSamples * Bytes).CopyTo(image.AsSpan(anchor + 1000 * Bytes));
            await WriteAsync(root, image, TwoTrackCue);
            var failure = await Assert.ThrowsAsync<ArgumentException>(() => CddaTrackFingerprints.RecordAsync(
                root, 2, Tolerance, AnchorOffset, AnchorSamples, TestContext.Current.CancellationToken));
            Assert.Contains("shifts 1000;", failure.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData(1, Tolerance, AnchorOffset, AnchorSamples)]
    [InlineData(4, Tolerance, AnchorOffset, AnchorSamples)]
    [InlineData(2, Tolerance, Tolerance - 1, AnchorSamples)]
    [InlineData(2, Tolerance, TrackSamples - AnchorSamples - Tolerance + 1, AnchorSamples)]
    [InlineData(2, CddaTrackFingerprint.MaximumToleranceSamples + 1, 6000, AnchorSamples)]
    [InlineData(2, Tolerance, AnchorOffset, 0)]
    [InlineData(2, -1, AnchorOffset, AnchorSamples)]
    public async Task RecordingRejectsTracksAndValuesThatCannotVerify(int track, int tolerance, int anchorOffset, int anchorSamples)
    {
        var root = CreateTemporaryDirectory();
        try
        {
            await WriteAsync(root, Rip(0), TwoTrackCue);
            await Assert.ThrowsAsync<ArgumentException>(() => CddaTrackFingerprints.RecordAsync(
                root, track, tolerance, anchorOffset, anchorSamples, TestContext.Current.CancellationToken));
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData(-5, 15)]
    [InlineData(30, 10)]
    public async Task AnExtentBeforeTheImageOrEndingBeforeItStartsIsRejected(long startSector, long endSector)
    {
        using var image = new MemoryStream(Rip(0));
        var extent = new CueBinTrackExtent(2, startSector, endSector);
        var track = new CddaTrackFingerprint(2, TrackSamples, Tolerance, AnchorOffset, AnchorSamples,
            new string('0', FileFingerprint.Xxh3Length), new string('0', FileFingerprint.Xxh3Length));
        Assert.Equal("extent", (await Assert.ThrowsAsync<ArgumentException>(() => CddaTrackFingerprints.VerifyAsync(
            image, extent, track, TestContext.Current.CancellationToken))).ParamName);
        Assert.Equal("extent", (await Assert.ThrowsAsync<ArgumentException>(() => CddaTrackFingerprints.RecordAsync(
            image, extent, Tolerance, AnchorOffset, AnchorSamples, TestContext.Current.CancellationToken))).ParamName);
    }

    [Fact]
    public async Task RecordedHashesUseTheFileFingerprintForm()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var manifest = await RecordAsync(root);
            var track = manifest.AudioTracks![0];
            Assert.Equal(new CddaTrackFingerprint(2, TrackSamples, Tolerance, AnchorOffset, AnchorSamples,
                FileFingerprint.Xxh3(DiscAudio.AsSpan(AnchorOffset * Bytes, AnchorSamples * Bytes)),
                FileFingerprint.Xxh3(DiscAudio.AsSpan(Tolerance * Bytes, (TrackSamples - 2 * Tolerance) * Bytes))), track);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void TrackExtentsEndAtTheNextPregapOrTrackOrTheImage()
    {
        var sheet = CueBinSheet.Parse("FILE game.bin BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n" +
            "TRACK 02 AUDIO\nINDEX 01 00:00:23\nTRACK 03 AUDIO\nINDEX 00 00:00:43\nINDEX 01 00:00:45\n" +
            "TRACK 04 AUDIO\nINDEX 01 00:00:60\n");
        Assert.Equal(new CueBinTrackExtent(1, 0, 23), sheet.TrackExtent(1, 70));
        Assert.Equal(new CueBinTrackExtent(2, 23, 43), sheet.TrackExtent(2, 70));
        Assert.Equal(new CueBinTrackExtent(3, 45, 60), sheet.TrackExtent(3, 70));
        Assert.Equal(new CueBinTrackExtent(4, 60, 70), sheet.TrackExtent(4, 70));
        Assert.Equal(10, sheet.TrackExtent(4, 70).Sectors);
        Assert.Throws<ArgumentOutOfRangeException>(() => sheet.TrackExtent(0, 70));
        Assert.Throws<ArgumentOutOfRangeException>(() => sheet.TrackExtent(5, 70));
        Assert.Equal(0, sheet.TrackExtent(4, 60).Sectors);
        Assert.Throws<InvalidDataException>(() => sheet.TrackExtent(4, 59));
        Assert.Throws<InvalidDataException>(() => sheet.TrackExtent(3, 50));
    }

    [Fact]
    public void ManifestReadsAudioTracksAndAddsThemToTheFingerprint()
    {
        var anchor = new string('a', 32);
        var central = new string('b', 32);
        var json = $$"""
            { "gameId": "game", "sourceEdition": "retail", "sourceKind": "cue-bin",
              "files": [ { "path": "GAME.DAT", "size": 3 } ],
              "audioTracks": [ { "track": 2, "samples": 11760, "toleranceSamples": 700, "anchorOffset": 1000,
                                 "anchorSamples": 588, "anchorXxh3": "{{anchor}}", "centralXxh3": "{{central}}" } ] }
            """;
        var manifest = AssetManifest.Load(new MemoryStream(Encoding.UTF8.GetBytes(json)));
        Assert.Equal(new CddaTrackFingerprint(2, 11760, 700, 1000, 588, anchor, central), Assert.Single(manifest.AudioTracks!));

        var filesOnly = manifest with { AudioTracks = null };
        Assert.NotEqual(filesOnly.Fingerprint(), manifest.Fingerprint());
        Assert.Equal(filesOnly.Fingerprint(), (manifest with { AudioTracks = [] }).Fingerprint());
        Assert.NotEqual(manifest.Fingerprint(),
            (manifest with { AudioTracks = [manifest.AudioTracks![0] with { ToleranceSamples = 699 }] }).Fingerprint());
    }

    private static readonly CddaTrackFingerprint ValidTrack = new(2, TrackSamples, Tolerance, AnchorOffset,
        AnchorSamples, new string('a', 32), new string('b', 32));

    private static readonly (string SourceKind, CddaTrackFingerprint Track)[] InvalidAudioTracks =
    [
        ("iso9660", ValidTrack),
        ("directory", ValidTrack),
        ("cue-bin", ValidTrack with { Track = 1 }),
        ("cue-bin", ValidTrack with { Track = 100 }),
        ("cue-bin", ValidTrack with { Samples = long.MinValue }),
        ("cue-bin", ValidTrack with { ToleranceSamples = CddaTrackFingerprint.MaximumToleranceSamples + 1 }),
        ("cue-bin", ValidTrack with { AnchorSamples = CddaTrackFingerprint.MaximumAnchorSamples + 1 }),
        ("cue-bin", ValidTrack with { AnchorOffset = Tolerance - 1 }),
        ("cue-bin", ValidTrack with { Samples = AnchorOffset + AnchorSamples + Tolerance - 1 }),
        ("cue-bin", ValidTrack with { AnchorXxh3 = new string('A', 32) }),
        ("cue-bin", ValidTrack with { CentralXxh3 = "" }),
    ];

    [Fact]
    public void ManifestRejectsAudioTracksThatCannotVerify()
    {
        new AssetManifest("game", "retail", [new("GAME.DAT", 3)], "cue-bin") { AudioTracks = [ValidTrack] }.Validate();
        foreach (var (sourceKind, track) in InvalidAudioTracks)
        {
            var manifest = new AssetManifest("game", "retail", [new("GAME.DAT", 3)], sourceKind) { AudioTracks = [track] };
            Assert.Throws<InvalidDataException>(manifest.Validate);
        }
    }

    [Fact]
    public void ManifestRejectsADuplicateOrNullAudioTrack()
    {
        var track = ValidTrack;
        var manifest = new AssetManifest("game", "retail", [new("GAME.DAT", 3)], "cue-bin");
        manifest.Validate();
        Assert.Throws<InvalidDataException>((manifest with { AudioTracks = [track, track] }).Validate);
        Assert.Throws<InvalidDataException>((manifest with { AudioTracks = [null!] }).Validate);
    }

    internal static async Task<AssetManifest> RecordAsync(string root)
    {
        await WriteAsync(root, Rip(0), TwoTrackCue);
        var tracks = new List<CddaTrackFingerprint>();
        foreach (var track in new[] { 2, 3 })
            tracks.Add(await CddaTrackFingerprints.RecordAsync(Path.Combine(root, "game.cue"), track,
                Tolerance, AnchorOffset, AnchorSamples, TestContext.Current.CancellationToken));
        return new AssetManifest("game", "retail",
            [new("EI/TEST.BIN", Payload.Length, FileFingerprint.Xxh3(Payload))], ContentSourceKinds.CueBin)
        { AudioTracks = tracks };
    }

    // The image a drive whose read offset is off by `shift` samples produces: the data track, then
    // the disc's audio moved `shift` samples later, with silence where the rip read past the audio.
    private static byte[] Rip(int shift)
    {
        var data = CueBinSourceTests.ToRaw(OriginalContentSourceTests.BuildIso(Payload));
        Assert.Equal(DataSectors * RawSector, data.Length);
        var image = new byte[data.Length + DiscAudio.Length];
        data.CopyTo(image, 0);
        var audio = image.AsSpan(data.Length);
        var offset = shift * Bytes;
        if (offset >= 0) DiscAudio.AsSpan(0, DiscAudio.Length - offset).CopyTo(audio[offset..]);
        else DiscAudio.AsSpan(-offset).CopyTo(audio);
        return image;
    }

    private static byte[] Noise(int length, int seed)
    {
        var bytes = new byte[length];
        new Random(seed).NextBytes(bytes);
        return bytes;
    }

    private static async Task WriteAsync(string root, byte[] bin, string cue)
    {
        await File.WriteAllBytesAsync(Path.Combine(root, "game.bin"), bin, TestContext.Current.CancellationToken);
        await File.WriteAllTextAsync(Path.Combine(root, "game.cue"), cue, TestContext.Current.CancellationToken);
    }

    internal static string CreateTemporaryDirectory()
    {
        var root = Path.Combine(Path.GetTempPath(), "cdda-fingerprint-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }
}
