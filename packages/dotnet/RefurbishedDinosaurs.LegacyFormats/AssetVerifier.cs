using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>Why the original failed verification against an <see cref="AssetManifest"/>.</summary>
public enum AssetProblem
{
    /// <summary>
    /// The source could not be opened as the manifest's source kind, or a file in it could not be
    /// read to the end.
    /// </summary>
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
    /// <see cref="AssetProblem.Unreadable"/>.
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

    // hashes holds the fingerprint of each file already read from this source, by normalized path.
    private static async Task<List<AssetVerificationIssue>> CheckAsync(
        OriginalContentSource source,
        AssetManifest manifest,
        Dictionary<string, string> hashes,
        CancellationToken cancellationToken)
    {
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
        return issues;
    }

    private static bool IsReadFailure(Exception exception) =>
        exception is IOException or UnauthorizedAccessException or InvalidDataException;

    private sealed record OpenedSource(OriginalContentSource? Source, AssetVerificationIssue? Failure)
    {
        public Dictionary<string, string> Hashes { get; } = new(StringComparer.OrdinalIgnoreCase);
    }
}
