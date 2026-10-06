using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>
/// One file of an <see cref="InstallShieldCabinetSource"/> or an <see cref="InstallShieldArchiveSource"/>,
/// with the file-table entry it is read from: a listed member (the source's <c>Members</c>), one of
/// the files at a path that holds different files (<see cref="InstallShieldPathConflict.Files"/>), or a
/// cabinet's file stored outside its volumes (<see cref="InstallShieldOutsideFile.Member"/>).
/// </summary>
/// <param name="Entry">
/// The file's path and expanded size. Its path is the entry's directory and name joined. For a listed
/// member it is the entry <see cref="OriginalContentSource.Files"/> lists and the path
/// <see cref="OriginalContentSource.OpenRead"/> takes; for a file of a path conflict or a file stored
/// outside, no file is listed at the path, and the source's <c>OpenEntry</c> reads it by
/// <see cref="InstallShieldEntryMetadata.Index"/> when the source reads it.
/// </param>
/// <param name="Metadata">The file-table entry the file is read from.</param>
/// <param name="SharedBy">
/// Cabinet entries at the file's path, ignoring case, that hold the same data: an entry that links to
/// the file's data, and a version 6 copy with the same expanded size and MD5. Each is also in
/// <see cref="InstallShieldCabinetSource.SkippedFiles"/>. Reading the file gives their bytes, so an
/// adapter that places entries by file group can place these too. Empty for an InstallShield 3
/// archive, which has no links or checksums.
/// </param>
public sealed record InstallShieldMember(
    ContentSourceEntry Entry, InstallShieldEntryMetadata Metadata, IReadOnlyList<InstallShieldEntryMetadata> SharedBy);

/// <summary>
/// A path, ignoring case, that holds two or more files an <see cref="InstallShieldCabinetSource"/> or an
/// <see cref="InstallShieldArchiveSource"/> cannot show to be the same. The source lists none of them
/// at the path and does not choose one: each is read by its entry's index with the source's
/// <c>OpenEntry</c>, and every entry at the path is in the source's <c>SkippedFiles</c>.
/// </summary>
/// <param name="Path">The path as the first entry at it in table order spells it.</param>
/// <param name="Files">
/// The files at the path whose data the source reads, in the table order of their first entries. Each
/// entry's own spelling of the path is in its <see cref="InstallShieldMember.Entry"/>. A cabinet decides
/// two entries hold the same file only when one links to the other's data, or, in version 6, when
/// their expanded sizes and MD5s match; equal names or sizes alone never make them one file.
/// </param>
/// <param name="StoredOutside">
/// The entries at the path whose data is stored outside the cabinet and whose file the cabinet does
/// not also hold inside, in table order, which <see cref="InstallShieldCabinetSource.SkippedFiles"/>
/// lists as <see cref="InstallShieldSkippedFileKind.StoredOutsideCabinet"/>. They are not read, since
/// a file found beside the header at the path cannot be told to be any one of the path's files
/// (<see cref="InstallShieldOutsideFileStatus.PathHeldByDifferentFiles"/>). An entry stored outside that a version 6 copy inside the cabinet matches in expanded size and MD5 is
/// in that copy's <see cref="InstallShieldMember.SharedBy"/> in <paramref name="Files"/> instead, and
/// reads that copy's bytes. Empty for an InstallShield 3 archive.
/// </param>
public sealed record InstallShieldPathConflict(
    string Path, IReadOnlyList<InstallShieldMember> Files, IReadOnlyList<InstallShieldEntryMetadata> StoredOutside)
{
    // The reason every entry at such a path is skipped with, naming the first entry of each file:
    // "files 3 and 14", "files 3, 14 and 20". A conflict holds at least two files.
    internal static string HeldReason(IEnumerable<int> firstEntries)
    {
        var all = firstEntries.Select(index => index.ToString(System.Globalization.CultureInfo.InvariantCulture)).ToArray();
        return $"The path holds different files: files {string.Join(", ", all[..^1])} and {all[^1]}, so none is listed at it.";
    }
}

/// <summary>
/// What an <see cref="InstallShieldCabinetSource"/> found where it looked for a file stored outside the
/// cabinet: beside the header, at <see cref="InstallShieldOutsideFile.LookupPath"/>, matched ignoring case.
/// </summary>
public enum InstallShieldOutsideFileStatus
{
    /// <summary>
    /// Exactly one file is at the path, its length is exactly the entry's stored size, and the path
    /// holds no other file of the cabinet. The source's <c>OpenEntry</c> reads it.
    /// </summary>
    Available,

    /// <summary>No file is at the path.</summary>
    Missing,

    /// <summary>
    /// One file is at the path, and its length (<see cref="InstallShieldOutsideFile.FoundLength"/>)
    /// is not the entry's stored size. It is not read: the reader does not know how such a file
    /// relates to the entry's data.
    /// </summary>
    LengthDiffers,

    /// <summary>
    /// More than one file matches the path ignoring case, which a case-sensitive file system can hold.
    /// None of them is read.
    /// </summary>
    SeveralMatches,

    /// <summary>
    /// The path, ignoring case, holds other files of the cabinet too
    /// (<see cref="InstallShieldCabinetSource.PathConflicts"/>), so a file there cannot be told to be
    /// this one. It is not read. <see cref="InstallShieldOutsideFile.FoundLength"/> still gives the
    /// length of a file found there.
    /// </summary>
    PathHeldByDifferentFiles,

    /// <summary>
    /// The entry holding the file's data is a version 6 link target with no name, or with a path
    /// <see cref="PortableAssetPath.Relative"/> does not accept, so there is no path to look up.
    /// </summary>
    NoUsablePath,
}

/// <summary>
/// One file an <see cref="InstallShieldCabinetSource"/> holds outside its volumes: an entry whose data
/// offset is the length of the volume that would hold it, with the entries that share its data.
/// </summary>
/// <param name="Member">
/// The file's path and expanded size, the entry it was first found at, and the entries at its path that
/// share its data, with their file groups.
/// </param>
/// <param name="LookupPath">
/// Where the file was looked for, relative to the folder that holds the header: the directory and name
/// of the entry that holds its data, which for a version 6 link is the entry the link ends at. It is
/// <see langword="null"/> with <see cref="InstallShieldOutsideFileStatus.NoUsablePath"/>.
/// </param>
/// <param name="StoredSize">
/// The bytes the header says the cabinet stores for the file: its compressed size when
/// <paramref name="Compressed"/>, its expanded size otherwise. A file found must have exactly this length to be read.
/// </param>
/// <param name="Compressed">Whether the entry is compressed, so the file found is read as compressed data.</param>
/// <param name="Status">What was found at <paramref name="LookupPath"/>.</param>
/// <param name="FoundLength">
/// The length of the file found when exactly one matched, otherwise <see langword="null"/>.
/// </param>
public sealed record InstallShieldOutsideFile(
    InstallShieldMember Member, string? LookupPath, long StoredSize, bool Compressed,
    InstallShieldOutsideFileStatus Status, long? FoundLength);

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
