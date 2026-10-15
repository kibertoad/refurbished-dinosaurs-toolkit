using System.Buffers.Binary;
using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;
using static RefurbishedDinosaurs.Core.Tests.SyntheticDiscImages;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class DiscImageSourceTests
{
    private const int Tolerance = 700;
    private const int AnchorOffset = 1000;
    private const int AnchorSamples = CddaTrackFingerprint.SamplesPerSector;

    public static TheoryData<string> Forms =>
        ["cue-bin", "split-bin", "iso-wav", "clonecd", "mds", "mds-sub", "mds-cooked", "mds-apart", "mds-mode2", "nrg-v2", "nrg-v1",
            "nrg-v1-binary"];

    private static string Write(string form, string root) => form switch
    {
        "cue-bin" => CueBin(root),
        "split-bin" => SplitBin(root),
        "iso-wav" => IsoWave(root),
        "clonecd" => CloneCd(root),
        "mds" => Mds(root),
        "mds-sub" => Mds(root, subchannel: true),
        "mds-cooked" => Mds(root, cookedData: true),
        "mds-apart" => Mds(root, pregapApart: true),
        "mds-mode2" => Mds(root, mode2: true),
        "nrg-v2" => Nrg(root),
        "nrg-v1" => Nrg(root, version2: false),
        "nrg-v1-binary" => Nrg(root, version2: false, bcdTimes: false),
        _ => throw new ArgumentOutOfRangeException(nameof(form)),
    };

    private static string ExpectedFormat(string form) => form switch
    {
        "cue-bin" or "split-bin" or "iso-wav" => DiscImageFormats.CueSheet,
        "clonecd" => DiscImageFormats.CloneCd,
        "nrg-v2" or "nrg-v1" or "nrg-v1-binary" => DiscImageFormats.NeroNrg,
        _ => DiscImageFormats.AlcoholMds,
    };

    [Theory]
    [MemberData(nameof(Forms))]
    public async Task EveryFormReadsTheSameDiscAsTheCueBin(string form)
    {
        var reference = Temporary();
        var root = Temporary();
        try
        {
            var manifest = await ReferenceManifestAsync(reference);
            var path = Write(form, root);
            using (var source = OriginalContentSource.OpenDiscImage(path))
            {
                using var expected = OriginalContentSource.OpenCueBin(Path.Combine(reference, "game.cue"));
                Assert.Equal(ContentSourceKinds.DiscImage, source.Kind);
                Assert.Equal(ExpectedFormat(form), source.DiscImageFormat);
                Assert.Equal(expected.Files, source.Files);
                Assert.Equal(expected.Label, source.Label);
                Assert.Equal(expected.VolumeBlocks, source.VolumeBlocks);
                Assert.Equal(await HashAsync(expected.OpenVolume()), await HashAsync(source.OpenVolume()));
                await using (var file = source.OpenRead("ei/test.bin"))
                    Assert.Equal(Payload, await ReadAllAsync(file));
                Assert.All(source.ImageFiles!, file => Assert.True(File.Exists(file)));

                // Each audio track reads the same sectors, through the API CddaWave and the fingerprints use.
                await using var raw = source.OpenBin();
                await using var bin = expected.OpenBin();
                foreach (var track in new[] { 2, 3 })
                {
                    var extent = source.Cue!.TrackExtent(track, raw.Length / RawSector);
                    Assert.Equal(expected.Cue!.TrackExtent(track, bin.Length / RawSector), extent);
                    Assert.Equal(WaveOf(bin, extent), WaveOf(raw, extent));
                }
            }

            // One disc-image manifest, recorded from the cue/bin, identifies every form.
            var result = await AssetVerifier.VerifyAsync(path, manifest, TestContext.Current.CancellationToken);
            Assert.True(result.IsValid, string.Join("; ", result.Issues.Select(issue => issue.Detail)));
            using var opened = OriginalContentSource.Open(path, ContentSourceKinds.DiscImage);
            Assert.Equal(ExpectedFormat(form), opened.DiscImageFormat);
        }
        finally
        {
            Directory.Delete(reference, true);
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task AnIsoIsReadButHoldsNoAudio()
    {
        var reference = Temporary();
        var root = Temporary();
        try
        {
            var manifest = await ReferenceManifestAsync(reference);
            var iso = Path.Combine(root, "game.iso");
            await File.WriteAllBytesAsync(iso, Iso, TestContext.Current.CancellationToken);
            using (var source = OriginalContentSource.OpenDiscImage(root))
            {
                Assert.Equal(DiscImageFormats.Iso, source.DiscImageFormat);
                Assert.Null(source.Cue);
                Assert.Throws<NotSupportedException>(() => source.OpenBin());
            }
            var result = await AssetVerifier.VerifyAsync(iso, manifest, TestContext.Current.CancellationToken);
            Assert.Equal([(2, AssetProblem.Unreadable), (3, AssetProblem.Unreadable)],
                result.Issues.Select(issue => (issue.AudioTrack!.Value, issue.Problem)));
            Assert.Null(result.Issues.FirstOrDefault(issue => issue.AudioTrack is null));
        }
        finally
        {
            Directory.Delete(reference, true);
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData("flac", "fLaC", "FLAC")]
    [InlineData("ogg", "OggS", "Ogg")]
    [InlineData("mp3", "ID3", "MP3")]
    public async Task CompressedAudioIsReportedUnreadableAndTheDataTrackStillVerifies(string extension, string magic, string named)
    {
        var reference = Temporary();
        var root = Temporary();
        try
        {
            var manifest = await ReferenceManifestAsync(reference);
            // The archiver gives FLAC and Ogg tracks the WAVE file type, as DOSBox sheets do.
            var cue = IsoWave(root, $"game (Track 02).{extension}", [.. System.Text.Encoding.ASCII.GetBytes(magic), .. new byte[64]]);
            using (var source = OriginalContentSource.OpenDiscImage(cue))
            {
                Assert.Equal(1, source.Cue!.ImageTracks);
                Assert.Equal(3, source.Cue.Tracks.Count);
                Assert.DoesNotContain(source.ImageFiles!, file => file.EndsWith(extension, StringComparison.Ordinal));
            }
            var result = await AssetVerifier.VerifyAsync(cue, manifest, TestContext.Current.CancellationToken);
            var issues = result.Issues.ToArray();
            Assert.Equal([(2, AssetProblem.Unreadable), (3, AssetProblem.Unreadable)],
                issues.Select(issue => (issue.AudioTrack!.Value, issue.Problem)));
            Assert.Contains($"holds {named} audio", issues[0].Detail, StringComparison.Ordinal);
            Assert.Contains("which is not read", issues[1].Detail, StringComparison.Ordinal);
        }
        finally
        {
            Directory.Delete(reference, true);
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task WaveFilesOfOtherSampleFormatsAndMissingFilesAreUnreadable()
    {
        var reference = Temporary();
        var root = Temporary();
        try
        {
            var mono = Wave(Track2);
            BinaryPrimitives.WriteUInt16LittleEndian(mono.AsSpan(22), 1);
            var cue = IsoWave(root, track2Bytes: mono);
            using (var source = OriginalContentSource.OpenDiscImage(cue))
                Assert.Equal(1, source.Cue!.ImageTracks);
            var manifest = await ReferenceManifestAsync(reference);
            var result = await AssetVerifier.VerifyAsync(cue, manifest, TestContext.Current.CancellationToken);
            Assert.Contains("1 channels", result.Issues.First(issue => issue.AudioTrack == 2).Detail, StringComparison.Ordinal);

            File.Delete(Path.Combine(root, "game (Track 02).wav"));
            result = await AssetVerifier.VerifyAsync(cue, manifest, TestContext.Current.CancellationToken);
            Assert.Contains("does not exist", result.Issues.First(issue => issue.AudioTrack == 2).Detail, StringComparison.Ordinal);
        }
        finally
        {
            Directory.Delete(reference, true);
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public void AnUnstoredPregapReadsAsAnError()
    {
        var root = Temporary();
        try
        {
            using var source = OriginalContentSource.OpenDiscImage(IsoWave(root));
            using var raw = source.OpenBin();
            raw.Position = (DataSectors + 1) * (long)RawSector;
            var error = Assert.Throws<InvalidDataException>(() => raw.ReadExactly(new byte[RawSector]));
            Assert.Contains("PREGAP", error.Message, StringComparison.Ordinal);
            raw.Position = 0;
            error = Assert.Throws<InvalidDataException>(() => raw.ReadExactly(new byte[RawSector]));
            Assert.Contains("2048-byte user data", error.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData("clonecd")]
    [InlineData("mds")]
    [InlineData("nrg-v2")]
    [InlineData("iso-wav")]
    public async Task AChangedImageFileIsDetected(string form)
    {
        var root = Temporary();
        try
        {
            var path = Write(form, root);
            using var source = OriginalContentSource.OpenDiscImage(path);
            var data = form switch
            {
                "clonecd" => "game.img",
                "mds" => "game.mdf",
                "nrg-v2" => "game.nrg",
                _ => "game (Track 02).wav",
            };
            var changed = Path.Combine(root, data);
            await using (var stream = new FileStream(changed, FileMode.Append)) stream.WriteByte(0);
            File.SetLastWriteTimeUtc(changed, DateTime.UtcNow.AddMinutes(5));
            await using var raw = source.OpenBin();
            raw.Position = Track2Start * (long)RawSector;
            Assert.Throws<IOException>(() => raw.ReadExactly(new byte[RawSector]));
            if (form == "iso-wav") return;
            await using var file = source.OpenRead("EI/TEST.BIN");
            Assert.Throws<IOException>(() => file.ReadByte());
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void EveryDataSectorReadIsChecked()
    {
        var root = Temporary();
        try
        {
            var image = Bin;
            image[OriginalContentSourceTests.IsoPayloadSector * RawSector + 15] = 2;
            var path = CloneCd(root, image: image);
            using var source = OriginalContentSource.OpenDiscImage(path);
            using var file = source.OpenRead("EI/TEST.BIN");
            var error = Assert.Throws<InvalidDataException>(() => file.ReadByte());
            Assert.Contains("mode 2", error.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    public static TheoryData<string, string, Type, string> CloneCdRefusals => new()
    {
        { "Sessions=1", "Sessions=2", typeof(NotSupportedException), "2 sessions" },
        { "DataTracksScrambled=0", "DataTracksScrambled=1", typeof(NotSupportedException), "scrambled" },
        { "Version=3", "Version=4", typeof(NotSupportedException), "version 4" },
        { "Point=0xa2", "Point=0xb0", typeof(NotSupportedException), "point 0xB0" },
        { "[TRACK 3]\r\nMODE=0", "[TRACK 3]\r\nMODE=1", typeof(InvalidDataException), "marks track 3 as audio" },
        { $"INDEX 1={Track3Start}", $"INDEX 1={Track3Start + 1}", typeof(InvalidDataException), "INDEX 1" },
        { "[Disc]", "[Mystery]\r\nKey=1\r\n[Disc]", typeof(InvalidDataException), "[Mystery]" },
        { "CDTextLength=0", "CDTextLength=0\r\nnot a key", typeof(InvalidDataException), "line 8" },
    };

    [Theory]
    [MemberData(nameof(CloneCdRefusals))]
    public void CloneCdLayoutsThatAreNotUnderstoodAreRefused(string find, string replace, Type exception, string message)
    {
        var root = Temporary();
        try
        {
            var path = CloneCd(root, text => text.Replace(find, replace, StringComparison.Ordinal));
            var error = Assert.Throws(exception, () => OriginalContentSource.OpenDiscImage(path));
            Assert.Contains(message, error.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void ACloneCdImageThatDoesNotEndAtTheLeadOutIsRefused()
    {
        var root = Temporary();
        try
        {
            var path = CloneCd(root, image: [.. Bin, .. new byte[RawSector]]);
            var error = Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenDiscImage(path));
            Assert.Contains("lead-out", error.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    public static TheoryData<int, byte, Type, string> MdsRefusals => new()
    {
        { 16, 2, typeof(NotSupportedException), "version 2" },
        { 18, 0x10, typeof(NotSupportedException), "DVD" },
        { 20, 2, typeof(NotSupportedException), "2 sessions" },
        // Track 1's mode byte, then its sector size.
        { 88 + 24 + 3 * 80, 0xAD, typeof(NotSupportedException), "mode 0xAD" },
        { 88 + 24 + 3 * 80 + 16, 0x20, typeof(NotSupportedException), "-byte sectors" },
        { 0, (byte)'X', typeof(InvalidDataException), "MEDIA DESCRIPTOR" },
    };

    [Theory]
    [MemberData(nameof(MdsRefusals))]
    public void MdsLayoutsThatAreNotUnderstoodAreRefused(int offset, byte value, Type exception, string message)
    {
        var root = Temporary();
        try
        {
            var path = Mds(root, edit: mds => mds[offset] = value);
            var error = Assert.Throws(exception, () => OriginalContentSource.OpenDiscImage(path));
            Assert.Contains(message, error.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void AnMdsWhoseTracksDoNotMeetIsRefused()
    {
        var root = Temporary();
        try
        {
            // Track 2's pregap made one sector shorter leaves a sector between the tracks.
            var path = Mds(root, cookedData: true, edit: mds => mds[88 + 24 + 6 * 80 + 8] = PregapSectors - 1);
            var error = Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenDiscImage(path));
            Assert.Contains("does not follow track 1", error.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData("clonecd", "the CloneCD .ccd game.ccd declares MODE2/2352")]
    [InlineData("mds", "the Alcohol .mds game.mds declares MODE2/2352")]
    [InlineData("nrg", "the Nero .nrg game.nrg declares MODE2/2352")]
    public async Task ASectorThatDoesNotMatchItsDescriptorNamesIt(string form, string message)
    {
        // Each descriptor declares the MODE1 data track as Mode 2.
        var root = Temporary();
        try
        {
            var path = form switch
            {
                "clonecd" => CloneCd(root, edit: ccd => ccd.Replace("[TRACK 1]\r\nMODE=1", "[TRACK 1]\r\nMODE=2", StringComparison.Ordinal)),
                "mds" => Mds(root, edit: mds => mds[88 + 24 + 3 * 80] = 0xAB),
                _ => Nrg(root, edit: chunks => Edit(chunks, "DAOX", dao => dao[22 + 14] = 0x06)),
            };
            var error = await Assert.ThrowsAsync<InvalidDataException>(async () =>
            {
                using var source = OriginalContentSource.OpenDiscImage(path);
                await HashAsync(source.OpenVolume());
            });
            Assert.Contains(message, error.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void AnMdxIsRefused()
    {
        var root = Temporary();
        try
        {
            var path = Path.Combine(root, "game.mdx");
            File.WriteAllBytes(path, new byte[64]);
            var error = Assert.Throws<NotSupportedException>(() => OriginalContentSource.Open(path));
            Assert.Contains(".mdx", error.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void NrgLayoutsThatAreNotUnderstoodAreRefused()
    {
        var root = Temporary();
        try
        {
            Assert.Contains("track-at-once", Assert.Throws<NotSupportedException>(() => OriginalContentSource.OpenDiscImage(
                Nrg(root, edit: chunks => [.. chunks, ("ETN2", new byte[32])]))).Message, StringComparison.Ordinal);
            Assert.Contains("\"ABCD\" chunk", Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenDiscImage(
                Nrg(root, edit: chunks => [.. chunks, ("ABCD", new byte[4])]))).Message, StringComparison.Ordinal);
            Assert.Contains("several sessions", Assert.Throws<NotSupportedException>(() => OriginalContentSource.OpenDiscImage(
                Nrg(root, edit: chunks => [.. chunks, chunks[1]]))).Message, StringComparison.Ordinal);
            // Track 2's mode set to 2336-byte Mode 2 sectors.
            Assert.Contains("2336-byte", Assert.Throws<NotSupportedException>(() => OriginalContentSource.OpenDiscImage(
                Nrg(root, edit: chunks => Edit(chunks, "DAOX", dao => dao[22 + 42 + 14] = 0x03)))).Message, StringComparison.Ordinal);
            // The lead-out moved one sector later than the stored sectors reach.
            Assert.Contains("stores 20 sectors for track 3", Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenDiscImage(
                Nrg(root, edit: chunks => Edit(chunks, "CUEX", cue => BinaryPrimitives.WriteInt32BigEndian(cue.AsSpan(7 * 8 + 4), LeadOut + 1)))))
                .Message, StringComparison.Ordinal);

            // A DVD-ROM media type (MEDIA_DVD_ROM).
            Assert.Contains("not a CD", Assert.Throws<NotSupportedException>(() => OriginalContentSource.OpenDiscImage(
                Nrg(root, edit: chunks => Edit(chunks, "MTYP", mtyp => { mtyp[2] = 0x02; mtyp[3] = 0; })))).Message, StringComparison.Ordinal);

            var path = Path.Combine(root, "game.nrg");
            File.WriteAllBytes(path, new byte[4096]);
            Assert.Contains("NER5", Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenDiscImage(path)).Message,
                StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData("    PREGAP 00:02:00", "    POSTGAP 00:02:00", "POSTGAP")]
    [InlineData("  TRACK 02 AUDIO", "  TRACK 02 MODE1/2352", "non-audio track 02")]
    [InlineData("    PREGAP 00:02:00", "    PREGAP 00:02:00\n    INDEX 00 00:00:00", "both PREGAP and INDEX 00")]
    [InlineData("TRACK 01 MODE1/2048", "TRACK 01 MODE1/2336", "MODE1/2048")]
    public void CueSheetsThatAreNotUnderstoodAreRefused(string find, string replace, string message)
    {
        var root = Temporary();
        try
        {
            var cue = IsoWave(root);
            File.WriteAllText(cue, File.ReadAllText(cue).Replace(find, replace, StringComparison.Ordinal));
            var error = Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenDiscImage(cue));
            Assert.Contains(message, error.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void AnIsoFileHoldingAudioTracksIsRefused()
    {
        var root = Temporary();
        try
        {
            File.WriteAllBytes(Path.Combine(root, "game.iso"), [.. Iso, .. new byte[TrackSectors * RawSector]]);
            var cue = Path.Combine(root, "game.cue");
            File.WriteAllText(cue, "FILE \"game.iso\" BINARY\nTRACK 01 MODE1/2048\nINDEX 01 00:00:00\n" +
                $"TRACK 02 AUDIO\nINDEX 01 {Msf(DataSectors)}\n");
            var error = Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenDiscImage(cue));
            Assert.Contains("may hold only the data track", error.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void ADirectoryIsReadThroughItsOneImage()
    {
        var root = Temporary();
        try
        {
            IsoWave(root);
            using (var source = OriginalContentSource.OpenDiscImage(root))
                Assert.Equal(DiscImageFormats.CueSheet, source.DiscImageFormat);
            CloneCd(root);
            Assert.Contains("several disc images",
                Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenDiscImage(root)).Message, StringComparison.Ordinal);
            using (var source = OriginalContentSource.OpenDiscImage(Path.Combine(root, "game.img")))
                Assert.Equal(DiscImageFormats.CloneCd, source.DiscImageFormat);
            using (var source = OriginalContentSource.Open(Path.Combine(root, "game.ccd")))
                Assert.Equal(ContentSourceKinds.DiscImage, source.Kind);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void TheCueBinKindStillReadsOnlyTheFirstFile()
    {
        var root = Temporary();
        try
        {
            var cue = IsoWave(root);
            var error = Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenCueBin(cue));
            Assert.Contains("MODE1/2352 or MODE2/2352", error.Message, StringComparison.Ordinal);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void ADiscImageManifestMayPinTheVolumeAndListAudio()
    {
        var zero = new string('0', 32);
        var manifest = new AssetManifest("game", "edition", [new AssetFileSpec("EI/TEST.BIN", 5)], ContentSourceKinds.DiscImage)
        {
            VolumeBlocks = DataSectors,
            AudioTracks = [new CddaTrackFingerprint(2, 11760, 0, 0, 1, zero, zero)],
        };
        manifest.Validate();
        Assert.True(ContentSourceKinds.IsSupported(ContentSourceKinds.DiscImage));
    }

    // A disc-image manifest recorded from the cue/bin form of the disc, with every pin it can give.
    private static async Task<AssetManifest> ReferenceManifestAsync(string reference)
    {
        var cue = CueBin(reference);
        using var source = OriginalContentSource.OpenCueBin(cue);
        var tracks = new List<CddaTrackFingerprint>();
        await using (var bin = source.OpenBin())
            foreach (var track in new[] { 2, 3 })
                tracks.Add(await CddaTrackFingerprints.RecordAsync(bin, source.Cue!.TrackExtent(track, bin.Length / RawSector),
                    Tolerance, AnchorOffset, AnchorSamples, TestContext.Current.CancellationToken));
        return new AssetManifest("game", "edition",
            [new AssetFileSpec("EI/TEST.BIN", Payload.Length, FileFingerprint.Xxh3(Payload))], ContentSourceKinds.DiscImage)
        {
            VolumeIdentifier = source.Label,
            VolumeBlocks = source.VolumeBlocks,
            VolumeXxh3 = await HashAsync(source.OpenVolume()),
            AudioTracks = tracks,
        };
    }

    private static byte[] WaveOf(Stream image, CueBinTrackExtent extent)
    {
        using var output = new MemoryStream();
        CddaWave.Write(image, output, extent.StartSector, extent.Sectors);
        return output.ToArray();
    }

    private static List<(string Id, byte[] Data)> Edit(List<(string Id, byte[] Data)> chunks, string id, Action<byte[]> edit)
    {
        edit(chunks.First(chunk => chunk.Id == id).Data);
        return chunks;
    }

    private static async Task<string> HashAsync(Stream stream)
    {
        await using (stream) return await FileFingerprint.Xxh3Async(stream, TestContext.Current.CancellationToken);
    }

    private static async Task<byte[]> ReadAllAsync(Stream stream)
    {
        using var copy = new MemoryStream();
        await stream.CopyToAsync(copy, TestContext.Current.CancellationToken);
        return copy.ToArray();
    }

    private static string Temporary()
    {
        var root = Path.Combine(Path.GetTempPath(), "disc-image-source-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }
}
