using System.Text;
using System.Text.Json;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.Core.Assets;

/// <summary>
/// Describes one supported edition of the original: the files a restoration needs from the user's
/// legally owned copy, and how that copy is read.
/// </summary>
/// <param name="GameId">Identifier of the restoration the manifest belongs to.</param>
/// <param name="SourceEdition">The edition of the original the sizes and hashes describe.</param>
/// <param name="Files">The expected files, with paths relative to the original's root.</param>
/// <param name="SourceKind">
/// How the copy is read, one of the <c>ContentSourceKinds</c> of RefurbishedDinosaurs.LegacyFormats:
/// <c>directory</c>, <c>iso9660</c> or <c>cue-bin</c>.
/// </param>
public sealed record AssetManifest(
    string GameId,
    string SourceEdition,
    IReadOnlyList<AssetFileSpec> Files,
    string SourceKind = "directory")
{
    /// <summary>The largest manifest <see cref="Load"/> reads.</summary>
    public const int MaximumBytes = 4 * 1024 * 1024;

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true
    };

    /// <summary>
    /// The identifier the ISO 9660 primary volume descriptor must carry, compared exactly after its
    /// trailing spaces and NULs are removed, or <see langword="null"/> to leave it unchecked. Only an
    /// <c>iso9660</c> or <c>cue-bin</c> manifest may give it.
    /// </summary>
    public string? VolumeIdentifier { get; init; }

    /// <summary>
    /// The volume space size the ISO 9660 primary volume descriptor must declare, in 2048-byte
    /// logical blocks, or <see langword="null"/> to leave it unchecked. Only an <c>iso9660</c> or
    /// <c>cue-bin</c> manifest may give it.
    /// </summary>
    public long? VolumeBlocks { get; init; }

    /// <summary>
    /// The XXH3-128 fingerprint of the ISO 9660 volume: its declared logical blocks from block 0, as
    /// <c>OriginalContentSource.OpenVolume</c> of RefurbishedDinosaurs.LegacyFormats reads them, or
    /// <see langword="null"/> to leave it unchecked. An <c>.iso</c> image and a cue/bin image of one
    /// disc give the same value. Only an <c>iso9660</c> or <c>cue-bin</c> manifest may give it.
    /// </summary>
    public string? VolumeXxh3 { get; init; }

    /// <summary>
    /// Reads a manifest from JSON (property names case-insensitive, comments and trailing commas allowed)
    /// and validates it.
    /// </summary>
    /// <exception cref="InvalidDataException">
    /// The stream holds more than <see cref="MaximumBytes"/> from its position, the JSON is malformed
    /// or empty, or <see cref="Validate"/> rejects it.
    /// </exception>
    public static AssetManifest Load(Stream json)
    {
        ArgumentNullException.ThrowIfNull(json);
        AssetManifest manifest;
        try
        {
            manifest = JsonSerializer.Deserialize<AssetManifest>(ReadBounded(json), JsonOptions)
                ?? throw new InvalidDataException("Asset manifest is empty.");
        }
        catch (JsonException exception)
        {
            throw new InvalidDataException($"Asset manifest is not valid JSON: {exception.Message}", exception);
        }
        manifest.Validate();
        return manifest;
    }

    // Reads at most MaximumBytes, so a non-seekable stream is bounded as well as a seekable one.
    private static byte[] ReadBounded(Stream json)
    {
        if (json.CanSeek && json.Length - json.Position > MaximumBytes) throw TooLarge();
        using var copy = new MemoryStream();
        var chunk = new byte[81920];
        int read;
        while ((read = json.Read(chunk)) > 0)
        {
            if (copy.Length + read > MaximumBytes) throw TooLarge();
            copy.Write(chunk, 0, read);
        }
        return copy.ToArray();
    }

    // The values ContentSourceKinds.Iso9660 and CueBin have in RefurbishedDinosaurs.LegacyFormats,
    // which this package does not reference: the kinds that hold an ISO 9660 volume.
    private static readonly string[] VolumeSourceKinds = ["iso9660", "cue-bin"];

    // The fewest blocks an ISO 9660 volume can declare: 16 system-area blocks, the primary volume
    // descriptor and the set terminator.
    private const long MinimumVolumeBlocks = 18;

    private bool PinsVolume => VolumeIdentifier is not null || VolumeBlocks is not null || VolumeXxh3 is not null;

    private void ValidateVolume()
    {
        if (!PinsVolume) return;
        if (!VolumeSourceKinds.Contains(SourceKind))
            throw new InvalidDataException(
                $"Asset manifest pins an ISO 9660 volume for source kind '{SourceKind}'; only 'iso9660' and 'cue-bin' hold one.");
        // The descriptor holds 32 bytes, and the reader drops trailing padding, so an identifier
        // ending in a space could never match.
        if (VolumeIdentifier is { } identifier &&
            (identifier.Length is 0 or > 32 || identifier[^1] == ' ' || identifier.Any(c => c is < ' ' or > '~')))
            throw new InvalidDataException(
                "Asset manifest volume identifier must be 1 to 32 printable ASCII characters and not end in a space.");
        if (VolumeBlocks < MinimumVolumeBlocks)
            throw new InvalidDataException(
                $"Asset manifest volume size must be at least {MinimumVolumeBlocks} logical blocks.");
        if (VolumeXxh3 is not null && !FileFingerprint.IsXxh3(VolumeXxh3))
            throw new InvalidDataException("Asset manifest has an invalid volume xxh3 value.");
    }

    private static InvalidDataException TooLarge() =>
        new($"Asset manifest is larger than {MaximumBytes} bytes.");

    /// <summary>
    /// Throws unless the game, edition and source kind are named, at least one file is required, every
    /// path passes <see cref="PortableAssetPath.Relative"/> and appears once (ignoring case), no size is
    /// negative, and every hash is an XXH3-128 fingerprint (<see cref="FileFingerprint.IsXxh3"/>).
    /// A volume pin (<see cref="VolumeIdentifier"/>, <see cref="VolumeBlocks"/>, <see cref="VolumeXxh3"/>)
    /// needs the <c>iso9660</c> or <c>cue-bin</c> source kind, an identifier of 1 to 32 printable ASCII
    /// characters that does not end in a space, at least 18 blocks, and an XXH3-128 fingerprint.
    /// </summary>
    /// <remarks>
    /// A manifest with no required file would match any copy, including an empty directory, so it
    /// cannot describe an edition.
    /// </remarks>
    /// <exception cref="InvalidDataException">The game, edition or source kind is blank, the file list is missing or names no required file, or a file record or volume pin is invalid.</exception>
    public void Validate()
    {
        if (string.IsNullOrWhiteSpace(GameId)) throw new InvalidDataException("Asset manifest has no game id.");
        if (string.IsNullOrWhiteSpace(SourceEdition))
            throw new InvalidDataException("Asset manifest has no source edition.");
        if (string.IsNullOrWhiteSpace(SourceKind))
            throw new InvalidDataException("Asset manifest has no source kind.");
        ValidateVolume();
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
            if (file.Xxh3 is not null && !FileFingerprint.IsXxh3(file.Xxh3))
                throw new InvalidDataException($"Asset '{normalized}' has an invalid xxh3 value.");
        }
        if (!Files.Any(file => file.Required))
            throw new InvalidDataException("Asset manifest names no required file.");
    }

    /// <summary>
    /// The edition's fingerprint: XXH3-128 of every file's normalized path, size and hash, ordered by
    /// path, followed by the volume pins when the manifest gives any. It names the edition an import
    /// read, for the installed manifest's <see cref="InstalledAssetManifest.SourceFingerprint"/>.
    /// </summary>
    /// <remarks>
    /// It leaves out <see cref="SourceKind"/>, so the same edition read from a disc image and from a
    /// directory it was copied into has one fingerprint, and <see cref="AssetFileSpec.Required"/>.
    /// A manifest that pins the volume has a fingerprint no directory manifest shares, and two
    /// editions that differ only in their volume pins have different fingerprints.
    /// </remarks>
    /// <exception cref="InvalidDataException"><see cref="Validate"/> rejects the manifest.</exception>
    public string Fingerprint()
    {
        Validate();
        // Sorting the normalized path makes the result independent of the separator a manifest uses.
        var lines = Files
            .Select(file => (Path: PortableAssetPath.Relative(file.Path), file.Size, file.Xxh3))
            .OrderBy(file => file.Path, StringComparer.OrdinalIgnoreCase)
            .Select(file => $"{file.Path}\0{file.Size}\0{file.Xxh3}");
        // A path cannot contain ':', so this line cannot collide with a file's.
        if (PinsVolume)
            lines = lines.Append($"volume:{VolumeIdentifier}\0{VolumeBlocks}\0{VolumeXxh3}");
        return FileFingerprint.Xxh3(Encoding.UTF8.GetBytes(string.Join('\n', lines)));
    }
}

/// <summary>One file the restoration expects in the original.</summary>
/// <param name="Path">Path relative to the original's root, with <c>/</c> or <c>\</c> separators.</param>
/// <param name="Size">Exact size in bytes.</param>
/// <param name="Xxh3">Its XXH3-128 fingerprint, or <see langword="null"/> to check the size only.</param>
/// <param name="Required">Whether a missing file is a problem; an optional file is checked only when present.</param>
public sealed record AssetFileSpec(
    string Path,
    long Size,
    string? Xxh3 = null,
    bool Required = true);
