using System.Text.Json;

namespace Toad.Discovery.Core.Assets;

/// <summary>Describes the files a restoration needs from the user's legally owned original.</summary>
/// <param name="GameId">Identifier of the restoration the manifest belongs to.</param>
/// <param name="SourceEdition">The edition of the original the sizes and hashes describe.</param>
/// <param name="Files">The expected files, with paths relative to the original's root.</param>
public sealed record AssetManifest(
    string GameId,
    string SourceEdition,
    IReadOnlyList<AssetFileSpec> Files)
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true
    };

    /// <summary>
    /// Reads a manifest from JSON (property names case-insensitive, comments and trailing commas allowed)
    /// and validates it.
    /// </summary>
    /// <exception cref="InvalidDataException">The JSON is empty or <see cref="Validate"/> rejects it.</exception>
    public static AssetManifest Load(Stream json)
    {
        ArgumentNullException.ThrowIfNull(json);
        var manifest = JsonSerializer.Deserialize<AssetManifest>(json, JsonOptions)
            ?? throw new InvalidDataException("Asset manifest is empty.");
        manifest.Validate();
        return manifest;
    }

    /// <summary>
    /// Throws unless the game and edition are named, every path is relative without <c>.</c> or <c>..</c>
    /// segments and appears once (ignoring case), no size is negative, and every hash is 64 hex digits.
    /// </summary>
    /// <exception cref="ArgumentException">The game or edition is blank.</exception>
    /// <exception cref="InvalidDataException">A file record is invalid.</exception>
    public void Validate()
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(GameId);
        ArgumentException.ThrowIfNullOrWhiteSpace(SourceEdition);
        ArgumentNullException.ThrowIfNull(Files);

        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var file in Files)
        {
            if (file is null) throw new InvalidDataException("Asset manifest contains a null file record.");
            var normalized = AssetPath.NormalizeRelative(file.Path);
            if (!seen.Add(normalized))
                throw new InvalidDataException($"Duplicate asset path '{normalized}'.");
            if (file.Size < 0)
                throw new InvalidDataException($"Asset '{normalized}' has a negative size.");
            if (file.Sha256 is not null && !FileFingerprint.IsSha256(file.Sha256))
                throw new InvalidDataException($"Asset '{normalized}' has an invalid SHA-256 value.");
        }
    }
}

/// <summary>One file the restoration expects in the original.</summary>
/// <param name="Path">Path relative to the original's root, with <c>/</c> or <c>\</c> separators.</param>
/// <param name="Size">Exact size in bytes.</param>
/// <param name="Sha256">Lowercase or uppercase hex SHA-256, or <see langword="null"/> to check the size only.</param>
/// <param name="Required">Whether a missing file is a problem; an optional file is checked only when present.</param>
public sealed record AssetFileSpec(
    string Path,
    long Size,
    string? Sha256 = null,
    bool Required = true);

internal static class AssetPath
{
    public static string NormalizeRelative(string path)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        var normalized = path.Replace('\\', '/');
        if (Path.IsPathRooted(path) || normalized.StartsWith('/') ||
            normalized.Split('/').Any(part => part is "" or "." or ".."))
            throw new InvalidDataException($"Asset path must be a normalized relative path: '{path}'.");
        return normalized;
    }
}
