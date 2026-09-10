namespace Toad.Discovery.Core.Assets;

public enum AssetProblem { Missing, WrongSize, WrongHash }

public sealed record AssetVerificationIssue(string Path, AssetProblem Problem, string Detail);

public sealed record AssetVerificationResult(IReadOnlyList<AssetVerificationIssue> Issues)
{
    public bool IsValid => Issues.Count == 0;
}

public static class AssetVerifier
{
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
            var relative = AssetPath.NormalizeRelative(spec.Path);
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
