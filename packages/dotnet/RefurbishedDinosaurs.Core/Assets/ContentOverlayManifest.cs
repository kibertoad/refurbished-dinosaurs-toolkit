using System.Text.Json;
using System.Text.Json.Serialization;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.Core.Assets;

/// <summary>The bounds a <see cref="ContentOverlay"/> and its manifest are read within.</summary>
/// <param name="MaximumFiles">The most file records the manifest may hold.</param>
/// <param name="MaximumFileBytes">The largest size one record may give.</param>
/// <param name="MaximumTotalBytes">The largest sum of the records' sizes.</param>
/// <param name="MaximumManifestBytes">The largest manifest file.</param>
public sealed record ContentOverlayLimits(
    int MaximumFiles = 100_000,
    long MaximumFileBytes = 1L << 30,
    long MaximumTotalBytes = 4L << 30,
    int MaximumManifestBytes = 4 * 1024 * 1024)
{
    /// <summary>The default bounds: 100,000 files, 1 GiB a file, 4 GiB in all and a 4 MiB manifest.</summary>
    public static ContentOverlayLimits Default { get; } = new();
}

/// <summary>
/// The description of a content overlay: the files it adds to or replaces in a content directory,
/// each with the fingerprint it expects to find there and the one it leaves.
/// <c>schemas/content-overlay.schema.json</c> describes the JSON form.
/// </summary>
/// <param name="FormatVersion">The overlay format version, <see cref="CurrentFormatVersion"/>.</param>
/// <param name="Name">
/// The overlay's identifier. <see cref="ContentOverlayResult.UpdateInstalledFiles"/> records it as the
/// conversion method of every file the overlay wrote.
/// </param>
/// <param name="GameId">The restoration the overlay belongs to.</param>
/// <param name="FromVersion">The version of the content the overlay applies to.</param>
/// <param name="ToVersion">The version of the content once it is applied.</param>
/// <param name="Files">The files the overlay writes.</param>
public sealed record ContentOverlayManifest(
    int FormatVersion,
    string Name,
    string GameId,
    string FromVersion,
    string ToVersion,
    IReadOnlyList<ContentOverlayFile> Files)
{
    /// <summary>The overlay format version this build reads.</summary>
    public const int CurrentFormatVersion = 1;

    // camelCase, as schemas/content-overlay.schema.json names the fields. Every field is required,
    // a null is accepted only for baseXxh3, and an unknown field is an error, as the schema says.
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        RespectNullableAnnotations = true,
        RespectRequiredConstructorParameters = true,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow
    };

    /// <summary>Reads a manifest from UTF-8 JSON and validates it against <paramref name="limits"/>.</summary>
    /// <param name="json">The manifest's bytes.</param>
    /// <param name="limits">The bounds to check, or <see langword="null"/> for <see cref="ContentOverlayLimits.Default"/>.</param>
    /// <exception cref="InvalidDataException">
    /// The JSON is larger than <see cref="ContentOverlayLimits.MaximumManifestBytes"/>, is not a
    /// manifest, or <see cref="Validate"/> rejects it.
    /// </exception>
    public static ContentOverlayManifest Parse(ReadOnlySpan<byte> json, ContentOverlayLimits? limits = null)
    {
        limits ??= ContentOverlayLimits.Default;
        if (json.Length > limits.MaximumManifestBytes)
            throw new InvalidDataException($"Overlay manifest is larger than {limits.MaximumManifestBytes} bytes.");
        ContentOverlayManifest manifest;
        try
        {
            manifest = JsonSerializer.Deserialize<ContentOverlayManifest>(json, JsonOptions)
                ?? throw new InvalidDataException("Overlay manifest is empty.");
        }
        catch (JsonException exception)
        {
            throw new InvalidDataException($"Overlay manifest is not valid: {exception.Message}", exception);
        }
        manifest.Validate(limits);
        return manifest;
    }

    /// <summary>
    /// Throws unless the format version is <see cref="CurrentFormatVersion"/>, the name, game and both
    /// versions are given, and the file list is within <paramref name="limits"/> and holds at least one
    /// record. Each record needs a path <see cref="PortableAssetPath.Relative"/> accepts, used once
    /// ignoring case and never as a directory of another record, a size within the limits, an
    /// XXH3-128 <see cref="ContentOverlayFile.Xxh3"/>, and a <see cref="ContentOverlayFile.BaseXxh3"/>
    /// that is <see langword="null"/> or another XXH3-128 fingerprint.
    /// </summary>
    /// <param name="limits">The bounds to check, or <see langword="null"/> for <see cref="ContentOverlayLimits.Default"/>.</param>
    /// <exception cref="InvalidDataException">The manifest breaks one of these rules.</exception>
    public void Validate(ContentOverlayLimits? limits = null)
    {
        limits ??= ContentOverlayLimits.Default;
        if (FormatVersion != CurrentFormatVersion)
            throw new InvalidDataException(
                $"Overlay format version {FormatVersion} is not supported; expected {CurrentFormatVersion}.");
        if (string.IsNullOrWhiteSpace(Name) || string.IsNullOrWhiteSpace(GameId) ||
            string.IsNullOrWhiteSpace(FromVersion) || string.IsNullOrWhiteSpace(ToVersion))
            throw new InvalidDataException("Overlay manifest must name the overlay, the game and both versions.");
        if (Files is null || Files.Count == 0) throw new InvalidDataException("Overlay manifest lists no files.");
        if (Files.Count > limits.MaximumFiles)
            throw new InvalidDataException($"Overlay manifest lists more than {limits.MaximumFiles} files.");

        var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var directories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        long total = 0;
        foreach (var file in Files)
        {
            if (file is null) throw new InvalidDataException("Overlay manifest contains a null file record.");
            var path = PortableAssetPath.Relative(file.Path);
            if (!paths.Add(path)) throw new InvalidDataException($"Overlay path appears twice: {path}");
            for (var slash = path.IndexOf('/'); slash >= 0; slash = path.IndexOf('/', slash + 1))
                directories.Add(path[..slash]);
            if (file.Bytes < 0 || file.Bytes > limits.MaximumFileBytes)
                throw new InvalidDataException($"Overlay file size is outside 0..{limits.MaximumFileBytes}: {path}");
            total += file.Bytes;
            if (total > limits.MaximumTotalBytes)
                throw new InvalidDataException($"Overlay files total more than {limits.MaximumTotalBytes} bytes.");
            if (!FileFingerprint.IsXxh3(file.Xxh3))
                throw new InvalidDataException($"Overlay file has an invalid xxh3: {path}");
            if (file.BaseXxh3 is not null && !FileFingerprint.IsXxh3(file.BaseXxh3))
                throw new InvalidDataException($"Overlay file has an invalid baseXxh3: {path}");
            if (string.Equals(file.BaseXxh3, file.Xxh3, StringComparison.Ordinal))
                throw new InvalidDataException($"Overlay file would leave its target unchanged: {path}");
        }
        var both = paths.FirstOrDefault(directories.Contains);
        if (both is not null)
            throw new InvalidDataException($"Overlay path is both a file and a directory of another file: {both}");
    }
}

/// <summary>One file a <see cref="ContentOverlayManifest"/> writes.</summary>
/// <param name="Path">The target's path relative to the content root, and the payload's path in the overlay.</param>
/// <param name="Bytes">The size of the payload and of the target once applied.</param>
/// <param name="BaseXxh3">
/// The XXH3-128 fingerprint the target must have before it is replaced, or <see langword="null"/> when
/// the overlay adds the file and the target must not exist.
/// </param>
/// <param name="Xxh3">The XXH3-128 fingerprint of the payload and of the target once applied.</param>
public sealed record ContentOverlayFile(string Path, long Bytes, string? BaseXxh3, string Xxh3);
