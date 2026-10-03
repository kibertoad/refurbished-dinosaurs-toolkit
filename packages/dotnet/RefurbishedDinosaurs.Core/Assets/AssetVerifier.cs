using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.Core.Assets;

/// <summary>Why a file failed verification against its <see cref="AssetFileSpec"/>.</summary>
public enum AssetProblem
{
    /// <summary>A required file does not exist.</summary>
    Missing,
    /// <summary>The file's size differs from the manifest.</summary>
    WrongSize,
    /// <summary>The file's SHA-256 differs from the manifest.</summary>
    WrongHash
}

/// <summary>One file that failed verification.</summary>
/// <param name="Path">The manifest path, normalized to <c>/</c> separators.</param>
/// <param name="Problem">What was wrong.</param>
/// <param name="Detail">A sentence giving the expected and found values.</param>
public sealed record AssetVerificationIssue(string Path, AssetProblem Problem, string Detail);

/// <summary>The outcome of <see cref="AssetVerifier.VerifyAsync"/>.</summary>
/// <param name="Issues">Every failing file, in manifest order.</param>
public sealed record AssetVerificationResult(IReadOnlyList<AssetVerificationIssue> Issues)
{
    /// <summary>Whether no file failed.</summary>
    public bool IsValid => Issues.Count == 0;
}

/// <summary>Checks a user's original against an <see cref="AssetManifest"/>.</summary>
public static class AssetVerifier
{
    /// <summary>
    /// Checks each manifest file under <paramref name="sourceRoot"/>: present when required, the exact
    /// size, and the SHA-256 when the manifest gives one. Hashing is skipped for a file of the wrong size.
    /// </summary>
    /// <param name="sourceRoot">The directory that holds the original.</param>
    /// <param name="manifest">The manifest, validated before any file is read.</param>
    /// <param name="cancellationToken">Cancels between files and during hashing.</param>
    public static async Task<AssetVerificationResult> VerifyAsync(
        string sourceRoot,
        AssetManifest manifest,
        CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(sourceRoot);
        ArgumentNullException.ThrowIfNull(manifest);
        manifest.Validate();

        var issues = new List<AssetVerificationIssue>();
        foreach (var spec in manifest.Files)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var relative = PortableAssetPath.Relative(spec.Path);
            var path = Path.Combine(sourceRoot, relative.Replace('/', Path.DirectorySeparatorChar));
            if (!File.Exists(path))
            {
                if (spec.Required)
                    issues.Add(new(relative, AssetProblem.Missing, "Required file was not found."));
                continue;
            }

            var length = new FileInfo(path).Length;
            if (length != spec.Size)
            {
                issues.Add(new(relative, AssetProblem.WrongSize,
                    $"Expected {spec.Size} bytes; found {length}."));
                continue;
            }

            if (spec.Sha256 is not null)
            {
                var actual = await FileFingerprint.Sha256Async(path, cancellationToken)
                    .ConfigureAwait(false);
                if (!actual.Equals(spec.Sha256, StringComparison.OrdinalIgnoreCase))
                    issues.Add(new(relative, AssetProblem.WrongHash,
                        $"Expected {spec.Sha256.ToLowerInvariant()}; found {actual}."));
            }
        }

        return new(issues);
    }
}
