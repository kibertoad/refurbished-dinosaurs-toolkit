using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>Why the original failed verification against an <see cref="AssetManifest"/>.</summary>
public enum AssetProblem
{
    /// <summary>The source could not be opened as the manifest's source kind.</summary>
    Unreadable,
    /// <summary>A required file does not exist.</summary>
    Missing,
    /// <summary>The file's size differs from the manifest.</summary>
    WrongSize,
    /// <summary>The file's XXH3-128 fingerprint differs from the manifest.</summary>
    WrongHash
}

/// <summary>One problem verification found.</summary>
/// <param name="Path">The manifest path, normalized to <c>/</c> separators, or <see langword="null"/> for the source itself.</param>
/// <param name="Problem">What was wrong.</param>
/// <param name="Detail">A sentence giving the expected and found values.</param>
public sealed record AssetVerificationIssue(string? Path, AssetProblem Problem, string Detail);

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
/// <param name="Edition">The first edition that matched, or <see langword="null"/>.</param>
/// <param name="Mismatches">Every edition tried before it, or every edition when none matched.</param>
public sealed record EditionIdentification(AssetManifest? Edition, IReadOnlyList<EditionMismatch> Mismatches)
{
    /// <summary>Whether an edition matched.</summary>
    public bool IsSupported => Edition is not null;
}

/// <summary>Checks a user's original against the <see cref="AssetManifest"/> of a supported edition.</summary>
public static class AssetVerifier
{
    /// <summary>
    /// Checks each manifest file in <paramref name="source"/>: present when required, the exact size,
    /// and the XXH3-128 fingerprint when the manifest gives one. Hashing is skipped for a file of the
    /// wrong size.
    /// </summary>
    /// <param name="source">The opened original.</param>
    /// <param name="manifest">The manifest, validated before any file is read.</param>
    /// <param name="cancellationToken">Cancels between files and during hashing.</param>
    /// <exception cref="InvalidDataException">The manifest is invalid.</exception>
    public static async Task<AssetVerificationResult> VerifyAsync(
        OriginalContentSource source,
        AssetManifest manifest,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(source);
        ArgumentNullException.ThrowIfNull(manifest);
        manifest.Validate();

        var issues = new List<AssetVerificationIssue>();
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
            string actual;
            await using (var stream = source.OpenRead(relative))
                actual = await FileFingerprint.Xxh3Async(stream, cancellationToken).ConfigureAwait(false);
            if (!actual.Equals(spec.Xxh3, StringComparison.Ordinal))
                issues.Add(new(relative, AssetProblem.WrongHash, $"Expected xxh3 {spec.Xxh3}; found {actual}."));
        }

        return new(issues);
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
        manifest.Validate();
        if (!ContentSourceKinds.IsSupported(manifest.SourceKind))
            throw new InvalidDataException($"Asset manifest names an unsupported source kind '{manifest.SourceKind}'.");

        OriginalContentSource source;
        try
        {
            source = OriginalContentSource.Open(path, manifest.SourceKind);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException
                                         or InvalidDataException or ArgumentException)
        {
            return new([new(null, AssetProblem.Unreadable, exception.Message)]);
        }

        using (source)
            return await VerifyAsync(source, manifest, cancellationToken).ConfigureAwait(false);
    }

    /// <summary>
    /// Verifies <paramref name="path"/> against each edition in turn, ordered by
    /// <see cref="AssetManifest.SourceEdition"/>, and returns the first that matches with the reasons
    /// each earlier edition did not.
    /// </summary>
    /// <param name="path">The directory, <c>.iso</c> image or <c>.cue</c> sheet holding the original.</param>
    /// <param name="editions">The supported editions' manifests.</param>
    /// <param name="cancellationToken">Cancels between files and during hashing.</param>
    /// <exception cref="InvalidDataException">An edition's manifest is invalid.</exception>
    public static async Task<EditionIdentification> IdentifyAsync(
        string path,
        IEnumerable<AssetManifest> editions,
        CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        ArgumentNullException.ThrowIfNull(editions);
        var mismatches = new List<EditionMismatch>();
        foreach (var edition in editions.OrderBy(edition => edition.SourceEdition, StringComparer.Ordinal))
        {
            var result = await VerifyAsync(path, edition, cancellationToken).ConfigureAwait(false);
            if (result.IsValid) return new(edition, mismatches);
            mismatches.Add(new(edition, result.Issues));
        }
        return new(null, mismatches);
    }
}
