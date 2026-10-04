using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

// IdentifyAsync reads each part of a source once, whatever the number of editions that check it,
// and reports a failed read for every edition that checks it.
public sealed class IdentifyReadsTests
{
    private const string FailureMessage = "Synthetic read failure.";

    [Fact]
    public async Task TheVolumeAndASharedFileAreReadOncePerSource()
    {
        var root = CddaTrackFingerprintTests.CreateTemporaryDirectory();
        try
        {
            var reference = await ReferenceAsync(root);
            var right = reference with { SourceEdition = "right", AudioTracks = null };
            var wrong = right with { SourceEdition = "wrong", VolumeXxh3 = new string('0', FileFingerprint.Xxh3Length) };
            var alsoRight = right with { SourceEdition = "also right" };
            var opens = 0;
            CountingSource? source = null;

            var found = await AssetVerifier.IdentifyAsync(kind =>
            {
                opens++;
                return source = new CountingSource(OriginalContentSource.Open(root, kind));
            }, [right, wrong, alsoRight], TestContext.Current.CancellationToken);

            Assert.Equal([right, alsoRight], found.Matches);
            var issue = Assert.Single(Assert.Single(found.Mismatches).Issues);
            Assert.Equal(AssetProblem.WrongVolumeHash, issue.Problem);
            Assert.Equal(1, opens);
            Assert.Equal((1, 23L * 2048), (source!.VolumeReads.Opens, source.VolumeReads.Bytes));
            Assert.Equal((1, 4L), (source.FileReads.Opens, source.FileReads.Bytes));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task AFailedVolumeReadHappensOnceAndEveryPinnedEditionReportsIt()
    {
        var root = CddaTrackFingerprintTests.CreateTemporaryDirectory();
        try
        {
            var reference = await ReferenceAsync(root);
            var editions = new[] { "a", "b", "c" }
                .Select(name => reference with { SourceEdition = name, AudioTracks = null }).ToArray();
            // Unpinned, so it never reads the volume and matches on its file.
            var unpinned = editions[0] with { SourceEdition = "unpinned", VolumeXxh3 = null };
            CountingSource? source = null;

            var found = await AssetVerifier.IdentifyAsync(
                kind => source = new CountingSource(OriginalContentSource.Open(root, kind)) { VolumeFailsAt = 10 * 2048 },
                [.. editions, unpinned], TestContext.Current.CancellationToken);

            Assert.Same(unpinned, found.Edition);
            Assert.Equal(editions, found.Mismatches.Select(mismatch => mismatch.Edition));
            var issues = found.Mismatches.Select(mismatch => Assert.Single(mismatch.Issues)).ToArray();
            Assert.All(issues, issue => Assert.Equal(issues[0], issue));
            Assert.Equal(AssetProblem.Unreadable, issues[0].Problem);
            Assert.Null(issues[0].Path);
            Assert.Equal($"The volume could not be read: {FailureMessage}", issues[0].Detail);
            Assert.Equal((1, 10L * 2048), (source!.VolumeReads.Opens, source.VolumeReads.Bytes));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task AFailedFileReadHappensOnceAndEveryEditionReportsIt()
    {
        var root = CddaTrackFingerprintTests.CreateTemporaryDirectory();
        try
        {
            var reference = await ReferenceAsync(root);
            var editions = new[] { "a", "b", "c" }
                .Select(name => reference with { SourceEdition = name, AudioTracks = null, VolumeXxh3 = null }).ToArray();
            CountingSource? source = null;

            var found = await AssetVerifier.IdentifyAsync(
                kind => source = new CountingSource(OriginalContentSource.Open(root, kind)) { FileFailsAt = 2 },
                editions, TestContext.Current.CancellationToken);

            Assert.Empty(found.Matches);
            var issues = found.Mismatches.Select(mismatch => Assert.Single(mismatch.Issues)).ToArray();
            Assert.Equal(3, issues.Length);
            Assert.All(issues, issue => Assert.Equal(
                new AssetVerificationIssue("EI/TEST.BIN", AssetProblem.Unreadable, $"The file could not be read: {FailureMessage}"),
                issue));
            Assert.Equal((1, 2L), (source!.FileReads.Opens, source.FileReads.Bytes));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task AnAudioTrackSeveralEditionsListIsVerifiedOnce()
    {
        var root = CddaTrackFingerprintTests.CreateTemporaryDirectory();
        try
        {
            var reference = await ReferenceAsync(root) with { VolumeXxh3 = null };
            var single = await CountImageReadsAsync(root, [reference]);
            Assert.True(single.Found.IsSupported);

            var editions = new[] { "a", "b", "c" }.Select(name => reference with { SourceEdition = name }).ToArray();
            var shared = await CountImageReadsAsync(root, editions);
            Assert.Equal(editions, shared.Found.Matches);
            Assert.Equal((1, single.Reads.Bytes), (shared.Reads.Opens, shared.Reads.Bytes));

            // Another fingerprint for track 3 is verified on its own; track 2 is not read again.
            var otherTrack3 = reference with
            {
                SourceEdition = "other",
                AudioTracks = [reference.AudioTracks![0], reference.AudioTracks[1] with { CentralXxh3 = new string('0', FileFingerprint.Xxh3Length) }]
            };
            var mixed = await CountImageReadsAsync(root, [reference, otherTrack3]);
            Assert.Same(reference, mixed.Found.Edition);
            var issue = Assert.Single(Assert.Single(mixed.Found.Mismatches).Issues);
            Assert.Equal((3, AssetProblem.AudioHashMismatch), (issue.AudioTrack!.Value, issue.Problem));
            Assert.Equal(2, mixed.Reads.Opens);
            Assert.True(mixed.Reads.Bytes < 2 * single.Reads.Bytes);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task AFailedAudioReadHappensOnceAndEveryEditionReportsIt()
    {
        var root = CddaTrackFingerprintTests.CreateTemporaryDirectory();
        try
        {
            var reference = await ReferenceAsync(root) with { VolumeXxh3 = null };
            var editions = new[] { "a", "b", "c" }.Select(name => reference with { SourceEdition = name }).ToArray();
            // The image fails past its data track, so both audio tracks fail.
            foreach (var failing in new Func<OriginalContentSource, CountingSource>[]
            {
                inner => new CountingSource(inner) { ImageFailsAt = 23 * 2352 },
                inner => new CountingSource(inner) { ImageFailsToOpen = true },
            })
            {
                CountingSource? source = null;
                var found = await AssetVerifier.IdentifyAsync(kind => source = failing(OriginalContentSource.Open(root, kind)),
                    editions, TestContext.Current.CancellationToken);

                Assert.Empty(found.Matches);
                var first = found.Mismatches[0].Issues;
                Assert.Equal([(2, AssetProblem.Unreadable), (3, AssetProblem.Unreadable)],
                    first.Select(issue => (issue.AudioTrack!.Value, issue.Problem)));
                Assert.All(first, issue => Assert.EndsWith(FailureMessage, issue.Detail, StringComparison.Ordinal));
                Assert.All(found.Mismatches, mismatch => Assert.Equal(first, mismatch.Issues));
                Assert.Equal(1, source!.ImageReads.Opens);
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task ATrackTheSheetLacksIsMissingWhenTheImageCannotBeOpened()
    {
        var root = CddaTrackFingerprintTests.CreateTemporaryDirectory();
        try
        {
            var reference = await ReferenceAsync(root) with { VolumeXxh3 = null };
            var withExtra = reference with
            {
                AudioTracks = [.. reference.AudioTracks!, reference.AudioTracks![1] with { Track = 4 }]
            };
            CountingSource? source = null;

            var found = await AssetVerifier.IdentifyAsync(
                kind => source = new CountingSource(OriginalContentSource.Open(root, kind)) { ImageFailsToOpen = true },
                [withExtra], TestContext.Current.CancellationToken);

            var issues = Assert.Single(found.Mismatches).Issues;
            Assert.Equal([(2, AssetProblem.Unreadable), (3, AssetProblem.Unreadable), (4, AssetProblem.Missing)],
                issues.Select(issue => (issue.AudioTrack!.Value, issue.Problem)));
            Assert.Equal(1, source!.ImageReads.Opens);
        }
        finally { Directory.Delete(root, true); }
    }

    // The recorded cue/bin image in root and its manifest, with the volume pinned by hash.
    private static async Task<AssetManifest> ReferenceAsync(string root)
    {
        var manifest = await CddaTrackFingerprintTests.RecordAsync(root);
        using var source = OriginalContentSource.OpenCueBin(root);
        await using var volume = source.OpenVolume();
        return manifest with { VolumeXxh3 = await FileFingerprint.Xxh3Async(volume, TestContext.Current.CancellationToken) };
    }

    private static async Task<(EditionIdentification Found, ReadCount Reads)> CountImageReadsAsync(
        string root, AssetManifest[] editions)
    {
        CountingSource? source = null;
        var found = await AssetVerifier.IdentifyAsync(kind => source = new CountingSource(OriginalContentSource.Open(root, kind)),
            editions, TestContext.Current.CancellationToken);
        return (found, source!.ImageReads);
    }

    private sealed class ReadCount
    {
        public int Opens { get; set; }
        public long Bytes { get; set; }
    }

    // Passes a real source through, counting the streams it opens and the bytes read from them. A
    // stream given a failure offset reads up to it, then throws IOException.
    private sealed class CountingSource(OriginalContentSource inner) : OriginalContentSource
    {
        public ReadCount VolumeReads { get; } = new();
        public ReadCount FileReads { get; } = new();
        public ReadCount ImageReads { get; } = new();
        public long? VolumeFailsAt { get; init; }
        public long? FileFailsAt { get; init; }
        public long? ImageFailsAt { get; init; }
        public bool ImageFailsToOpen { get; init; }

        public override string Kind => inner.Kind;
        public override string? Label => inner.Label;
        public override CueBinSheet? Cue => inner.Cue;
        public override IReadOnlyList<ContentSourceEntry> Files => inner.Files;
        public override long? VolumeBlocks => inner.VolumeBlocks;

        public override bool TryGetFile(string relativePath, out ContentSourceEntry? entry) =>
            inner.TryGetFile(relativePath, out entry);

        public override Stream OpenRead(string relativePath) => Count(inner.OpenRead(relativePath), FileReads, FileFailsAt);

        public override Stream OpenVolume() => Count(inner.OpenVolume(), VolumeReads, VolumeFailsAt);

        public override Stream OpenBin()
        {
            if (!ImageFailsToOpen) return Count(inner.OpenBin(), ImageReads, ImageFailsAt);
            ImageReads.Opens++;
            throw new IOException(FailureMessage);
        }

        public override void Dispose() => inner.Dispose();

        private static CountingStream Count(Stream stream, ReadCount count, long? failsAt)
        {
            count.Opens++;
            return new(stream, count, failsAt);
        }
    }

    private sealed class CountingStream(Stream inner, ReadCount count, long? failsAt) : Stream
    {
        public override bool CanRead => true;
        public override bool CanSeek => inner.CanSeek;
        public override bool CanWrite => false;
        public override long Length => inner.Length;
        public override long Position { get => inner.Position; set => inner.Position = value; }

        public override int Read(Span<byte> buffer)
        {
            if (failsAt is { } end)
            {
                if (inner.Position >= end) throw new IOException(FailureMessage);
                buffer = buffer[..(int)Math.Min(buffer.Length, end - inner.Position)];
            }
            var read = inner.Read(buffer);
            count.Bytes += read;
            return read;
        }

        public override int Read(byte[] buffer, int offset, int length) => Read(buffer.AsSpan(offset, length));

        public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default) =>
            ValueTask.FromResult(Read(buffer.Span));

        public override Task<int> ReadAsync(byte[] buffer, int offset, int length, CancellationToken cancellationToken) =>
            Task.FromResult(Read(buffer.AsSpan(offset, length)));

        public override long Seek(long offset, SeekOrigin origin) => inner.Seek(offset, origin);
        public override void Flush() { }
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int length) => throw new NotSupportedException();

        protected override void Dispose(bool disposing)
        {
            if (disposing) inner.Dispose();
            base.Dispose(disposing);
        }
    }
}
