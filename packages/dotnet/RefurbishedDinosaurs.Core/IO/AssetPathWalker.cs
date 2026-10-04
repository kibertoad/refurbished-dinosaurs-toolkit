using System.IO.Enumeration;

namespace RefurbishedDinosaurs.Core.IO;

/// <summary>
/// Walks a portable relative reference below one root, one component at a time, ignoring ordinal
/// case. This holds the rules <see cref="PortableAssetPath.ResolveFile"/>,
/// <see cref="PortableAssetPath.ResolveDirectory"/> and content overlays share: a component must
/// match exactly one entry, even when one of the matches is spelled exactly; an entry that is a
/// symbolic link or reparse point is rejected; and a component before the last must be a directory.
/// Hidden, system and reparse entries are listed and an unreadable directory throws, so nothing a
/// different host would see is skipped.
/// </summary>
/// <remarks>
/// The caller checks the root itself. With <c>cacheListings</c> set, each directory is listed once
/// and kept for every later walk through the same instance, which suits many lookups under a root
/// that does not change while the instance is in use. Without it, each walk reads only the entries
/// that match.
/// </remarks>
internal sealed class AssetPathWalker(string root, bool cacheListings)
{
    private static readonly EnumerationOptions EntryOptions = new()
    {
        AttributesToSkip = 0,
        IgnoreInaccessible = false,
        MatchType = MatchType.Simple,
        RecurseSubdirectories = false,
        ReturnSpecialDirectories = false
    };

    private readonly Dictionary<string, ILookup<string, Entry>> _listings = new(StringComparer.Ordinal);

    /// <summary>The full path of the root the walker resolves below.</summary>
    public string Root { get; } = root;

    /// <summary>
    /// Matches the components of <paramref name="relative"/>, which must already be portable, until
    /// one does not exist.
    /// </summary>
    /// <returns>
    /// The actual spelling of each component that exists, in order, and whether the last of them is
    /// a directory. Fewer spellings than components means the next component does not exist.
    /// </returns>
    /// <exception cref="InvalidDataException">
    /// A component matches more than one entry, matches a link, or matches a file while more
    /// components follow it.
    /// </exception>
    public (IReadOnlyList<string> Spelled, bool IsDirectory) Walk(string relative)
    {
        var parts = relative.Split('/');
        var spelled = new List<string>(parts.Length);
        var current = Root;
        var isDirectory = true;
        for (var index = 0; index < parts.Length; index++)
        {
            var matches = Matches(current, parts[index]);
            if (matches.Length == 0) break;
            var spelledSoFar = string.Join('/', spelled.Append(parts[index]));
            if (matches.Length > 1) throw new InvalidDataException($"Asset spelling is ambiguous: {spelledSoFar}");
            var match = matches[0];
            if ((match.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException($"Asset path passes through a symbolic link or reparse point: {spelledSoFar}");
            if (!match.IsDirectory && index < parts.Length - 1)
                throw new InvalidDataException($"Asset path passes through a file: {spelledSoFar}");
            spelled.Add(match.Name);
            current = Path.Join(current, match.Name);
            isDirectory = match.IsDirectory;
        }
        return (spelled, isDirectory);
    }

    /// <summary>Up to two entries of <paramref name="directory"/> named <paramref name="name"/> ignoring case.</summary>
    private Entry[] Matches(string directory, string name)
    {
        if (!cacheListings)
            return new FileSystemEnumerable<Entry>(directory, (ref FileSystemEntry entry) => Read(ref entry), EntryOptions)
            {
                ShouldIncludePredicate = (ref FileSystemEntry entry) =>
                    entry.FileName.Equals(name, StringComparison.OrdinalIgnoreCase)
            }.Take(2).ToArray();
        if (!_listings.TryGetValue(directory, out var listing))
        {
            listing = new FileSystemEnumerable<Entry>(directory, (ref FileSystemEntry entry) => Read(ref entry), EntryOptions)
                .ToLookup(entry => entry.Name, StringComparer.OrdinalIgnoreCase);
            _listings.Add(directory, listing);
        }
        return listing[name].Take(2).ToArray();
    }

    private static Entry Read(ref FileSystemEntry entry) =>
        new(entry.FileName.ToString(), entry.Attributes, entry.IsDirectory);

    private readonly record struct Entry(string Name, FileAttributes Attributes, bool IsDirectory);
}
