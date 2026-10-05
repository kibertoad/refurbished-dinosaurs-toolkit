using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>
/// One listed member of an <see cref="InstallShieldCabinetSource"/> or an
/// <see cref="InstallShieldArchiveSource"/>, with the file-table entry it is listed from.
/// </summary>
/// <param name="Entry">
/// The member as <see cref="OriginalContentSource.Files"/> lists it. Its path is the entry's directory
/// and name joined, which is the path <see cref="OriginalContentSource.OpenRead"/> takes.
/// </param>
/// <param name="Metadata">The file-table entry the member is listed from.</param>
/// <param name="SharedBy">
/// Cabinet entries at the member's path, ignoring case, that are not listed because they hold the same
/// data: an entry that links to the member's data, and a version 6 copy with the same expanded size and
/// MD5. Each is also in <see cref="InstallShieldCabinetSource.SkippedFiles"/>. Reading the member gives
/// their bytes, so an adapter that places entries by file group can place these too. Empty for an
/// InstallShield 3 archive, which refuses two entries at one path.
/// </param>
public sealed record InstallShieldMember(
    ContentSourceEntry Entry, InstallShieldEntryMetadata Metadata, IReadOnlyList<InstallShieldEntryMetadata> SharedBy);

/// <summary>What the header records about one file-table entry, beyond its data.</summary>
/// <param name="Index">The entry's index in the cabinet's or archive's file table.</param>
/// <param name="DirectoryIndex">The index of the directory the entry names.</param>
/// <param name="Directory">
/// The directory's name with <c>/</c> separators, or an empty string for the root. It is the part of
/// the member's path that comes from the directory, and <see cref="PortableAssetPath.Relative"/> has
/// accepted it.
/// </param>
/// <param name="Name">
/// The entry's name as the header gives it, with any <c>\</c> written as <c>/</c>. The member's path is
/// <see cref="Directory"/> and this name joined with <c>/</c> (this name alone when the directory is empty).
/// </param>
/// <param name="FileGroups">The file groups whose ranges hold the entry.</param>
public sealed record InstallShieldEntryMetadata(
    int Index, int DirectoryIndex, string Directory, string Name, InstallShieldFileGroupMembership FileGroups);

/// <summary>How an entry's file-group relationship reads from the header.</summary>
public enum InstallShieldFileGroupMembershipKind
{
    /// <summary>
    /// The source records no file groups: a cabinet whose descriptor lists none, or an InstallShield 3
    /// archive, whose format has no file groups.
    /// </summary>
    NoFileGroups,
    /// <summary>The cabinet lists file groups, every one read whole, and none of their ranges holds the entry.</summary>
    None,
    /// <summary>
    /// The cabinet lists file groups, every one read whole, and exactly one range holds the entry.
    /// </summary>
    One,
    /// <summary>
    /// The cabinet lists file groups, every one read whole, and more than one range holds the entry.
    /// The header does not say which of them installs it, so no one of them is chosen.
    /// </summary>
    Several,
    /// <summary>
    /// The groups that hold the entry are not known: the group list did not read whole
    /// (<see cref="InstallShieldCabinetSource.FileGroupProblem"/>), or a group whose range is malformed
    /// may hold it (<see cref="InstallShieldFileGroupMembership.MalformedGroups"/>).
    /// <see cref="InstallShieldFileGroupMembership.Groups"/> still lists the well-formed groups that
    /// were read and hold it, unless the ranges name more (entry, group) pairs than the file limit, when
    /// it is empty.
    /// </summary>
    Undetermined
}

/// <summary>The file groups whose ranges hold one file-table entry.</summary>
/// <param name="Kind">How the relationship reads.</param>
/// <param name="Groups">
/// Indexes into <see cref="InstallShieldCabinetSource.FileGroups"/> of the groups with a well-formed range
/// that holds the entry, in list order.
/// </param>
/// <param name="MalformedGroups">
/// Indexes into <see cref="InstallShieldCabinetSource.FileGroups"/> of the groups whose range is not
/// well-formed and may hold the entry, in list order: a group whose range did not read or is reversed may
/// hold any entry, and a group whose range reaches past the file table may hold any entry from its first
/// file on. Not empty only when <see cref="Kind"/> is
/// <see cref="InstallShieldFileGroupMembershipKind.Undetermined"/>.
/// </param>
public sealed record InstallShieldFileGroupMembership(
    InstallShieldFileGroupMembershipKind Kind, IReadOnlyList<int> Groups, IReadOnlyList<int> MalformedGroups)
{
    internal static InstallShieldFileGroupMembership NoFileGroups { get; } =
        new(InstallShieldFileGroupMembershipKind.NoFileGroups, [], []);
}

/// <summary>
/// One file group an InstallShield cabinet's descriptor lists. A group names a range of file-table
/// indexes; the cabinet records nothing that ties an entry to a group other than that range.
/// </summary>
/// <param name="Index">The group's position in <see cref="InstallShieldCabinetSource.FileGroups"/>.</param>
/// <param name="Name">
/// The group's name, read as ISO 8859-1, or <see langword="null"/> when it does not read. The name is
/// data: it is not checked with <see cref="PortableAssetPath.Relative"/>, since an InstallShield group
/// name need not be a file name. A caller that builds a path from it checks that path.
/// </param>
/// <param name="FirstFile">
/// The first file-table index of the group's range, the header's unsigned 32-bit value, or
/// <see langword="null"/> when it does not read.
/// </param>
/// <param name="LastFile">
/// The last file-table index of the group's range, inclusive, the header's unsigned 32-bit value, or
/// <see langword="null"/> when it does not read.
/// </param>
/// <param name="Problem">
/// Why the group's descriptor, name or range is not usable, or <see langword="null"/> when all three
/// read and the range lies inside the file table. A group whose range is well-formed gives its members
/// even when its name has a problem.
/// </param>
public sealed record InstallShieldFileGroup(int Index, string? Name, long? FirstFile, long? LastFile, string? Problem);

// The file groups of a cabinet, read as Unshield reads them: the cabinet descriptor holds 71 offsets
// at 0x3e, each the head of a list of 12-byte entries (name offset, descriptor offset, next entry
// offset), and each group descriptor starts with its name offset and holds its first and last file
// index at 0x4c and 0x50 for major versions 0 and 5, or 0x16 and 0x1a for version 6, unsigned as
// Unshield reads them. Every offset is relative to the cabinet descriptor. Nothing here fails the open: what does not read is reported.
internal sealed class InstallShieldFileGroupTable
{
    private const int ListOffset = 0x3e;
    private const int ListCount = 71;
    private const int ListEntrySize = 12;

    // Whether the descriptor lists any group, or its lists did not read.
    private readonly bool listed;
    private readonly int fileCount;
    private readonly int maximumPairs;

    private InstallShieldFileGroupTable(
        IReadOnlyList<InstallShieldFileGroup> groups, bool listed, string? problem, int fileCount, int maximumPairs)
    {
        Groups = groups;
        Problem = problem;
        this.listed = listed;
        this.fileCount = fileCount;
        this.maximumPairs = maximumPairs;
    }

    public IReadOnlyList<InstallShieldFileGroup> Groups { get; }
    // Why the lists did not read whole, or null. Memberships adds the pair limit to it.
    public string? Problem { get; }

    public static InstallShieldFileGroupTable Read(
        InstallShieldHeaderReader reader, long descriptor, int majorVersion, int fileCount, InstallShieldCabinetLimits limits)
    {
        var groups = new List<InstallShieldFileGroup>();
        // The entries every list has reached, and those the current list has. A list that returns to
        // its own entry loops. A list that reaches an entry an earlier list read joins that list, so
        // the rest of it is read already: it ends there, and each group is listed once.
        var read = new HashSet<uint>();
        var visited = new HashSet<uint>();
        // Group names by offset, so groups that share a name share one string.
        var names = new Dictionary<uint, (string? Name, string? Problem)>();
        var listed = false;
        var (firstAt, lastAt) = majorVersion == 6 ? (0x16, 0x1a) : (0x4c, 0x50);
        for (var list = 0; list < ListCount; list++)
        {
            uint next;
            visited.Clear();
            try
            {
                next = reader.UInt32(descriptor + ListOffset + 4L * list);
            }
            catch (InvalidDataException exception)
            {
                return new(groups, true, $"The cabinet descriptor's file group lists do not read. {exception.Message}", fileCount, limits.MaximumFiles);
            }
            while (next != 0)
            {
                listed = true;
                if (!visited.Add(next))
                    return new(groups, true, $"File group list {list} returns to its entry at descriptor offset 0x{next:x}.", fileCount, limits.MaximumFiles);
                if (!read.Add(next)) break;
                if (groups.Count == limits.MaximumFiles)
                    return new(groups, true, $"The cabinet lists more than {limits.MaximumFiles} file groups, the file limit; the rest are not read.", fileCount, limits.MaximumFiles);
                uint groupDescriptor;
                try
                {
                    reader.Require(descriptor + next, ListEntrySize, "file group list entry");
                    groupDescriptor = reader.UInt32(descriptor + next + 4);
                    next = reader.UInt32(descriptor + next + 8);
                }
                catch (InvalidDataException exception)
                {
                    return new(groups, true, $"An entry of file group list {list} does not read. {exception.Message}", fileCount, limits.MaximumFiles);
                }
                groups.Add(ReadGroup(reader, descriptor, groups.Count, groupDescriptor, firstAt, lastAt, fileCount, names));
            }
        }
        return new(groups, listed, null, fileCount, limits.MaximumFiles);
    }

    private static InstallShieldFileGroup ReadGroup(
        InstallShieldHeaderReader reader, long descriptor, int index, uint offset, int firstAt, int lastAt, int fileCount,
        Dictionary<uint, (string? Name, string? Problem)> names)
    {
        if (offset == 0) return new(index, null, null, null, "The group's list entry names no group descriptor.");
        uint nameOffset;
        uint first, last;
        try
        {
            reader.Require(descriptor + offset, lastAt + 4, "file group descriptor");
            nameOffset = reader.UInt32(descriptor + offset);
            first = reader.UInt32(descriptor + offset + firstAt);
            last = reader.UInt32(descriptor + offset + lastAt);
        }
        catch (InvalidDataException exception)
        {
            return new(index, null, null, null, $"The group's descriptor does not read. {exception.Message}");
        }

        var problems = new List<string>();
        string? name = null;
        if (nameOffset == 0)
            problems.Add("The group has no name.");
        else
        {
            if (!names.TryGetValue(nameOffset, out var read))
            {
                try
                {
                    read = (reader.String(descriptor + nameOffset, "file group name"), null);
                }
                catch (InvalidDataException exception)
                {
                    read = (null, $"The group's name does not read. {exception.Message}");
                }
                names[nameOffset] = read;
            }
            name = read.Name;
            if (read.Problem is not null) problems.Add(read.Problem);
        }
        if (first > last)
            problems.Add($"Its range, files {first} to {last}, is reversed.");
        else if (last >= fileCount)
            problems.Add($"Its range, files {first} to {last}, reaches past the {fileCount}-entry file table.");
        return new(index, name, first, last, problems.Count == 0 ? null : string.Join(' ', problems));
    }

    // The membership of each entry in entries (file-table indexes, ascending, distinct), and Problem
    // with the pair limit added when it is reached. The groups' ranges together may name at most the
    // file limit of (entry, group) pairs, which keeps the lists this builds bounded however the ranges
    // overlap.
    public (Dictionary<int, InstallShieldFileGroupMembership> ByEntry, string? Problem) Memberships(int[] entries)
    {
        var result = new Dictionary<int, InstallShieldFileGroupMembership>(entries.Length);
        if (!listed && Problem is null)
        {
            foreach (var entry in entries) result[entry] = InstallShieldFileGroupMembership.NoFileGroups;
            return (result, null);
        }

        var held = new Dictionary<int, List<int>>();
        var suspected = new Dictionary<int, List<int>>();
        long pairs = 0;
        var exhausted = false;
        foreach (var group in Groups)
        {
            long from, to;
            Dictionary<int, List<int>> target;
            if (group is { FirstFile: { } first, LastFile: { } last } && first <= last)
            {
                // A range inside the table holds its entries; one that reaches past it may hold the
                // entries from its first file on.
                (from, to, target) = last < fileCount ? (first, last, held) : (first, long.MaxValue, suspected);
            }
            else
                (from, to, target) = (0, long.MaxValue, suspected);

            // A range that starts past the table holds no entry.
            if (from >= fileCount) continue;
            var start = Array.BinarySearch(entries, (int)from);
            if (start < 0) start = ~start;
            for (var position = start; position < entries.Length && entries[position] <= to; position++)
            {
                if (++pairs > maximumPairs)
                {
                    exhausted = true;
                    break;
                }
                if (!target.TryGetValue(entries[position], out var groups)) target[entries[position]] = groups = [];
                groups.Add(group.Index);
            }
            if (exhausted) break;
        }

        var problem = Problem;
        if (exhausted)
        {
            var pairsProblem = $"The file groups' ranges name more than {maximumPairs} entries with their groups, the file limit; memberships are not read.";
            problem = problem is null ? pairsProblem : $"{problem} {pairsProblem}";
            held.Clear();
            suspected.Clear();
        }
        foreach (var entry in entries)
        {
            IReadOnlyList<int> groups = held.TryGetValue(entry, out var heldBy) ? heldBy : [];
            IReadOnlyList<int> malformed = suspected.TryGetValue(entry, out var suspectedBy) ? suspectedBy : [];
            var kind = problem is not null || malformed.Count > 0 ? InstallShieldFileGroupMembershipKind.Undetermined
                : groups.Count switch
                {
                    0 => InstallShieldFileGroupMembershipKind.None,
                    1 => InstallShieldFileGroupMembershipKind.One,
                    _ => InstallShieldFileGroupMembershipKind.Several
                };
            result[entry] = new(kind, groups, malformed);
        }
        return (result, problem);
    }
}
