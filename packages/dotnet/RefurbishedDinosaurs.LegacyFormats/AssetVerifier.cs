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
    /// <summary>The ISO 9660 volume identifier differs from the manifest's <see cref="AssetManifest.VolumeIdentifier"/>.</summary>
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

/// <summary>Why one edition did not match during <see cref="AssetVerifier.IdentifyAsync"/>.</summary>
/// <param name="Edition">The edition tried.</param>
/// <param name="Issues">What did not match.</param>
public sealed record EditionMismatch(AssetManifest Edition, IReadOnlyList<AssetVerificationIssue> Issues);

/// <summary>The outcome of <see cref="AssetVerifier.IdentifyAsync"/>.</summary>
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
        return new(await CheckAsync(source, manifest, new(StringComparer.OrdinalIgnoreCase), cancellationToken)
            .ConfigureAwait(false));
    }

    /// <summary>
    /// Opens <paramref name="path"/> as the manifest's <see cref="AssetManifest.SourceKind"/> and checks
    /// it as <see cref="VerifyAsync(OriginalContentSource, AssetManifest, CancellationToken)"/> does. A
    /// source that cannot be opened is reported as <see cref="AssetProblem.Unreadable"/>.
    /// </summary>
    /// <param name="path">The directory, <c>.iso</c> image or <c>.cue</c> sheet holding the original.</param>
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
        var opened = Open(path, manifest.SourceKind);
        if (opened.Source is null) return new([opened.Failure!]);
        using (opened.Source)
            return new(await CheckAsync(opened.Source, manifest, opened.Hashes, cancellationToken)
                .ConfigureAwait(false));
    }

    /// <summary>
    /// Verifies <paramref name="path"/> against every edition, in the order given, and returns the
    /// editions it matched and why each other edition did not. Every manifest is validated before the
    /// source is opened. The source is opened once per source kind, and a file two editions both
    /// hash is read once.
    /// </summary>
    /// <remarks>
    /// <see cref="EditionIdentification.Edition"/> is set only when exactly one edition matched. When
    /// several match, for example because one edition's files are a subset of another's, the
    /// manifests cannot tell them apart and <see cref="EditionIdentification.IsAmbiguous"/> is set.
    /// </remarks>
    /// <param name="path">The directory, <c>.iso</c> image or <c>.cue</c> sheet holding the original.</param>
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
                    sources.Add(edition.SourceKind, opened = Open(path, edition.SourceKind));
                IReadOnlyList<AssetVerificationIssue> issues = opened.Source is null
                    ? [opened.Failure!]
                    : await CheckAsync(opened.Source, edition, opened.Hashes, cancellationToken).ConfigureAwait(false);
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

    private static OpenedSource Open(string path, string kind)
    {
        try
        {
            return new(OriginalContentSource.Open(path, kind), null);
        }
        catch (Exception exception) when (IsReadFailure(exception) || exception is ArgumentException)
        {
            return new(null, new(null, AssetProblem.Unreadable, exception.Message));
        }
    }

    // The key hashes stores the volume's fingerprint under. A normalized path cannot contain ':'.
    private const string VolumeHashKey = ":volume";

    private static async Task CheckVolumeAsync(
        OriginalContentSource source,
        AssetManifest manifest,
        Dictionary<string, string> hashes,
        List<AssetVerificationIssue> issues,
        CancellationToken cancellationToken)
    {
        if (manifest.VolumeIdentifier is null && manifest.VolumeBlocks is null && manifest.VolumeXxh3 is null) return;
        if (source.VolumeBlocks is not { } blocks)
        {
            issues.Add(new(null, AssetProblem.Unreadable, $"The {source.Kind} source has no ISO 9660 volume to check."));
            return;
        }

        var decided = false;
        if (manifest.VolumeIdentifier is { } identifier && !identifier.Equals(source.Label, StringComparison.Ordinal))
        {
            var found = source.Label is null ? "none" : $"'{source.Label}'";
            issues.Add(new(null, AssetProblem.WrongVolumeIdentifier,
                $"Expected volume identifier '{identifier}'; found {found}."));
            decided = true;
        }
        if (manifest.VolumeBlocks is { } expected && expected != blocks)
        {
            issues.Add(new(null, AssetProblem.WrongVolumeSize, $"Expected {expected} logical blocks; found {blocks}."));
            decided = true;
        }
        if (manifest.VolumeXxh3 is not { } expectedHash || decided) return;

        if (!hashes.TryGetValue(VolumeHashKey, out var actual))
        {
            try
            {
                await using var stream = source.OpenVolume();
                actual = await FileFingerprint.Xxh3Async(stream, cancellationToken).ConfigureAwait(false);
            }
            catch (Exception exception) when (IsReadFailure(exception) || exception is NotSupportedException)
            {
                issues.Add(new(null, AssetProblem.Unreadable, $"The volume could not be read: {exception.Message}"));
                return;
            }
            hashes[VolumeHashKey] = actual;
        }
        if (!actual.Equals(expectedHash, StringComparison.Ordinal))
            issues.Add(new(null, AssetProblem.WrongVolumeHash, $"Expected volume xxh3 {expectedHash}; found {actual}."));
    }

    // hashes holds the fingerprint of each file already read from this source, by normalized path.
    private static async Task<List<AssetVerificationIssue>> CheckAsync(
        OriginalContentSource source,
        AssetManifest manifest,
        Dictionary<string, string> hashes,
        CancellationToken cancellationToken)
    {
        var issues = new List<AssetVerificationIssue>();
        await CheckVolumeAsync(source, manifest, hashes, issues, cancellationToken).ConfigureAwait(false);
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
            if (!hashes.TryGetValue(relative, out var actual))
            {
                try
                {
                    await using var stream = source.OpenRead(relative);
                    actual = await FileFingerprint.Xxh3Async(stream, cancellationToken).ConfigureAwait(false);
                }
                catch (Exception exception) when (IsReadFailure(exception))
                {
                    issues.Add(new(relative, AssetProblem.Unreadable, $"The file could not be read: {exception.Message}"));
                    continue;
                }
                hashes[relative] = actual;
            }
            if (!actual.Equals(spec.Xxh3, StringComparison.Ordinal))
                issues.Add(new(relative, AssetProblem.WrongHash, $"Expected xxh3 {spec.Xxh3}; found {actual}."));
        }
        if (manifest.AudioTracks is { Count: > 0 } tracks)
            await CheckAudioAsync(source, tracks, issues, cancellationToken).ConfigureAwait(false);
        return issues;
    }

    private static async Task CheckAudioAsync(
        OriginalContentSource source,
        IReadOnlyList<CddaTrackFingerprint> tracks,
        List<AssetVerificationIssue> issues,
        CancellationToken cancellationToken)
    {
        if (source.Cue is not { } sheet || source.OpenRawImage is not { } openImage)
        {
            foreach (var track in tracks)
                issues.Add(new(null, AssetProblem.Unreadable,
                    $"The {source.Kind} source holds no CD audio track {track.Track:D2}.") { AudioTrack = track.Track });
            return;
        }

        Stream image;
        try { image = openImage(); }
        catch (Exception exception) when (IsReadFailure(exception))
        {
            foreach (var track in tracks)
                issues.Add(new(null, AssetProblem.Unreadable, $"The image could not be read: {exception.Message}")
                    { AudioTrack = track.Track });
            return;
        }

        await using (image.ConfigureAwait(false))
        {
            var imageSectors = image.Length / CueBinSheet.RawSectorSize;
            foreach (var track in tracks)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (track.Track > sheet.Tracks.Count || sheet.Tracks[track.Track - 1].Type != "AUDIO")
                {
                    issues.Add(new(null, AssetProblem.Missing,
                        $"The cue sheet has no audio track {track.Track:D2}.") { AudioTrack = track.Track });
                    continue;
                }
                CddaTrackVerification result;
                try
                {
                    result = await CddaTrackFingerprints.VerifyAsync(image,
                        sheet.TrackExtent(track.Track, imageSectors), track, cancellationToken).ConfigureAwait(false);
                }
                catch (Exception exception) when (IsReadFailure(exception))
                {
                    issues.Add(new(null, AssetProblem.Unreadable, $"The track could not be read: {exception.Message}")
                        { AudioTrack = track.Track });
                    continue;
                }
                if (result.Problem is { } problem)
                    issues.Add(new(null, problem, result.Detail) { AudioTrack = track.Track });
            }
        }
    }

    private static bool IsReadFailure(Exception exception) =>
        exception is IOException or UnauthorizedAccessException or InvalidDataException;

    private sealed record OpenedSource(OriginalContentSource? Source, AssetVerificationIssue? Failure)
    {
        public Dictionary<string, string> Hashes { get; } = new(StringComparer.OrdinalIgnoreCase);
    }
}
