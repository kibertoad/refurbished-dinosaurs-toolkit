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
/// <param name="SourceFingerprint">The edition's <see cref="AssetManifest.Fingerprint"/>.</param>
/// <param name="ExtractedAtUtc">When the import ran.</param>
/// <param name="Files">Every installed file.</param>
/// <param name="ExtractorVersion">Version of the importer that wrote the content.</param>
public sealed record InstalledAssetManifest(
    int FormatVersion,
    string Product,
    string SourceEdition,
    string SourceFingerprint,
    DateTimeOffset ExtractedAtUtc,
    IReadOnlyList<InstalledAsset> Files,
    string ExtractorVersion)
{
    /// <summary>The largest manifest <see cref="Read"/> accepts by default.</summary>
    public const long DefaultMaximumBytes = 4 * 1024 * 1024;

    // camelCase, as schemas/installed-asset-manifest.schema.json names the fields.
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web) { WriteIndented = true };

    /// <summary>
    /// Writes the manifest as indented camelCase JSON, the form
    /// <c>schemas/installed-asset-manifest.schema.json</c> describes, replacing <paramref name="path"/> atomically.
    /// </summary>
    public void Write(string path) => AtomicFile.WriteAllText(path, JsonSerializer.Serialize(this, JsonOptions));

    /// <summary>Reads a manifest written by <see cref="Write"/>. Property names match ignoring case.</summary>
    /// <param name="path">The manifest file.</param>
    /// <param name="maximumBytes">The largest file to read.</param>
    /// <exception cref="InvalidDataException">The file is larger than <paramref name="maximumBytes"/>, holds JSON <c>null</c>, or is not a manifest.</exception>
    public static InstalledAssetManifest Read(string path, long maximumBytes = DefaultMaximumBytes)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        var length = new FileInfo(path).Length;
        if (length > maximumBytes)
            throw new InvalidDataException($"Installed-content manifest is {length} bytes; the limit is {maximumBytes}.");
        try
        {
            return JsonSerializer.Deserialize<InstalledAssetManifest>(File.ReadAllBytes(path), JsonOptions)
                ?? throw new InvalidDataException("Installed-content manifest is empty.");
        }
        catch (JsonException exception)
        {
            throw new InvalidDataException($"Installed-content manifest is not valid: {exception.Message}", exception);
        }
    }
}

/// <summary>One installed file.</summary>
/// <param name="Path">Path relative to the content root.</param>
/// <param name="Bytes">Size in bytes.</param>
/// <param name="Xxh3">XXH3-128 fingerprint of the installed file (<see cref="FileFingerprint"/>).</param>
/// <param name="SourcePath">The file in the original it was produced from.</param>
/// <param name="MediaType">MIME type of the installed file.</param>
/// <param name="Conversion">How the file was converted from the original, or <see langword="null"/> when copied.</param>
public sealed record InstalledAsset(
    string Path,
    long Bytes,
    string Xxh3,
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

/// <summary>What installed content is checked against.</summary>
/// <param name="FormatVersion">The manifest format version this build of the game reads.</param>
/// <param name="Product">The restoration the content must belong to.</param>
/// <param name="ManifestFileName">The manifest's file name under the content root.</param>
/// <param name="RejectUnlistedFiles">
/// Whether a file under the content root that the manifest does not list, other than the manifest
/// itself, is a problem. Leave it off when players keep saves, logs or mods beside the content.
/// </param>
/// <param name="VerifyHashes">Whether to hash every file, or check sizes only.</param>
public sealed record InstalledAssetExpectations(
    int FormatVersion,
    string Product,
    string ManifestFileName = "manifest.json",
    bool RejectUnlistedFiles = false,
    bool VerifyHashes = true);

/// <summary>Why installed content failed verification.</summary>
public enum InstalledAssetProblem
{
    /// <summary>The manifest file does not exist.</summary>
    ManifestMissing,
    /// <summary>The manifest file is too large, unreadable or not a manifest.</summary>
    ManifestUnreadable,
    /// <summary>The manifest's format version is not the one expected.</summary>
    FormatVersionMismatch,
    /// <summary>The content belongs to another restoration.</summary>
    ProductMismatch,
    /// <summary>The source edition is blank.</summary>
    SourceEditionMissing,
    /// <summary>The source fingerprint is not an XXH3-128 fingerprint.</summary>
    SourceFingerprintInvalid,
    /// <summary>The extractor version is blank.</summary>
    ExtractorVersionMissing,
    /// <summary>The manifest lists no files.</summary>
    NoFiles,
    /// <summary>A file record is null, has a negative size, an invalid hash or no source path.</summary>
    InvalidRecord,
    /// <summary>A path appears twice, ignoring case.</summary>
    DuplicatePath,
    /// <summary>A path is not a portable relative path, or escapes the content root.</summary>
    UnsafePath,
    /// <summary>A listed file does not exist.</summary>
    Missing,
    /// <summary>A listed file has the wrong size.</summary>
    WrongSize,
    /// <summary>A listed file has the wrong fingerprint.</summary>
    WrongHash,
    /// <summary>A file the manifest does not list is present (<see cref="InstalledAssetExpectations.RejectUnlistedFiles"/>).</summary>
    Unlisted,
    /// <summary>The content root could not be listed.</summary>
    InventoryUnreadable
}

/// <summary>One problem verification found.</summary>
/// <param name="Problem">What was wrong.</param>
/// <param name="Path">The file it concerns, relative to the content root with <c>/</c> separators, or <see langword="null"/>.</param>
/// <param name="Detail">One sentence describing it.</param>
public sealed record InstalledAssetIssue(InstalledAssetProblem Problem, string? Path, string Detail);

/// <summary>The outcome of <see cref="InstalledAssetVerifier"/>.</summary>
/// <param name="Issues">Every problem found.</param>
/// <param name="VerifiedFiles">How many files passed every check.</param>
public sealed record InstalledAssetVerification(IReadOnlyList<InstalledAssetIssue> Issues, int VerifiedFiles)
{
    /// <summary>Whether no problem was found.</summary>
    public bool IsValid => Issues.Count == 0;
}

/// <summary>Checks installed content against the <see cref="InstalledAssetManifest"/> written with it.</summary>
public static class InstalledAssetVerifier
{
    /// <summary>
    /// Reads the manifest from <paramref name="root"/> (<see cref="InstalledAssetExpectations.ManifestFileName"/>,
    /// at most <see cref="InstalledAssetManifest.DefaultMaximumBytes"/>) and checks the content as
    /// <see cref="VerifyAsync(string, InstalledAssetManifest, InstalledAssetExpectations, CancellationToken)"/>
    /// does. A missing or unreadable manifest is reported, not thrown.
    /// </summary>
    /// <param name="root">The content root.</param>
    /// <param name="expected">What the content is checked against.</param>
    /// <param name="cancellationToken">Cancels between files and during hashing.</param>
    public static async Task<InstalledAssetVerification> VerifyDirectoryAsync(
        string root,
        InstalledAssetExpectations expected,
        CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        ArgumentNullException.ThrowIfNull(expected);
        var manifestPath = SafePath.Below(root, expected.ManifestFileName);
        if (!File.Exists(manifestPath))
            return new([new(InstalledAssetProblem.ManifestMissing, expected.ManifestFileName,
                "The installed-content manifest was not found.")], 0);

        InstalledAssetManifest manifest;
        try
        {
            manifest = InstalledAssetManifest.Read(manifestPath);
        }
        catch (Exception exception) when (exception is InvalidDataException or IOException
                                         or UnauthorizedAccessException)
        {
            return new([new(InstalledAssetProblem.ManifestUnreadable, expected.ManifestFileName,
                exception.Message)], 0);
        }
        return await VerifyAsync(root, manifest, expected, cancellationToken).ConfigureAwait(false);
    }

    /// <summary>
    /// Checks the manifest's format version, product, source edition and fingerprint, and extractor
    /// version, then each file: a portable path inside <paramref name="root"/>, listed once, a valid
    /// record, present, the recorded size, and the recorded fingerprint when
    /// <see cref="InstalledAssetExpectations.VerifyHashes"/> is set. With
    /// <see cref="InstalledAssetExpectations.RejectUnlistedFiles"/>, it also reports every other file
    /// under <paramref name="root"/>. A manifest with no files is a problem.
    /// </summary>
    /// <param name="root">The content root the manifest's paths are relative to.</param>
    /// <param name="manifest">The manifest to check against.</param>
    /// <param name="expected">What the content is checked against.</param>
    /// <param name="cancellationToken">Cancels between files and during hashing.</param>
    public static async Task<InstalledAssetVerification> VerifyAsync(
        string root,
        InstalledAssetManifest manifest,
        InstalledAssetExpectations expected,
        CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        ArgumentNullException.ThrowIfNull(manifest);
        ArgumentNullException.ThrowIfNull(expected);
        var issues = new List<InstalledAssetIssue>();
        if (manifest.FormatVersion != expected.FormatVersion)
            issues.Add(new(InstalledAssetProblem.FormatVersionMismatch, null,
                $"Format version {manifest.FormatVersion} is incompatible; expected {expected.FormatVersion}."));
        if (!string.Equals(manifest.Product, expected.Product, StringComparison.Ordinal))
            issues.Add(new(InstalledAssetProblem.ProductMismatch, null,
                $"The content belongs to '{manifest.Product}', not '{expected.Product}'."));
        if (string.IsNullOrWhiteSpace(manifest.SourceEdition))
            issues.Add(new(InstalledAssetProblem.SourceEditionMissing, null, "Source edition is missing."));
        if (!FileFingerprint.IsXxh3(manifest.SourceFingerprint))
            issues.Add(new(InstalledAssetProblem.SourceFingerprintInvalid, null, "Source fingerprint is malformed."));
        if (string.IsNullOrWhiteSpace(manifest.ExtractorVersion))
            issues.Add(new(InstalledAssetProblem.ExtractorVersionMissing, null, "Extractor version is missing."));
        if (manifest.Files is null || manifest.Files.Count == 0)
        {
            issues.Add(new(InstalledAssetProblem.NoFiles, null, "The manifest lists no files."));
            return new(issues, 0);
        }

        var fullRoot = Path.GetFullPath(root);
        var listed = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var verified = 0;
        foreach (var asset in manifest.Files)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (asset is null)
            {
                issues.Add(new(InstalledAssetProblem.InvalidRecord, null, "The manifest contains a null file record."));
                continue;
            }

            string relative, target;
            try
            {
                relative = PortableAssetPath.Relative(asset.Path);
                target = SafePath.Below(fullRoot, relative);
            }
            catch (InvalidDataException)
            {
                issues.Add(new(InstalledAssetProblem.UnsafePath, asset.Path, $"Unsafe path: {asset.Path}"));
                continue;
            }
            if (!listed.Add(target))
            {
                issues.Add(new(InstalledAssetProblem.DuplicatePath, relative, $"Duplicate path: {relative}"));
                continue;
            }
            if (asset.Bytes < 0 || !FileFingerprint.IsXxh3(asset.Xxh3) || string.IsNullOrWhiteSpace(asset.SourcePath))
            {
                issues.Add(new(InstalledAssetProblem.InvalidRecord, relative,
                    $"Invalid size, fingerprint or source path: {relative}"));
                continue;
            }
            if (!File.Exists(target))
            {
                issues.Add(new(InstalledAssetProblem.Missing, relative, $"Missing: {relative}"));
                continue;
            }
            var length = new FileInfo(target).Length;
            if (length != asset.Bytes)
            {
                issues.Add(new(InstalledAssetProblem.WrongSize, relative,
                    $"Expected {asset.Bytes} bytes; found {length}: {relative}"));
                continue;
            }
            if (expected.VerifyHashes)
            {
                var hash = await FileFingerprint.Xxh3Async(target, cancellationToken).ConfigureAwait(false);
                if (!hash.Equals(asset.Xxh3, StringComparison.Ordinal))
                {
                    issues.Add(new(InstalledAssetProblem.WrongHash, relative,
                        $"Expected xxh3 {asset.Xxh3}; found {hash}: {relative}"));
                    continue;
                }
            }
            verified++;
        }

        if (expected.RejectUnlistedFiles) ReportUnlisted(fullRoot, expected.ManifestFileName, listed, issues);
        return new(issues, verified);
    }

    private static void ReportUnlisted(
        string root, string manifestFileName, HashSet<string> listed, List<InstalledAssetIssue> issues)
    {
        listed.Add(SafePath.Below(root, manifestFileName));
        try
        {
            foreach (var path in Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories))
            {
                if (listed.Contains(Path.GetFullPath(path))) continue;
                var relative = Path.GetRelativePath(root, path).Replace('\\', '/');
                issues.Add(new(InstalledAssetProblem.Unlisted, relative, $"Not listed in the manifest: {relative}"));
            }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            issues.Add(new(InstalledAssetProblem.InventoryUnreadable, null,
                $"The content root could not be listed: {exception.Message}"));
        }
    }
}
