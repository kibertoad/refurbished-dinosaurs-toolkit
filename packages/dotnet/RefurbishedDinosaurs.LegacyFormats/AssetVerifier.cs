using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>Why the original failed verification against an <see cref="AssetManifest"/>.</summary>
public enum AssetProblem
{
    /// <summary>
    /// The source could not be opened as the manifest's source kind, a file in it could not be read
    /// to the end, the manifest pins an ISO 9660 volume that the source does not have or that could
    /// not be read to the end, or a CD audio track could not be read from the source.
    /// </summary>
    Unreadable,
    /// <summary>A required file does not exist.</summary>
    Missing,
    /// <summary>
    /// The file's size differs from the manifest, or a CD audio track's length differs from its
    /// fingerprint by more than the tolerance.
    /// </summary>
    WrongSize,
    /// <summary>The file's XXH3-128 fingerprint differs from the manifest.</summary>
    WrongHash,
    /// <summary>
    /// A CD audio track's anchor matched at no shift within the fingerprint's tolerance: the rip is
    /// shifted further than the tolerance, or the track holds other audio.
    /// </summary>
    AudioOffsetOutOfRange,
    /// <summary>
    /// A CD audio track's anchor matched at more than one shift within the tolerance, so the track's
    /// alignment is unknown and its central samples were not checked.
    /// </summary>
    AudioAlignmentAmbiguous,
    /// <summary>A CD audio track's anchor matched at one shift, and the central samples at that shift differ from the fingerprint.</summary>
    AudioHashMismatch,
    /// <summary>
    /// The ISO 9660 volume identifier differs from the manifest's <see cref="AssetManifest.VolumeIdentifier"/>.
    /// The detail writes both identifiers as JSON strings, with each control character as a <c>\u</c>
    /// escape, so the found value can be copied into a manifest.
    /// </summary>
    WrongVolumeIdentifier,
    /// <summary>The ISO 9660 volume space size differs from the manifest's <see cref="AssetManifest.VolumeBlocks"/>.</summary>
    WrongVolumeSize,
    /// <summary>The XXH3-128 fingerprint of the ISO 9660 volume differs from the manifest's <see cref="AssetManifest.VolumeXxh3"/>.</summary>
    WrongVolumeHash
}

/// <summary>One problem verification found.</summary>
/// <param name="Path">
/// The manifest path, normalized to <c>/</c> separators, or <see langword="null"/> for the source
/// itself or an audio track.
/// </param>
/// <param name="Problem">What was wrong.</param>
/// <param name="Detail">A sentence giving the expected and found values.</param>
public sealed record AssetVerificationIssue(string? Path, AssetProblem Problem, string Detail)
{
    /// <summary>The number of the manifest's audio track the problem is with, or <see langword="null"/> for a file or the source.</summary>
    public int? AudioTrack { get; init; }
}

/// <summary>The outcome of <see cref="AssetVerifier.VerifyAsync(OriginalContentSource, AssetManifest, CancellationToken)"/>.</summary>
/// <param name="Issues">Every problem, in manifest order.</param>
public sealed record AssetVerificationResult(IReadOnlyList<AssetVerificationIssue> Issues)
{
    /// <summary>Whether nothing failed.</summary>
    public bool IsValid => Issues.Count == 0;
}

/// <summary>
/// Why one edition did not match during
/// <see cref="AssetVerifier.IdentifyAsync(string, IEnumerable{AssetManifest}, CancellationToken)"/>.
/// </summary>
/// <param name="Edition">The edition tried.</param>
/// <param name="Issues">What did not match.</param>
public sealed record EditionMismatch(AssetManifest Edition, IReadOnlyList<AssetVerificationIssue> Issues);

/// <summary>The outcome of <see cref="AssetVerifier.IdentifyAsync(string, IEnumerable{AssetManifest}, CancellationToken)"/>.</summary>
/// <param name="Matches">Every edition the copy matched, in the order given.</param>
/// <param name="Mismatches">Every other edition, in the order given, with what did not match.</param>
public sealed record EditionIdentification(
    IReadOnlyList<AssetManifest> Matches,
    IReadOnlyList<EditionMismatch> Mismatches)
{
    /// <summary>The edition the copy is, when exactly one matched; otherwise <see langword="null"/>.</summary>
    public AssetManifest? Edition => Matches.Count == 1 ? Matches[0] : null;

    /// <summary>Whether exactly one edition matched.</summary>
    public bool IsSupported => Matches.Count == 1;

    /// <summary>
    /// Whether more than one edition matched, so the manifests do not tell the copy's edition apart.
    /// </summary>
    public bool IsAmbiguous => Matches.Count > 1;
}

/// <summary>Checks a user's original against the <see cref="AssetManifest"/> of a supported edition.</summary>
public static class AssetVerifier
{
    /// <summary>
    /// Checks each manifest file in <paramref name="source"/>: present when required, the exact size,
    /// and the XXH3-128 fingerprint when the manifest gives one. Hashing is skipped for a file of the
    /// wrong size. A file that cannot be read to the end is reported as
    /// <see cref="AssetProblem.Unreadable"/>. Then each of the manifest's
    /// <see cref="AssetManifest.AudioTracks"/> is checked against the source's cue sheet and image as
    /// <see cref="CddaTrackFingerprints.VerifyAsync"/> describes. Its problems carry
    /// <see cref="AssetVerificationIssue.AudioTrack"/>; a track the sheet lacks or does not mark
    /// <c>AUDIO</c> is <see cref="AssetProblem.Missing"/>, and a source that is not a cue/bin image
    /// reports each track as <see cref="AssetProblem.Unreadable"/>.
    /// </summary>
    /// <param name="source">The opened original.</param>
    /// <param name="manifest">The manifest, validated before any file is read.</param>
    /// <param name="cancellationToken">Cancels between files and during hashing.</param>
    /// <exception cref="InvalidDataException">The manifest is invalid.</exception>
    /// <remarks>
    /// When the manifest pins the ISO 9660 volume, the volume is checked before any file: the
    /// identifier against <see cref="OriginalContentSource.Label"/>, the size against
    /// <see cref="OriginalContentSource.VolumeBlocks"/>, then the fingerprint of
    /// <see cref="OriginalContentSource.OpenVolume"/>. Each failure has its own problem and a
    /// <see langword="null"/> path. The fingerprint is skipped when the identifier or size already
    /// differs, since both are part of the volume's bytes. A source with no ISO 9660 volume, or a
    /// volume that cannot be read to the end, is reported as <see cref="AssetProblem.Unreadable"/>.
    /// The files are checked whatever the volume pins found, so a match on the volume never stands in
    /// for a file.
    /// </remarks>
    public static async Task<AssetVerificationResult> VerifyAsync(
        OriginalContentSource source,
        AssetManifest manifest,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(source);
        ArgumentNullException.ThrowIfNull(manifest);
        manifest.Validate();
        return new(await CheckAsync(source, manifest, new SourceReads(), cancellationToken)
            .ConfigureAwait(false));
    }

    /// <summary>
    /// Opens <paramref name="path"/> as the manifest's <see cref="AssetManifest.SourceKind"/> and checks
    /// it as <see cref="VerifyAsync(OriginalContentSource, AssetManifest, CancellationToken)"/> does. A
    /// source that cannot be opened is reported as <see cref="AssetProblem.Unreadable"/>.
    /// </summary>
    /// <param name="path">
    /// The directory, <c>.iso</c> image, <c>.cue</c> sheet, InstallShield <c>.hdr</c> header or
    /// InstallShield 3 archive holding the original.
    /// </param>
    /// <param name="manifest">The manifest, validated before the source is opened.</param>
    /// <param name="cancellationToken">Cancels between files and during hashing.</param>
    /// <exception cref="InvalidDataException">The manifest is invalid or names an unsupported source kind.</exception>
    public static async Task<AssetVerificationResult> VerifyAsync(
        string path,
        AssetManifest manifest,
        CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        ArgumentNullException.ThrowIfNull(manifest);
        ValidateForOpening(manifest);
        var opened = Open(kind => OriginalContentSource.Open(path, kind), manifest.SourceKind);
        if (opened.Source is null) return new([opened.Failure!]);
        using (opened.Source)
            return new(await CheckAsync(opened.Source, manifest, opened.Reads, cancellationToken)
                .ConfigureAwait(false));
    }

    /// <summary>
    /// Verifies <paramref name="path"/> against every edition, in the order given, and returns the
    /// editions it matched and why each other edition did not. Every manifest is validated before the
    /// source is opened. The source is opened once per source kind. From each opened source, the
    /// volume and a file that several editions check are each read once, and a CD audio track is
    /// verified once per <see cref="CddaTrackFingerprint"/>, so editions that list the same
    /// fingerprint share one verification. A read that fails is reported for every edition that
    /// checks it, so all editions get the answer of that one read.
    /// </summary>
    /// <remarks>
    /// <see cref="EditionIdentification.Edition"/> is set only when exactly one edition matched. When
    /// several match, for example because one edition's files are a subset of another's, the
    /// manifests cannot tell them apart and <see cref="EditionIdentification.IsAmbiguous"/> is set.
    /// </remarks>
    /// <param name="path">
    /// The directory, <c>.iso</c> image, <c>.cue</c> sheet, InstallShield <c>.hdr</c> header or
    /// InstallShield 3 archive holding the original.
    /// </param>
    /// <param name="editions">The supported editions' manifests.</param>
    /// <param name="cancellationToken">Cancels between files and during hashing.</param>
    /// <exception cref="ArgumentException"><paramref name="editions"/> contains <see langword="null"/>.</exception>
    /// <exception cref="InvalidDataException">An edition's manifest is invalid or names an unsupported source kind.</exception>
    public static async Task<EditionIdentification> IdentifyAsync(
        string path,
        IEnumerable<AssetManifest> editions,
        CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        return await IdentifyAsync(kind => OriginalContentSource.Open(path, kind), editions, cancellationToken)
            .ConfigureAwait(false);
    }

    // IdentifyAsync with the source opened by open, which takes the source kind. The tests pass an
    // opener whose sources count their reads.
    internal static async Task<EditionIdentification> IdentifyAsync(
        Func<string, OriginalContentSource> open,
        IEnumerable<AssetManifest> editions,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(editions);
        var candidates = editions.ToArray();
        foreach (var edition in candidates)
        {
            if (edition is null) throw new ArgumentException("Editions contain a null manifest.", nameof(editions));
            ValidateForOpening(edition);
        }

        var sources = new Dictionary<string, OpenedSource>(StringComparer.Ordinal);
        var matches = new List<AssetManifest>();
        var mismatches = new List<EditionMismatch>();
        try
        {
            foreach (var edition in candidates)
            {
                if (!sources.TryGetValue(edition.SourceKind, out var opened))
                    sources.Add(edition.SourceKind, opened = Open(open, edition.SourceKind));
                IReadOnlyList<AssetVerificationIssue> issues = opened.Source is null
                    ? [opened.Failure!]
                    : await CheckAsync(opened.Source, edition, opened.Reads, cancellationToken).ConfigureAwait(false);
                if (issues.Count == 0) matches.Add(edition);
                else mismatches.Add(new(edition, issues));
            }
        }
        finally
        {
            foreach (var opened in sources.Values) opened.Source?.Dispose();
        }
        return new(matches, mismatches);
    }

    private static void ValidateForOpening(AssetManifest manifest)
    {
        manifest.Validate();
        if (!ContentSourceKinds.IsSupported(manifest.SourceKind))
            throw new InvalidDataException($"Asset manifest names an unsupported source kind '{manifest.SourceKind}'.");
    }

    private static OpenedSource Open(Func<string, OriginalContentSource> open, string kind)
    {
        try
        {
            return new(open(kind), null);
        }
        catch (Exception exception) when (IsReadFailure(exception) || exception is ArgumentException or NotSupportedException)
        {
            return new(null, new(null, AssetProblem.Unreadable, exception.Message));
        }
    }

    // Writes an identifier or a name as a JSON string, so a control character (C0, DEL or C1) or an
    // unpaired surrogate stays visible on one line of the report and the value can be copied into a
    // manifest as it stands. It escapes as PortableAssetPath does in Core's messages; the two stay
    // separate because Core and LegacyFormats ship as separately versioned packages.
    internal static string JsonString(string value)
    {
        var text = new System.Text.StringBuilder("\"", value.Length + 2);
        for (var index = 0; index < value.Length; index++)
        {
            var c = value[index];
            if (c is '"' or '\\') text.Append('\\').Append(c);
            else if (char.IsHighSurrogate(c) && index + 1 < value.Length && char.IsLowSurrogate(value[index + 1]))
                text.Append(c).Append(value[++index]);
            else if (char.IsControl(c) || char.IsSurrogate(c)) text.Append($"\\u{(int)c:X4}");
            else text.Append(c);
        }
        return text.Append('"').ToString();
    }

    private static async Task CheckVolumeAsync(
        OriginalContentSource source,
        AssetManifest manifest,
        SourceReads reads,
        List<AssetVerificationIssue> issues,
        CancellationToken cancellationToken)
    {
        if (!manifest.PinsVolume) return;
        if (source.VolumeBlocks is not { } blocks)
        {
            issues.Add(new(null, AssetProblem.Unreadable, $"The {source.Kind} source has no ISO 9660 volume to check."));
            return;
        }

        var decided = false;
        if (manifest.VolumeIdentifier is { } identifier && !identifier.Equals(source.Label, StringComparison.Ordinal))
        {
            var found = source.Label is null ? "none" : JsonString(source.Label);
            issues.Add(new(null, AssetProblem.WrongVolumeIdentifier,
                $"Expected volume identifier {JsonString(identifier)}; found {found}."));
            decided = true;
        }
        if (manifest.VolumeBlocks is { } expected && expected != blocks)
        {
            issues.Add(new(null, AssetProblem.WrongVolumeSize, $"Expected {expected} logical blocks; found {blocks}."));
            decided = true;
        }
        if (manifest.VolumeXxh3 is not { } expectedHash || decided) return;

        var volume = reads.Volume ??= await HashAsync(source.OpenVolume,
            exception => IsReadFailure(exception) || exception is NotSupportedException,
            cancellationToken).ConfigureAwait(false);
        if (volume.Failure is { } failure)
            issues.Add(new(null, AssetProblem.Unreadable, $"The volume could not be read: {failure}"));
        else if (!volume.Xxh3!.Equals(expectedHash, StringComparison.Ordinal))
            issues.Add(new(null, AssetProblem.WrongVolumeHash, $"Expected volume xxh3 {expectedHash}; found {volume.Xxh3}."));
    }

    private static async Task<List<AssetVerificationIssue>> CheckAsync(
        OriginalContentSource source,
        AssetManifest manifest,
        SourceReads reads,
        CancellationToken cancellationToken)
    {
        var issues = new List<AssetVerificationIssue>();
        await CheckVolumeAsync(source, manifest, reads, issues, cancellationToken).ConfigureAwait(false);
        foreach (var spec in manifest.Files)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var relative = PortableAssetPath.Relative(spec.Path);
            if (!source.TryGetFile(relative, out var entry))
            {
                if (spec.Required)
                    issues.Add(new(relative, AssetProblem.Missing, "Required file was not found."));
                continue;
            }

            if (entry!.Size != spec.Size)
            {
                issues.Add(new(relative, AssetProblem.WrongSize,
                    $"Expected {spec.Size} bytes; found {entry.Size}."));
                continue;
            }

            if (spec.Xxh3 is null) continue;
            if (!reads.Files.TryGetValue(relative, out var read))
                reads.Files[relative] = read = await HashAsync(() => source.OpenRead(relative), IsReadFailure,
                    cancellationToken).ConfigureAwait(false);
            if (read.Failure is { } failure)
                issues.Add(new(relative, AssetProblem.Unreadable, $"The file could not be read: {failure}"));
            else if (!read.Xxh3!.Equals(spec.Xxh3, StringComparison.Ordinal))
                issues.Add(new(relative, AssetProblem.WrongHash, $"Expected xxh3 {spec.Xxh3}; found {read.Xxh3}."));
        }
        if (manifest.AudioTracks is { Count: > 0 } tracks)
            await CheckAudioAsync(source, tracks, reads, issues, cancellationToken).ConfigureAwait(false);
        return issues;
    }

    // Hashes the stream open returns, or gives the message of an exception isFailure accepts.
    private static async Task<HashRead> HashAsync(
        Func<Stream> open,
        Func<Exception, bool> isFailure,
        CancellationToken cancellationToken)
    {
        try
        {
            await using var stream = open();
            return new(await FileFingerprint.Xxh3Async(stream, cancellationToken).ConfigureAwait(false), null);
        }
        catch (Exception exception) when (isFailure(exception))
        {
            return new(null, exception.Message);
        }
    }

    private static async Task CheckAudioAsync(
        OriginalContentSource source,
        IReadOnlyList<CddaTrackFingerprint> tracks,
        SourceReads reads,
        List<AssetVerificationIssue> issues,
        CancellationToken cancellationToken)
    {
        if (source.Cue is not { } sheet)
        {
            foreach (var track in tracks)
                issues.Add(NoAudioTrack(source, track));
            return;
        }

        // Opened through OpenBin for the first track this source has not verified yet. An image that
        // fails to open is tried once per source. A source without a BIN image refuses OpenBin with
        // NotSupportedException.
        Stream? image = null;
        try
        {
            foreach (var track in tracks)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (track.Track > sheet.Tracks.Count || sheet.Tracks[track.Track - 1].Type != "AUDIO")
                {
                    issues.Add(new(null, AssetProblem.Missing,
                        $"The cue sheet has no audio track {track.Track:D2}.") { AudioTrack = track.Track });
                    continue;
                }
                if (!reads.Tracks.TryGetValue(track, out var issue))
                {
                    if (image is null && reads.ImageFailure is null && !reads.NoImage)
                    {
                        try { image = source.OpenBin(); }
                        catch (NotSupportedException) { reads.NoImage = true; }
                        catch (Exception exception) when (IsReadFailure(exception)) { reads.ImageFailure = exception.Message; }
                    }
                    issue = image is not null
                        ? await VerifyTrackAsync(image, sheet, track, cancellationToken).ConfigureAwait(false)
                        : reads.NoImage
                            ? NoAudioTrack(source, track)
                            : new(null, AssetProblem.Unreadable, $"The image could not be read: {reads.ImageFailure}")
                                { AudioTrack = track.Track };
                    reads.Tracks[track] = issue;
                }
                if (issue is not null) issues.Add(issue);
            }
        }
        finally
        {
            if (image is not null) await image.DisposeAsync().ConfigureAwait(false);
        }
    }

    private static AssetVerificationIssue NoAudioTrack(OriginalContentSource source, CddaTrackFingerprint track) =>
        new(null, AssetProblem.Unreadable, $"The {source.Kind} source holds no CD audio track {track.Track:D2}.")
            { AudioTrack = track.Track };

    // The problem with one track, or null when it matched.
    private static async Task<AssetVerificationIssue?> VerifyTrackAsync(
        Stream image,
        CueBinSheet sheet,
        CddaTrackFingerprint track,
        CancellationToken cancellationToken)
    {
        CddaTrackVerification result;
        try
        {
            result = await CddaTrackFingerprints.VerifyAsync(image,
                sheet.TrackExtent(track.Track, image.Length / CueBinSheet.RawSectorSize), track, cancellationToken)
                .ConfigureAwait(false);
        }
        catch (Exception exception) when (IsReadFailure(exception))
        {
            return new(null, AssetProblem.Unreadable, $"The track could not be read: {exception.Message}")
                { AudioTrack = track.Track };
        }
        return result.Problem is { } problem ? new(null, problem, result.Detail) { AudioTrack = track.Track } : null;
    }

    private static bool IsReadFailure(Exception exception) =>
        exception is IOException or UnauthorizedAccessException or InvalidDataException;

    private sealed record OpenedSource(OriginalContentSource? Source, AssetVerificationIssue? Failure)
    {
        public SourceReads Reads { get; } = new();
    }

    // A fingerprint read from a source, or the message of the exception that stopped the read.
    private readonly record struct HashRead(string? Xxh3, string? Failure);

    // What has been read from one opened source. Every edition checked against the source gets the
    // answer of one read, a failed read included.
    private sealed class SourceReads
    {
        public HashRead? Volume { get; set; }

        // By normalized path.
        public Dictionary<string, HashRead> Files { get; } = new(StringComparer.OrdinalIgnoreCase);

        // Why the BIN image could not be opened, once an attempt failed.
        public string? ImageFailure { get; set; }

        // Whether OpenBin refused because the source has no BIN image.
        public bool NoImage { get; set; }

        // Each verified track's problem, or null for a match, by fingerprint. The fingerprint names
        // the track, and the source's sheet and image fix that track's extent, so for one source the
        // fingerprint also determines the extent.
        public Dictionary<CddaTrackFingerprint, AssetVerificationIssue?> Tracks { get; } = new();
    }
}
