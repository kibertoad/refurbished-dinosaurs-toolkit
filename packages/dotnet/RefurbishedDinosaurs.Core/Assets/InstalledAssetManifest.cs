using System.Text.Json;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.Core.Assets;

/// <summary>
/// The record an importer writes next to the content it installed, so the game can verify it and an
/// uninstaller can remove exactly those files.
/// </summary>
/// <param name="FormatVersion">The restoration's own manifest format version.</param>
/// <param name="Product">The restoration that wrote the content.</param>
/// <param name="SourceEdition">The edition of the original the content came from.</param>
/// <param name="SourceFingerprintSha256">A SHA-256 identifying the original the import read.</param>
/// <param name="ExtractedAtUtc">When the import ran.</param>
/// <param name="Files">Every installed file.</param>
/// <param name="ExtractorVersion">Version of the importer that wrote the content.</param>
public sealed record InstalledAssetManifest(
    int FormatVersion,
    string Product,
    string SourceEdition,
    string SourceFingerprintSha256,
    DateTimeOffset ExtractedAtUtc,
    IReadOnlyList<InstalledAsset> Files,
    string ExtractorVersion)
{
    /// <summary>Writes the manifest as indented JSON, replacing <paramref name="path"/> atomically.</summary>
    public void Write(string path) => AtomicFile.WriteAllText(path,
        JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));

    /// <summary>Reads a manifest written by <see cref="Write"/>.</summary>
    /// <exception cref="InvalidDataException">The file holds JSON <c>null</c>.</exception>
    public static InstalledAssetManifest Read(string path) =>
        JsonSerializer.Deserialize<InstalledAssetManifest>(File.ReadAllText(path))
        ?? throw new InvalidDataException("Installed-content manifest is empty.");
}

/// <summary>One installed file.</summary>
/// <param name="Path">Path relative to the content root.</param>
/// <param name="Bytes">Size in bytes.</param>
/// <param name="Sha256">SHA-256 of the installed file, as hex.</param>
/// <param name="SourcePath">The file in the original it was produced from.</param>
/// <param name="MediaType">MIME type of the installed file.</param>
/// <param name="Conversion">How the file was converted from the original, or <see langword="null"/> when copied.</param>
public sealed record InstalledAsset(
    string Path,
    long Bytes,
    string Sha256,
    string SourcePath,
    string MediaType = "application/octet-stream",
    AssetConversion? Conversion = null);

/// <summary>How an installed file was produced from its source.</summary>
/// <param name="Method">The restoration's name for the conversion.</param>
/// <param name="Width">Image width in pixels, for images.</param>
/// <param name="Height">Image height in pixels, for images.</param>
/// <param name="BitsPerPixel">Bits per pixel of the output, for images.</param>
/// <param name="PixelFormat">Name of the output pixel format, for images.</param>
public sealed record AssetConversion(
    string Method,
    int? Width = null,
    int? Height = null,
    int? BitsPerPixel = null,
    string? PixelFormat = null);

/// <summary>The outcome of <see cref="InstalledAssetVerifier.VerifyAsync"/>.</summary>
/// <param name="IsValid">Whether no error was found.</param>
/// <param name="Errors">One sentence per problem.</param>
/// <param name="VerifiedFiles">How many files passed every check.</param>
public sealed record InstalledAssetVerification(
    bool IsValid,
    IReadOnlyList<string> Errors,
    int VerifiedFiles);

/// <summary>Checks installed content against the <see cref="InstalledAssetManifest"/> written with it.</summary>
public static class InstalledAssetVerifier
{
    /// <summary>
    /// Checks the manifest's format version, source fingerprint, edition and extractor version, then each
    /// file: unique path, inside <paramref name="root"/>, present, the recorded size, and the recorded hash
    /// when <paramref name="verifyHashes"/> is set. A manifest with no files is an error.
    /// </summary>
    /// <param name="root">The content root the manifest's paths are relative to.</param>
    /// <param name="manifest">The manifest to check against.</param>
    /// <param name="expectedFormatVersion">The manifest format version this build of the game reads.</param>
    /// <param name="verifyHashes">Whether to hash every file, or check sizes only.</param>
    /// <param name="cancellationToken">Cancels between files and during hashing.</param>
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
