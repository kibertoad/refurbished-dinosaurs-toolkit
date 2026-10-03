using System.Text.Json;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.Core.Assets;

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
    /// Throws unless the game and edition are named, every path passes <see cref="PortableAssetPath.Relative"/>
    /// and appears once (ignoring case), no size is negative, and every hash is 64 hex digits.
    /// </summary>
    /// <exception cref="InvalidDataException">The game or edition is blank, the file list is missing, or a file record is invalid.</exception>
    public void Validate()
    {
        if (string.IsNullOrWhiteSpace(GameId)) throw new InvalidDataException("Asset manifest has no game id.");
        if (string.IsNullOrWhiteSpace(SourceEdition))
            throw new InvalidDataException("Asset manifest has no source edition.");
        if (Files is null) throw new InvalidDataException("Asset manifest has no file list.");

        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var file in Files)
        {
            if (file is null) throw new InvalidDataException("Asset manifest contains a null file record.");
            var normalized = PortableAssetPath.Relative(file.Path);
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
