using System.Text.Json;
using Toad.Discovery.Core.IO;

namespace Toad.Discovery.Core.Assets;

public sealed record InstalledAssetManifest(
    int FormatVersion,
    string Product,
    string SourceEdition,
    string SourceFingerprintSha256,
    DateTimeOffset ExtractedAtUtc,
    IReadOnlyList<InstalledAsset> Files,
    string ExtractorVersion)
{
    public void Write(string path) => AtomicFile.WriteAllText(path,
        JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));

    public static InstalledAssetManifest Read(string path) =>
        JsonSerializer.Deserialize<InstalledAssetManifest>(File.ReadAllText(path))
        ?? throw new InvalidDataException("Installed-content manifest is empty.");
}

public sealed record InstalledAsset(
    string Path,
    long Bytes,
    string Sha256,
    string SourcePath,
    string MediaType = "application/octet-stream",
    AssetConversion? Conversion = null);

public sealed record AssetConversion(
    string Method,
    int? Width = null,
    int? Height = null,
    int? BitsPerPixel = null,
    string? PixelFormat = null);

public sealed record InstalledAssetVerification(
    bool IsValid,
    IReadOnlyList<string> Errors,
    int VerifiedFiles);

public static class InstalledAssetVerifier
{
    public static async Task<InstalledAssetVerification> VerifyAsync(
        string root,
        InstalledAssetManifest manifest,
        int expectedFormatVersion,
        bool verifyHashes = true,
        CancellationToken cancellationToken = default)
    {
        var errors = new List<string>();
        if (manifest.FormatVersion != expectedFormatVersion)
            errors.Add($"Format version {manifest.FormatVersion} is incompatible; expected {expectedFormatVersion}.");
        if (!FileFingerprint.IsSha256(manifest.SourceFingerprintSha256))
            errors.Add("Source fingerprint is malformed.");
        if (string.IsNullOrWhiteSpace(manifest.SourceEdition)) errors.Add("Source edition is missing.");
        if (string.IsNullOrWhiteSpace(manifest.ExtractorVersion)) errors.Add("Extractor version is missing.");
        var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var verified = 0;
        foreach (var asset in manifest.Files ?? [])
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (asset is null) { errors.Add("Manifest contains a null asset record."); continue; }
            if (!paths.Add(asset.Path)) { errors.Add($"Duplicate path: {asset.Path}"); continue; }
            if (asset.Bytes < 0 || !FileFingerprint.IsSha256(asset.Sha256))
            { errors.Add($"Invalid size or hash: {asset.Path}"); continue; }
            string target;
            try { target = SafePath.Below(root, asset.Path); }
            catch (InvalidDataException) { errors.Add($"Path escapes content root: {asset.Path}"); continue; }
            if (!File.Exists(target)) { errors.Add($"Missing: {asset.Path}"); continue; }
            if (new FileInfo(target).Length != asset.Bytes) { errors.Add($"Wrong size: {asset.Path}"); continue; }
            if (verifyHashes)
            {
                var hash = await FileFingerprint.Sha256Async(target, cancellationToken).ConfigureAwait(false);
                if (!hash.Equals(asset.Sha256, StringComparison.OrdinalIgnoreCase))
                { errors.Add($"Wrong hash: {asset.Path}"); continue; }
            }
            verified++;
        }
        if (manifest.Files is null || manifest.Files.Count == 0) errors.Add("Manifest contains no assets.");
        return new(errors.Count == 0, errors, verified);
    }
}
