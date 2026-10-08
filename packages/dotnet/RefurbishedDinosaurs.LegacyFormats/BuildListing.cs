using System.Reflection;
using System.Security;
using System.Text;
using System.Text.RegularExpressions;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>A disc that <see cref="BuildListing.Make"/> lists, read from an image of it.</summary>
/// <param name="Prefix">The prefix its paths take: <c>CD:</c>, or <c>CD1:</c>, <c>CD2:</c> and so on.</param>
/// <param name="ImagePath">
/// Where to read the image: an <c>.iso</c> file of 2,048-byte sectors, or the <c>.cue</c> or <c>.bin</c>
/// file of a cue/bin raw image whose data track is <c>MODE1/2352</c> or <c>MODE2/2352</c>.
/// </param>
/// <param name="Source">
/// The image's path as the build's manifest or list of other files writes it, which the record gives
/// as the medium's <c>source</c>.
/// </param>
public sealed record BuildListingDisc(string Prefix, string ImagePath, string Source);

/// <summary>One medium of a <see cref="BuildListingRecord"/>.</summary>
/// <param name="Prefix">An empty string for the installation directory, or the disc's prefix.</param>
/// <param name="Source">The disc image's path as the build writes it, or <see langword="null"/> for the installation directory.</param>
/// <param name="Layout">
/// The sectors read from a disc: <c>2048</c>, <c>MODE1/2352</c> or <c>MODE2/2352</c>, or
/// <see langword="null"/> for the installation directory.
/// </param>
public sealed record BuildListingMedium(string Prefix, string? Source, string? Layout);

/// <summary>One item of a <see cref="BuildListingRecord"/>. Exactly one of the three last fields is set.</summary>
/// <param name="Path">The path, as the documentation standard writes it (ENTRY-TYPES-11).</param>
/// <param name="Size">For a file, its size in bytes.</param>
/// <param name="Link">For a symbolic link or junction, its target as stored. The listing does not follow it.</param>
/// <param name="Stopped">For a path the listing could not give as a file or a link, the reason.</param>
public sealed record BuildListingItem(string Path, long? Size, string? Link, string? Stopped);

/// <summary>
/// A build's listing record, the <c>&lt;ID&gt;.listing.yaml</c> file of the documentation standard
/// (ENTRY-TYPES-16 and ENTRY-TYPES-17): how the listing was made, and the paths and sizes it found.
/// </summary>
public sealed class BuildListingRecord
{
    internal BuildListingRecord(
        string tool, DateOnly date, IReadOnlyList<BuildListingMedium> media, IReadOnlyList<BuildListingItem> items)
    {
        Tool = tool;
        Date = date;
        Media = media;
        Items = items;
    }

    /// <summary>The program that made the listing: this package's name and version.</summary>
    public string Tool { get; }

    /// <summary>The day the listing was made.</summary>
    public DateOnly Date { get; }

    /// <summary>
    /// What the listing did with symbolic links, junctions and other reparse points: <c>listed</c>,
    /// since it gives each one as an item and enters none. The record's <c>cycles</c> is therefore null.
    /// </summary>
    public string Links => "listed";

    /// <summary>The installation directory and each disc the listing covered, in the order they were given.</summary>
    public IReadOnlyList<BuildListingMedium> Media { get; }

    /// <summary>
    /// Every file, link and stopped path, sorted by path compared byte by byte in UTF-8. The listing
    /// goes inside no archive, so no item is an archive member and the record's <c>archives</c> is empty.
    /// </summary>
    public IReadOnlyList<BuildListingItem> Items { get; }

    /// <summary>
    /// The record as the YAML the documentation standard gives. Each text value is written in single
    /// quotes, or in double quotes when it holds a single quote.
    /// </summary>
    /// <exception cref="InvalidDataException">
    /// A path, link target or source holds both a single quote and a double quote or a backslash,
    /// which no quoted form the checker reads can hold. The message names the value.
    /// </exception>
    public string ToYaml()
    {
        var text = new StringBuilder();
        text.Append("tool: ").Append(Quoted(Tool)).Append('\n');
        text.Append("date: ").Append(Date.ToString("yyyy-MM-dd", System.Globalization.CultureInfo.InvariantCulture)).Append('\n');
        text.Append("links: ").Append(Links).Append('\n');
        text.Append("cycles: null\n");
        text.Append("media:\n");
        foreach (var medium in Media)
        {
            text.Append("  - prefix: ").Append(Quoted(medium.Prefix)).Append('\n');
            text.Append("    source: ").Append(medium.Source is null ? "null" : Quoted(medium.Source)).Append('\n');
            text.Append("    layout: ").Append(medium.Layout is null ? "null" : Quoted(medium.Layout)).Append('\n');
        }
        text.Append("archives: []\n");
        if (Items.Count == 0) return text.Append("items: []\n").ToString();
        text.Append("items:\n");
        foreach (var item in Items)
        {
            text.Append("  - path: ").Append(Quoted(item.Path)).Append('\n');
            if (item.Size is { } size) text.Append("    size: ").Append(size).Append('\n');
            else if (item.Link is { } link) text.Append("    link: ").Append(Quoted(link)).Append('\n');
            else text.Append("    stopped: ").Append(Quoted(item.Stopped!)).Append('\n');
        }
        return text.ToString();
    }

    // The checker's YAML reader ends a quoted value at the next quote of its kind and reads no
    // escapes, and in double quotes a backslash starts an escape in YAML itself.
    private static string Quoted(string value)
    {
        if (!value.Contains('\'')) return $"'{value}'";
        if (!value.Contains('"') && !value.Contains('\\')) return $"\"{value}\"";
        throw new InvalidDataException(
            $"{AssetVerifier.JsonString(value)} holds a single quote and a double quote or backslash, so the listing record cannot write it.");
    }
}

/// <summary>
/// Makes a build's listing record: the files of an installation directory and of disc images, with
/// their sizes, in the form the documentation standard gives (ENTRY-TYPES-16 and ENTRY-TYPES-17).
/// </summary>
/// <remarks>
/// <para>
/// The installation directory is walked into every subdirectory, hidden and system files included.
/// A symbolic link or junction is an item with its target as stored, and is not entered. Any other
/// reparse point, a link whose target cannot be read, and a directory that cannot be entered are
/// stopped items with the reason. A file's size is taken from the file system; no file is read.
/// </para>
/// <para>
/// A disc is read through <see cref="OriginalContentSource.OpenIso9660(string)"/> or
/// <see cref="OriginalContentSource.OpenCueBin(string)"/>, with their checks. Its paths are the
/// names of its primary volume as that reader gives them: without the version suffix or the dot that
/// ends a name with no extension, except where dropping them would give two entries of one directory
/// the same name. A cue/bin image's audio tracks are file items under their track references
/// (<c>CD:track02</c>), each with the size of its raw audio from its <c>INDEX 01</c> to where the next
/// track's pregap or audio begins, as <see cref="CueBinSheet.TrackExtent"/> gives it.
/// </para>
/// <para>
/// The listing goes inside no archive. A record shows what was listed, not what the game uses; the
/// documentation standard's checker compares it with the build's manifest and list of other files.
/// </para>
/// </remarks>
public static partial class BuildListing
{
    [GeneratedRegex("^CD[0-9]*:$", RegexOptions.CultureInvariant)]
    private static partial Regex DiscPrefix();

    private static readonly EnumerationOptions OneDirectory = new()
    {
        RecurseSubdirectories = false,
        IgnoreInaccessible = false,
        AttributesToSkip = FileAttributes.None,
        MatchType = MatchType.Win32,
        ReturnSpecialDirectories = false
    };

    /// <summary>Lists an installation directory and disc images into one listing record.</summary>
    /// <param name="installationDirectory">The directory the build is installed to, or <see langword="null"/> when the listing covers discs only.</param>
    /// <param name="discs">The discs the game reads, each from an image.</param>
    /// <param name="date">The day the listing is made.</param>
    /// <exception cref="ArgumentException">
    /// Nothing is to be listed, a disc's prefix is not <c>CD:</c> or <c>CD</c> followed by a number and a
    /// colon, two discs share a prefix, a disc's <see cref="BuildListingDisc.Source"/> is blank, or a
    /// disc's image is not an <c>.iso</c>, <c>.cue</c> or <c>.bin</c> file.
    /// </exception>
    /// <exception cref="DirectoryNotFoundException">The installation directory does not exist.</exception>
    /// <exception cref="InvalidDataException">
    /// A name in the installation directory holds a character a listing path cannot hold: a control
    /// character, <c>|</c>, which marks an archive member, or a <c>\</c>, which Linux and macOS allow
    /// in a name. Or a disc image is not valid, as its reader describes.
    /// </exception>
    /// <exception cref="UnauthorizedAccessException">The installation directory itself cannot be read.</exception>
    /// <exception cref="IOException">The installation directory itself cannot be read, or a disc image changed while it was read.</exception>
    public static BuildListingRecord Make(string? installationDirectory, IReadOnlyList<BuildListingDisc> discs, DateOnly date)
    {
        ArgumentNullException.ThrowIfNull(discs);
        if (installationDirectory is null && discs.Count == 0)
            throw new ArgumentException("A listing covers an installation directory, a disc or both.", nameof(discs));
        var prefixes = new HashSet<string>(StringComparer.Ordinal);
        foreach (var disc in discs)
        {
            ArgumentNullException.ThrowIfNull(disc, nameof(discs));
            if (disc.Prefix is null || !DiscPrefix().IsMatch(disc.Prefix))
                throw new ArgumentException($"Disc prefix {AssetVerifier.JsonString(disc.Prefix ?? "")} is not CD:, CD1:, CD2: and so on.", nameof(discs));
            if (!prefixes.Add(disc.Prefix))
                throw new ArgumentException($"Two discs have the prefix {disc.Prefix}.", nameof(discs));
            if (string.IsNullOrWhiteSpace(disc.Source))
                throw new ArgumentException($"Disc {disc.Prefix} gives no source path for its image.", nameof(discs));
        }

        var media = new List<BuildListingMedium>();
        var items = new List<BuildListingItem>();
        if (installationDirectory is not null)
        {
            var root = new DirectoryInfo(Path.GetFullPath(installationDirectory));
            if (!root.Exists)
                throw new DirectoryNotFoundException($"Installation directory {installationDirectory} does not exist.");
            media.Add(new BuildListingMedium("", null, null));
            Walk(root, "", items);
        }
        foreach (var disc in discs) media.Add(ListDisc(disc, items));

        var sorted = items.Select(item => (Key: Encoding.UTF8.GetBytes(item.Path), Item: item))
            .OrderBy(pair => pair.Key, ByteOrder.Instance).ToArray();
        for (var index = 1; index < sorted.Length; index++)
            if (sorted[index].Key.AsSpan().SequenceEqual(sorted[index - 1].Key))
                throw new InvalidDataException($"The listing gives {AssetVerifier.JsonString(sorted[index].Item.Path)} twice.");
        return new BuildListingRecord(ToolName(), date, media, sorted.Select(pair => pair.Item).ToArray());
    }

    private static void Walk(DirectoryInfo directory, string relative, List<BuildListingItem> items)
    {
        FileSystemInfo[] entries;
        try
        {
            entries = directory.EnumerateFileSystemInfos("*", OneDirectory).ToArray();
        }
        catch (Exception exception) when (relative.Length > 0 &&
            exception is UnauthorizedAccessException or IOException or SecurityException)
        {
            items.Add(new(relative, null, null, $"a directory it could not enter: {exception.Message}"));
            return;
        }
        foreach (var entry in entries)
        {
            var path = relative.Length == 0 ? entry.Name : $"{relative}/{entry.Name}";
            CheckName(entry.Name, path);
            if ((entry.Attributes & FileAttributes.ReparsePoint) != 0)
            {
                items.Add(Linked(entry, path));
                continue;
            }
            if (entry is DirectoryInfo subdirectory) Walk(subdirectory, path, items);
            else items.Add(new(path, ((FileInfo)entry).Length, null, null));
        }
    }

    // A symbolic link or junction gives its target; other reparse points, such as a cloud file's
    // placeholder, have none to give and are not entered.
    private static BuildListingItem Linked(FileSystemInfo entry, string path)
    {
        string? target;
        try
        {
            target = entry.LinkTarget;
        }
        catch (Exception exception) when (exception is UnauthorizedAccessException or IOException)
        {
            return new(path, null, null, $"a link whose target could not be read: {exception.Message}");
        }
        return target is null
            ? new(path, null, null, "a reparse point that is not a symbolic link or junction, not entered")
            : new(path, null, target, null);
    }

    private static void CheckName(string name, string path)
    {
        foreach (var c in name)
        {
            var problem = c < ' ' || c == '\u007f' ? $"the control character U+{(int)c:X4}"
                : c == '|' ? "'|', which marks an archive member in a listing path"
                : c == '\\' ? "'\\', which reads as a separator in a listing path"
                : null;
            if (problem is not null)
                throw new InvalidDataException($"{AssetVerifier.JsonString(path)} holds {problem}.");
        }
    }

    private static BuildListingMedium ListDisc(BuildListingDisc disc, List<BuildListingItem> items)
    {
        var extension = Path.GetExtension(disc.ImagePath);
        var cueBin = extension.Equals(".cue", StringComparison.OrdinalIgnoreCase) ||
            extension.Equals(".bin", StringComparison.OrdinalIgnoreCase);
        if (!cueBin && !extension.Equals(".iso", StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException(
                $"Disc {disc.Prefix} is read from an .iso, .cue or .bin image, not {disc.ImagePath}. " +
                "A mounted disc gives the names the drive chooses, such as Joliet names, not those of the primary volume.",
                "discs");
        using var source = cueBin ? OriginalContentSource.OpenCueBin(disc.ImagePath) : OriginalContentSource.OpenIso9660(disc.ImagePath);
        foreach (var file in source.Files) items.Add(new($"{disc.Prefix}{file.Path}", file.Size, null, null));
        if (source.Cue is not { } sheet) return new BuildListingMedium(disc.Prefix, disc.Source, "2048");
        long imageSectors;
        using (var bin = source.OpenBin()) imageSectors = bin.Length / CueBinSheet.RawSectorSize;
        foreach (var track in sheet.Tracks.Skip(1))
        {
            var extent = sheet.TrackExtent(track.Number, imageSectors);
            items.Add(new($"{disc.Prefix}track{track.Number:D2}", extent.Sectors * CueBinSheet.RawSectorSize, null, null));
        }
        return new BuildListingMedium(disc.Prefix, disc.Source, sheet.Tracks[0].Type);
    }

    private static string ToolName()
    {
        var assembly = typeof(BuildListing).Assembly;
        var version = assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
            ?? assembly.GetName().Version?.ToString() ?? "unknown";
        return $"RefurbishedDinosaurs.LegacyFormats {version}";
    }

    private sealed class ByteOrder : IComparer<byte[]>
    {
        public static readonly ByteOrder Instance = new();
        public int Compare(byte[]? x, byte[]? y) => x.AsSpan().SequenceCompareTo(y);
    }
}
