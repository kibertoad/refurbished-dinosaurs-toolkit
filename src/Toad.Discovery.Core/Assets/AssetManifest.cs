using System.Text.Json;

namespace Toad.Discovery.Core.Assets;

/// <summary>Describes files that must be obtained from a user's legal original copy.</summary>
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

    public static AssetManifest Load(Stream json)
    {
        ArgumentNullException.ThrowIfNull(json);
        var manifest = JsonSerializer.Deserialize<AssetManifest>(json, JsonOptions)
            ?? throw new InvalidDataException("Asset manifest is empty.");
        manifest.Validate();
        return manifest;
    }

    public void Validate()
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(GameId);
        ArgumentException.ThrowIfNullOrWhiteSpace(SourceEdition);
        ArgumentNullException.ThrowIfNull(Files);

        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var file in Files)
        {
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
