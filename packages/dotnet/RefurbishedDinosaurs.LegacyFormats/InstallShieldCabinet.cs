using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Text;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>
/// Bounds applied when an InstallShield cabinet set is opened. Each is checked against what the
/// header declares, before any member is read.
/// </summary>
/// <param name="MaximumFiles">The most members the set may list.</param>
/// <param name="MaximumExpandedBytes">The most bytes all listed members may expand to together.</param>
/// <param name="MaximumHeaderBytes">
/// The most header bytes read into memory. A <c>.hdr</c> header is read whole, so a longer file fails
/// the open. A <c>.cab</c> that holds the header is read from its start only as far as the header's
/// structures reach (the cabinet descriptor, the file table, the file descriptors and the names),
/// wherever they lie before the member data, so a cabinet of any size opens. A structure that ends
/// past this many bytes into the <c>.cab</c> fails the open with a message naming the limit; one that
/// ends past the end of the file is reported as a truncated header. The volumes are found by name,
/// so a <c>data1.cab</c> that holds the header is also read as volume 1.
/// </param>
/// <param name="MaximumNameBytes">
/// The longest file or directory name, in bytes before its NUL terminator, that is read from the
/// header. A listed member whose name is longer fails the open; an entry left out with such a name
/// is skipped with a <see langword="null"/> path. The bound keeps the work spent on each name small,
/// so a header whose entries all point into one long unterminated run cannot make every entry scan it.
/// </param>
public sealed record InstallShieldCabinetLimits(
    int MaximumFiles = 100_000,
    long MaximumExpandedBytes = 8L * 1024 * 1024 * 1024,
    int MaximumHeaderBytes = 64 * 1024 * 1024,
    int MaximumNameBytes = 1024)
{
    /// <summary>The default limits.</summary>
    public static InstallShieldCabinetLimits Default { get; } = new();

    internal void Validate()
    {
        ArgumentOutOfRangeException.ThrowIfNegative(MaximumFiles);
        ArgumentOutOfRangeException.ThrowIfNegative(MaximumExpandedBytes);
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(MaximumHeaderBytes);
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(MaximumNameBytes);
    }
}

/// <summary>
/// A file-table entry an <see cref="InstallShieldCabinetSource"/> or an
/// <see cref="InstallShieldArchiveSource"/> does not list.
/// </summary>
/// <param name="Index">The entry's index in the cabinet's or archive's file table.</param>
/// <param name="Path">
/// The entry's path, when it has a name that reads and <see cref="PortableAssetPath.Relative"/> accepts; otherwise <see langword="null"/>.
/// </param>
/// <param name="Reason">Why the entry is not listed.</param>
public sealed record InstallShieldSkippedFile(int Index, string? Path, string Reason);

/// <summary>
/// The files of an InstallShield cabinet set of major version 0, 5 or 6 (<c>dataN.hdr</c> with
/// <c>dataN.cab</c> volumes) as an <see cref="OriginalContentSource"/>. Open it with
/// <see cref="OriginalContentSource.OpenInstallShieldCabinet(string, InstallShieldCabinetLimits?)"/>
/// or, for a set inside another source such as a disc image,
/// <see cref="OriginalContentSource.OpenInstallShieldCabinet(OriginalContentSource, string, InstallShieldCabinetLimits?)"/>.
/// </summary>
/// <remarks>
/// <para>
/// Opening reads the header and every volume header, and checks each listed member: its path is
/// accepted by <see cref="PortableAssetPath.Relative"/>, its data lies inside the volumes, and the
/// member count and expanded total are within <see cref="InstallShieldCabinetLimits"/>. Members are
/// decoded only when read.
/// </para>
/// <para>
/// Major version 0 is the version word Unshield reads as 0, such as <c>0x01000004</c>. Its file
/// descriptors and volume headers have the version 5 layout, with two differences: a version 0
/// descriptor ends after the data offset, and a version 0 member is split across volumes only when
/// its descriptor says so. Everything this class says of version 5 holds for version 0 too.
/// </para>
/// <para>
/// Two entries at one path, ignoring case, that share data through a link are listed once: the
/// first in table order is listed, and the other goes to <see cref="SkippedFiles"/>. In a version 6
/// set, two entries stored apart at one path with the same expanded size and the same MD5 are taken
/// as one file: the first in table order is listed, and the other goes to <see cref="SkippedFiles"/>
/// without its stored bytes being read. Any other pair of entries at one
/// path fails the open, and so does every such pair in a version 5 set, which records no MD5.
/// </para>
/// <para>
/// Reading a member to its end checks that its data expands to exactly the declared size and, for
/// a version 6 set, that the expanded bytes match the MD5 the header records. A failed check throws
/// <see cref="InvalidDataException"/> from the read that would have returned the member's last bytes.
/// Version 5 members are checked by size only.
/// </para>
/// <para>
/// <see cref="Members"/> gives each listed member's file-table index, directory and name, and the
/// file groups whose ranges hold it, read from the header the open has already read. A member's path
/// stays its directory and name joined; an installer that places files by group does so outside the
/// cabinet, so a caller that needs installed paths builds them from this metadata. A group name is
/// reported as data and is not checked as a path. The groups never fail the open: a list that does not
/// read, a malformed group and an entry held by several groups are each reported, with
/// <see cref="FileGroupProblem"/>, <see cref="InstallShieldFileGroup.Problem"/> and
/// <see cref="InstallShieldFileGroupMembership.Kind"/>.
/// </para>
/// </remarks>
public sealed class InstallShieldCabinetSource : OriginalContentSource
{
    // The layout (common header, cabinet descriptor, file table, volume headers, chunked deflate and
    // the obfuscation) follows Unshield's reading of the format: https://github.com/twogood/unshield.
    private const uint Signature = 0x28635349;
    private const uint MicrosoftCabinetSignature = 0x4643534d;
    private const int CommonHeaderSize = 20;
    private const int DescriptorFieldsSize = 0x30;
    private const int Version6DescriptorSize = 0x57;
    private const int Version5DescriptorSize = 0x3a;
    // Unshield reads a version 0 descriptor only up to its data offset, with no MD5 after it.
    private const int Version0DescriptorSize = 0x2a;
    private const int MaximumVolume = 255;
    private const ushort InvalidFlag = 8;
    private const ushort CompressedFlag = 4;
    private const ushort ObfuscatedFlag = 2;
    private const ushort SplitFlag = 1;
    private const byte LinkPreviousFlag = 1;

    private readonly Func<int, Stream> openVolume;
    private readonly Dictionary<string, Member> members = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<int, VolumeHeader> volumes = [];
    // Major versions 0 and 5 share the file descriptor and volume header layout.
    private readonly bool version5Layout;

    // limits has been validated by the caller.
    internal InstallShieldCabinetSource(
        InstallShieldHeaderReader reader, Func<int, Stream> openVolume, InstallShieldCabinetLimits limits)
    {
        this.openVolume = openVolume;
        var signature = reader.UInt32(0);
        if (signature == MicrosoftCabinetSignature)
            throw new InvalidDataException("File is a Microsoft cabinet, not an InstallShield cabinet.");
        if (signature != Signature)
            throw new InvalidDataException("File is not an InstallShield cabinet header.");
        var version = reader.UInt32(4);
        MajorVersion = ReadMajorVersion(version) ?? throw new NotSupportedException(
            $"InstallShield cabinet version word 0x{version:x8} follows neither version-word encoding the reader knows.");
        if (MajorVersion is not (0 or 5 or 6))
            throw new NotSupportedException(
                $"InstallShield cabinet version word 0x{version:x8} reads as major version {MajorVersion}; only 0, 5 and 6 are supported.");
        version5Layout = MajorVersion is 0 or 5;

        long descriptor = reader.UInt32(12);
        // As in Unshield, the declared descriptor size only has to be nonzero. It bounds nothing the
        // reader reads, so the descriptor's fields are read whatever size it declares.
        if (reader.UInt32(16) == 0)
            throw new InvalidDataException("InstallShield header has no cabinet descriptor.");
        reader.Require(descriptor, DescriptorFieldsSize, "cabinet descriptor");
        var table = descriptor + reader.UInt32(descriptor + 0x0c);
        var directoryCount = reader.UInt32(descriptor + 0x1c);
        var fileCount = reader.UInt32(descriptor + 0x28);
        long descriptorsOffset = reader.UInt32(descriptor + 0x2c);
        if (fileCount > limits.MaximumFiles)
            throw new InvalidDataException(
                $"InstallShield cabinet declares {fileCount} files, more than the limit of {limits.MaximumFiles}.");
        // The directory part of the file table has to be in the header before its names are read,
        // which also bounds the array below by the bytes the header can hold.
        reader.Require(table, 4L * directoryCount, "file table");

        // A directory's name is read when an entry needs it, so a name no entry uses is never read.
        var directories = new string?[directoryCount];
        string DirectoryName(int index, ushort directory)
        {
            if (directory >= directories.Length)
                throw new InvalidDataException($"InstallShield file {index} names directory {directory}, which does not exist.");
            return directories[directory] ??= reader.String(table + reader.UInt32(table + 4L * directory), "directory name");
        }

        // The path, and the directory and name it joins with / separators. When the joined path is
        // accepted, so is the directory alone, since its characters and components are among the path's.
        (string Path, string Directory, string Name) EntryPath(int index, FileDescriptor file)
        {
            var name = reader.String(table + file.NameOffset, "file name");
            var directory = DirectoryName(index, file.DirectoryIndex);
            var combined = directory.Length == 0 ? name : $"{directory}\\{name}";
            try
            {
                var path = PortableAssetPath.Relative(combined);
                return (path, directory.Length == 0 ? string.Empty : PortableAssetPath.Relative(directory), name.Replace('\\', '/'));
            }
            catch (InvalidDataException exception)
            {
                throw new InvalidDataException($"InstallShield file {index} has a path that is not portable. {exception.Message}", exception);
            }
        }

        var descriptors = new FileDescriptor[fileCount];
        for (var index = 0; index < descriptors.Length; index++)
            descriptors[index] = version5Layout
                ? ReadVersion5Descriptor(reader, table + reader.UInt32(table + 4L * (directoryCount + index)),
                    MajorVersion == 0 ? Version0DescriptorSize : Version5DescriptorSize)
                : ReadVersion6Descriptor(reader, table + descriptorsOffset + (long)index * Version6DescriptorSize);

        var skipped = new List<InstallShieldSkippedFile>();
        long expandedTotal = 0;
        for (var index = 0; index < descriptors.Length; index++)
        {
            var file = descriptors[index];
            var reason = (file.Flags & InvalidFlag) != 0 ? "The cabinet marks the file invalid."
                : file.NameOffset == 0 ? "The file has no name."
                : file.DataOffset == 0 ? "The file has no data offset."
                : null;
            if (reason is not null)
            {
                // As in Unshield, the name of an entry left out does not have to read: its path is
                // recorded when it reads and is relative, and is null otherwise.
                string? skippedPath = null;
                if (file.NameOffset != 0)
                {
                    try
                    {
                        skippedPath = EntryPath(index, file).Path;
                    }
                    catch (InvalidDataException)
                    {
                    }
                }
                skipped.Add(new(index, skippedPath, reason));
                continue;
            }

            // A listed entry's path has to read and be relative, or the open fails.
            var (path, directoryName, name) = EntryPath(index, file);

            var dataIndex = ResolveLink(descriptors, index);
            var data = descriptors[dataIndex];
            var linkReason = (data.Flags & InvalidFlag) != 0 ? $"The file links to file {dataIndex}, which the cabinet marks invalid."
                : data.DataOffset == 0 ? $"The file links to file {dataIndex}, which has no data offset."
                : null;
            if (linkReason is not null)
            {
                skipped.Add(new(index, path, linkReason));
                continue;
            }

            if (data.ExpandedSize < 0 || data.CompressedSize < 0)
                throw new InvalidDataException($"InstallShield file {index} declares a size beyond 2^63 bytes.");
            // Version 0 and 5 descriptors carry no MD5 the reader checks, so theirs is null.
            var md5 = data.Md5;
            if (members.TryGetValue(path, out var existing))
            {
                if (existing.DataIndex == dataIndex)
                {
                    skipped.Add(new(index, path,
                        $"The file shares the data of file {existing.Index} at '{existing.Entry.Path}', which is listed."));
                    existing.SharedBy.Add(new(index, file.DirectoryIndex, directoryName, name));
                    continue;
                }
                // Version 6 records each file's MD5, so two copies stored apart can be told to be the
                // same file. The copy listed is the one checked when read; the other is not read, and
                // neither are its volumes, so a duplicate in a missing or damaged volume is still skipped.
                if (existing.Md5 is not null && md5 is not null && existing.Entry.Size == data.ExpandedSize &&
                    existing.Md5.AsSpan().SequenceEqual(md5))
                {
                    skipped.Add(new(index, path,
                        $"The file duplicates file {existing.Index} at '{existing.Entry.Path}': same expanded size and MD5."));
                    existing.SharedBy.Add(new(index, file.DirectoryIndex, directoryName, name));
                    continue;
                }
                throw new InvalidDataException(
                    $"InstallShield cabinet holds two different files at '{path}' (file {existing.DataIndex} and file {dataIndex}).");
            }
            members.Add(path, new Member(
                new ContentSourceEntry(path, data.ExpandedSize), index, dataIndex, (data.Flags & CompressedFlag) != 0,
                (data.Flags & ObfuscatedFlag) != 0, md5, Segments(dataIndex, data, descriptors.Length),
                new(index, file.DirectoryIndex, directoryName, name), []));
            // Compared this way round, the total cannot overflow even when the limit is near long.MaxValue.
            if (data.ExpandedSize > limits.MaximumExpandedBytes - expandedTotal)
                throw new InvalidDataException(
                    $"InstallShield cabinet expands to more than the limit of {limits.MaximumExpandedBytes} bytes.");
            expandedTotal += data.ExpandedSize;
        }

        SkippedFiles = skipped;
        var listed = members.Values.OrderBy(member => member.Entry.Path, StringComparer.OrdinalIgnoreCase).ToArray();
        Files = listed.Select(member => member.Entry).ToArray();

        // The file groups are read last, from the header already open, so a set that opens without
        // them opens the same way with them: what does not read is reported, never thrown.
        var groupTable = InstallShieldFileGroupTable.Read(reader, descriptor, MajorVersion, descriptors.Length, limits);
        var memberships = groupTable.Memberships(listed
            .SelectMany(member => member.SharedBy.Prepend(member.Listed)).Select(entry => entry.Index)
            .Order().ToArray());
        FileGroups = groupTable.Groups;
        FileGroupProblem = groupTable.Problem;
        InstallShieldEntryMetadata Metadata(EntryName entry) => new(
            entry.Index, entry.DirectoryIndex, entry.Directory, entry.Name, memberships[entry.Index]);
        foreach (var member in listed)
            member.Metadata = new(member.Entry, Metadata(member.Listed), member.SharedBy.Select(Metadata).ToArray());
        Members = listed.Select(member => member.Metadata!).ToArray();
    }

    /// <inheritdoc />
    public override string Kind => ContentSourceKinds.InstallShieldCabinet;
    /// <summary>Always <see langword="null"/>: a cabinet set has no volume identifier.</summary>
    public override string? Label => null;
    /// <inheritdoc />
    public override IReadOnlyList<ContentSourceEntry> Files { get; }
    /// <summary>The InstallShield major version the header's version word gives: 0, 5 or 6.</summary>
    public int MajorVersion { get; }
    /// <summary>
    /// File-table entries that are not listed, in table order: entries the cabinet marks invalid,
    /// entries with no name or no data offset (their name and directory are not checked, and the
    /// path is <see langword="null"/> when they do not read), version 6 entries whose link ends at an
    /// entry the cabinet marks invalid or that has no data offset, entries that share a listed member's data
    /// at the same path, and version 6 entries stored apart from a listed member at the same path
    /// with the same expanded size and MD5. Each reason names the entry it refers to.
    /// </summary>
    public IReadOnlyList<InstallShieldSkippedFile> SkippedFiles { get; }

    /// <summary>
    /// The listed members with the file-table entries they are listed from, in the order of
    /// <see cref="Files"/>. Each gives its entry's index, directory, name and file groups, and the
    /// unlisted entries at its path that hold the same data. File groups do not change
    /// <see cref="Files"/> or <see cref="OpenRead"/>: a member's path is its directory and name joined.
    /// </summary>
    public IReadOnlyList<InstallShieldMember> Members { get; }

    /// <summary>
    /// The file groups the cabinet descriptor lists, in the order Unshield reads them: its 71 lists in
    /// turn, each from its head. Empty when the descriptor lists none. A group whose descriptor, name or
    /// range does not read or lies outside the file table is still listed, with its
    /// <see cref="InstallShieldFileGroup.Problem"/>.
    /// </summary>
    public IReadOnlyList<InstallShieldFileGroup> FileGroups { get; }

    /// <summary>
    /// Why the file groups were not read whole, or <see langword="null"/> when they were: a list entry
    /// that does not read, a list that returns to an entry already read, more groups than
    /// <see cref="InstallShieldCabinetLimits.MaximumFiles"/>, or ranges that together name more
    /// (entry, group) pairs than that limit. <see cref="FileGroups"/> then holds the groups read
    /// before the problem, and every entry's membership is
    /// <see cref="InstallShieldFileGroupMembershipKind.Undetermined"/>. The open does not fail on it,
    /// since members are read without their groups.
    /// </summary>
    public string? FileGroupProblem { get; }

    /// <summary>Finds a listed member by path, ignoring case, as <see cref="TryGetFile"/> does.</summary>
    /// <exception cref="InvalidDataException"><paramref name="relativePath"/> is not accepted by <see cref="PortableAssetPath.Relative"/>.</exception>
    public bool TryGetMember(string relativePath, out InstallShieldMember? member)
    {
        if (members.TryGetValue(PortableAssetPath.Relative(relativePath), out var found))
        {
            member = found.Metadata;
            return true;
        }
        member = null;
        return false;
    }

    /// <inheritdoc />
    public override bool TryGetFile(string relativePath, out ContentSourceEntry? entry)
    {
        if (members.TryGetValue(PortableAssetPath.Relative(relativePath), out var member))
        {
            entry = member.Entry;
            return true;
        }
        entry = null;
        return false;
    }

    /// <summary>
    /// Opens a member as a read-only seekable stream that decodes it while reading. Seeking forward
    /// decodes the bytes skipped; seeking backward decodes again from the member's start. Reading the
    /// member to its end checks it as the class remarks describe.
    /// </summary>
    /// <exception cref="FileNotFoundException">The set has no such member, or a volume is gone.</exception>
    /// <exception cref="InvalidDataException"><paramref name="relativePath"/> is not accepted by <see cref="PortableAssetPath.Relative"/>.</exception>
    public override Stream OpenRead(string relativePath)
    {
        if (!members.TryGetValue(PortableAssetPath.Relative(relativePath), out var member))
            throw new FileNotFoundException("InstallShield cabinet has no such file.", relativePath);
        return new InstallShieldMemberStream(
            member.Entry.Path, member.Entry.Size, member.Compressed, member.Obfuscated, member.Md5,
            member.Segments, openVolume);
    }

    /// <inheritdoc />
    public override void Dispose() { }

    // The two encodings Unshield reads: a top byte of 1 keeps the major version in bits 12 to 15,
    // and a top byte of 2 or 4 keeps a hundredfold version in the low word. Both give 0 for some
    // words. Any other top byte gives null.
    internal static int? ReadMajorVersion(uint version) => (version >> 24) switch
    {
        1 => (int)((version >> 12) & 0xf),
        2 or 4 => (int)(version & 0xffff) / 100,
        _ => null
    };

    private static FileDescriptor ReadVersion5Descriptor(InstallShieldHeaderReader reader, long offset, int size)
    {
        reader.Require(offset, size, "file descriptor");
        return new(
            Flags: reader.UInt16(offset + 8),
            ExpandedSize: reader.UInt32(offset + 10),
            CompressedSize: reader.UInt32(offset + 14),
            DataOffset: reader.UInt32(offset + 0x26),
            Md5: null,
            NameOffset: reader.UInt32(offset),
            DirectoryIndex: reader.UInt16(offset + 4),
            LinkPrevious: 0,
            LinkFlags: 0,
            Volume: 0);
    }

    private static FileDescriptor ReadVersion6Descriptor(InstallShieldHeaderReader reader, long offset)
    {
        reader.Require(offset, Version6DescriptorSize, "file descriptor");
        return new(
            Flags: reader.UInt16(offset),
            ExpandedSize: (long)reader.UInt64(offset + 2),
            CompressedSize: (long)reader.UInt64(offset + 0x0a),
            DataOffset: (long)reader.UInt64(offset + 0x12),
            Md5: reader.Bytes(offset + 0x1a, 16),
            NameOffset: reader.UInt32(offset + 0x3a),
            DirectoryIndex: reader.UInt16(offset + 0x3e),
            LinkPrevious: reader.UInt32(offset + 0x4c),
            LinkFlags: reader.Byte(offset + 0x54),
            Volume: reader.UInt16(offset + 0x55));
    }

    // A version 6 entry can link to an earlier entry whose data it shares. Returns the index of the
    // entry the chain ends at; a link outside the table or a cycle means the header is damaged.
    private static int ResolveLink(FileDescriptor[] descriptors, int index)
    {
        var current = index;
        for (var step = 0; step <= descriptors.Length; step++)
        {
            var descriptor = descriptors[current];
            if ((descriptor.LinkFlags & LinkPreviousFlag) == 0) return current;
            if (descriptor.LinkPrevious >= descriptors.Length || descriptor.LinkPrevious == current)
                throw new InvalidDataException($"InstallShield file {current} links to file {descriptor.LinkPrevious}, which does not exist.");
            current = (int)descriptor.LinkPrevious;
        }
        throw new InvalidDataException($"InstallShield file {index} is part of a link cycle.");
    }

    // Where a member's stored bytes lie, following the volume headers as Unshield does.
    private InstallShieldSegment[] Segments(int index, FileDescriptor file, int fileCount)
    {
        var compressed = (file.Flags & CompressedFlag) != 0;
        var remaining = compressed ? file.CompressedSize : file.ExpandedSize;
        var volume = version5Layout ? 1 : file.Volume;
        var header = Volume(volume, index);
        var split = (file.Flags & SplitFlag) != 0;
        if (version5Layout)
        {
            // Version 0 and 5 descriptors do not name a volume: the member is in the first volume
            // whose last file index reaches it.
            while (index > header.LastIndex) header = Volume(++volume, index);
            // A version 5 member is also split when that volume's record of it disagrees. Unshield
            // infers this for version 5 only, so a version 0 member is split only when its flag says so.
            if (MajorVersion == 5)
                split |= (index < fileCount - 1 && index == header.LastIndex && header.LastCompressed != file.CompressedSize)
                         || (index > 0 && index == header.FirstIndex && header.FirstCompressed != file.CompressedSize);
        }

        var segments = new List<InstallShieldSegment>();
        while (true)
        {
            long offset, length;
            if (!split)
                (offset, length) = (file.DataOffset, remaining);
            else if (index == header.LastIndex && header.LastOffset != 0x7fffffff)
                (offset, length) = (header.LastOffset, compressed ? header.LastCompressed : header.LastExpanded);
            else if (index == header.FirstIndex)
                (offset, length) = (header.FirstOffset, compressed ? header.FirstCompressed : header.FirstExpanded);
            else
                throw new InvalidDataException($"InstallShield file {index} is split but volume {volume} does not record it.");

            var take = Math.Min(length, remaining);
            if (take <= 0 && remaining > 0)
                throw new InvalidDataException($"InstallShield volume {volume} holds none of file {index}'s data.");
            if (offset < 0 || offset > header.Length - take)
                throw new InvalidDataException(
                    $"InstallShield file {index} lies past the end of volume {volume}, which is shorter than the header claims.");
            if (take > 0) segments.Add(new(volume, offset, take));
            remaining -= take;
            if (remaining == 0) return [.. segments];
            if (!split)
                throw new InvalidDataException($"InstallShield file {index} is not split but its data continues past volume {volume}.");
            header = Volume(++volume, index);
        }
    }

    private VolumeHeader Volume(int volume, int index)
    {
        if (volume is < 1 or > MaximumVolume)
            throw new InvalidDataException($"InstallShield file {index} names volume {volume}, which is out of range.");
        if (volumes.TryGetValue(volume, out var cached)) return cached;
        using var stream = openVolume(volume);
        var size = version5Layout ? 40 : 64;
        var bytes = new byte[CommonHeaderSize + size];
        try
        {
            stream.ReadExactly(bytes);
        }
        catch (EndOfStreamException exception)
        {
            throw new InvalidDataException($"InstallShield volume {volume} is too short for its header.", exception);
        }
        if (BinaryPrimitives.ReadUInt32LittleEndian(bytes) != Signature)
            throw new InvalidDataException($"InstallShield volume {volume} is not an InstallShield cabinet.");

        uint U32(int at) => BinaryPrimitives.ReadUInt32LittleEndian(bytes.AsSpan(CommonHeaderSize + at));
        long U64(int at)
        {
            var value = (long)BinaryPrimitives.ReadUInt64LittleEndian(bytes.AsSpan(CommonHeaderSize + at));
            return value >= 0 ? value
                : throw new InvalidDataException($"InstallShield volume {volume} header holds a value beyond 2^63.");
        }
        var header = version5Layout
            ? new VolumeHeader(stream.Length, U32(8), U32(12), U32(16), U32(20), U32(24),
                U32(28) == 0 ? 0x7fffffff : U32(28), U32(32), U32(36))
            : new VolumeHeader(stream.Length, U32(8), U32(12), U64(16), U64(24), U64(32), U64(40), U64(48), U64(56));
        volumes.Add(volume, header);
        return header;
    }

    private sealed record FileDescriptor(
        ushort Flags, long ExpandedSize, long CompressedSize, long DataOffset, byte[]? Md5,
        uint NameOffset, ushort DirectoryIndex, uint LinkPrevious, byte LinkFlags, ushort Volume);

    private sealed record VolumeHeader(
        long Length, uint FirstIndex, uint LastIndex, long FirstOffset, long FirstExpanded, long FirstCompressed,
        long LastOffset, long LastExpanded, long LastCompressed);

    private sealed record Member(
        ContentSourceEntry Entry, int Index, int DataIndex, bool Compressed, bool Obfuscated, byte[]? Md5,
        InstallShieldSegment[] Segments, EntryName Listed, List<EntryName> SharedBy)
    {
        public InstallShieldMember? Metadata { get; set; }
    }

    private sealed record EntryName(int Index, int DirectoryIndex, string Directory, string Name);
}

internal sealed record InstallShieldSegment(int Volume, long Offset, long Length);

// The header bytes a source is opened from. A .hdr is read whole. A .cab that holds the header is
// also volume 1, and its header structures (cabinet descriptor, file table, file descriptors, names)
// can lie anywhere before its member data: Unshield reads the whole file and does not bound them by
// the cabinet descriptor's size. So a .cab is read forward from its start only as far as the
// structures the source asks for reach, and never past the header limit.
internal sealed class InstallShieldHeaderReader : IDisposable
{
    private const int MinimumRead = 4096;
    private readonly Stream? stream;
    private readonly long length;
    private readonly int limit;
    private readonly int maximumNameBytes;
    private readonly string description;
    private byte[] data;
    private int filled;

    private InstallShieldHeaderReader(
        Stream? stream, byte[] data, long length, int limit, int maximumNameBytes, string description)
    {
        this.stream = stream;
        this.data = data;
        filled = stream is null ? data.Length : 0;
        this.length = length;
        this.limit = limit;
        this.maximumNameBytes = maximumNameBytes;
        this.description = description;
    }

    // A .hdr file, read whole by the caller.
    public static InstallShieldHeaderReader Whole(byte[] data, string fileName, int maximumNameBytes) =>
        new(null, data, data.Length, data.Length, maximumNameBytes, $"header file '{fileName}'");

    // A .cab that holds the header. The reader owns the stream and reads it forward on demand.
    public static InstallShieldHeaderReader Forward(Stream stream, string fileName, InstallShieldCabinetLimits limits) =>
        new(stream, [], stream.Length, limits.MaximumHeaderBytes, limits.MaximumNameBytes, $"cabinet '{fileName}'");

    public void Dispose() => stream?.Dispose();

    public void Require(long offset, long count, string what)
    {
        if (offset < 0 || count < 0 || offset > length - count)
            throw new InvalidDataException(
                $"InstallShield header is truncated: its {what} lies past the end of the {length}-byte {description}.");
        var end = offset + count;
        if (end > limit)
            throw new InvalidDataException(
                $"InstallShield header's {what} ends {end} bytes into {description}, past the header limit of {limit} bytes.");
        Load(end);
    }

    public byte Byte(long offset)
    {
        Require(offset, 1, "data");
        return data[offset];
    }

    public ushort UInt16(long offset)
    {
        Require(offset, 2, "data");
        return BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan((int)offset));
    }

    public uint UInt32(long offset)
    {
        Require(offset, 4, "data");
        return BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan((int)offset));
    }

    public ulong UInt64(long offset)
    {
        Require(offset, 8, "data");
        return BinaryPrimitives.ReadUInt64LittleEndian(data.AsSpan((int)offset));
    }

    public byte[] Bytes(long offset, int count)
    {
        Require(offset, count, "data");
        return data.AsSpan((int)offset, count).ToArray();
    }

    // Names are NUL-terminated single-byte strings, read as ISO 8859-1. The terminator is looked
    // for only within the name limit, so no name costs more than that to read.
    public string String(long offset, string what)
    {
        Require(offset, 1, what);
        var window = (int)Math.Min((long)maximumNameBytes + 1, length - offset);
        // Within the header limit; a name that would need bytes past it fails on the limit below.
        var searched = (int)Math.Min(window, limit - offset);
        Load(offset + searched);
        var end = Array.IndexOf(data, (byte)0, (int)offset, searched);
        if (end >= 0) return Encoding.Latin1.GetString(data, (int)offset, end - (int)offset);
        if (searched < window) Require(offset, window, what);
        if (window <= maximumNameBytes)
            throw new InvalidDataException($"InstallShield header is truncated: a {what} has no terminator.");
        throw new InvalidDataException(
            $"InstallShield header has a {what} longer than the limit of {maximumNameBytes} bytes.");
    }

    // end is at most the smaller of the file's length and the header limit, so it fits an int. The
    // buffer grows by doubling, but each read stops MinimumRead bytes past end at most, so little of
    // the member data after the header is read.
    private void Load(long end)
    {
        if (end <= filled) return;
        var available = Math.Min(length, limit);
        var size = (int)Math.Min(Math.Max(end, (long)filled + MinimumRead), available);
        if (size > data.Length) Array.Resize(ref data, (int)Math.Min(Math.Max(size, 2L * data.Length), available));
        try
        {
            stream!.ReadExactly(data.AsSpan(filled, size - filled));
        }
        catch (EndOfStreamException exception)
        {
            throw new InvalidDataException($"InstallShield {description} ended before its declared length.", exception);
        }
        filled = size;
    }
}

internal static class InstallShieldCabinetOpener
{
    public static InstallShieldCabinetSource FromDirectory(string path, InstallShieldCabinetLimits limits)
    {
        var fullPath = Path.GetFullPath(path);
        if (!File.Exists(fullPath)) throw new FileNotFoundException("InstallShield cabinet header does not exist.", fullPath);
        var directory = Path.GetDirectoryName(fullPath)!;
        var fileName = Path.GetFileName(fullPath);
        var prefix = VolumePrefix(fileName);
        using var header = OpenHeader(new FileStream(fullPath, FileMode.Open, FileAccess.Read, FileShare.Read), fileName, limits);
        // Each volume's file is looked up once, not on every member read.
        var found = new ConcurrentDictionary<int, string>();
        return new InstallShieldCabinetSource(header, volume =>
        {
            if (!found.TryGetValue(volume, out var file))
            {
                var name = $"{prefix}{volume}.cab";
                var matches = Directory.EnumerateFiles(directory)
                    .Where(candidate => Path.GetFileName(candidate).Equals(name, StringComparison.OrdinalIgnoreCase)).ToArray();
                file = matches.Length switch
                {
                    1 => found.GetOrAdd(volume, matches[0]),
                    0 => throw new FileNotFoundException($"InstallShield volume {volume} was not found.", Path.Combine(directory, name)),
                    _ => throw new InvalidDataException($"Several files match InstallShield volume name '{name}'.")
                };
            }
            return new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.Read);
        }, limits);
    }

    public static InstallShieldCabinetSource FromSource(OriginalContentSource container, string headerPath, InstallShieldCabinetLimits limits)
    {
        var relative = PortableAssetPath.Relative(headerPath);
        var separator = relative.LastIndexOf('/');
        var directory = separator < 0 ? string.Empty : relative[..(separator + 1)];
        var fileName = relative[(separator + 1)..];
        var prefix = VolumePrefix(fileName);
        if (!container.TryGetFile(relative, out _))
            throw new FileNotFoundException("InstallShield cabinet header was not found in the source.", relative);
        using var header = OpenHeader(container.OpenRead(relative), fileName, limits);
        return new InstallShieldCabinetSource(header, volume =>
        {
            var name = $"{directory}{prefix}{volume}.cab";
            return container.TryGetFile(name, out _)
                ? container.OpenRead(name)
                : throw new FileNotFoundException($"InstallShield volume {volume} was not found in the source.", name);
        }, limits);
    }

    // As Unshield does, the volume names keep the header's name up to its first dot or digit:
    // data1.hdr gives data1.cab, data2.cab and so on.
    private static string VolumePrefix(string fileName)
    {
        var end = fileName.IndexOfAny(['.', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9']);
        var prefix = end < 0 ? fileName : fileName[..end];
        return prefix.Length > 0 ? prefix : throw new InvalidDataException($"InstallShield header name '{fileName}' has no volume prefix.");
    }

    // A .hdr file is read whole. A .cab that holds the header is also volume 1, so it is read forward
    // only as far as the header's structures reach (see InstallShieldHeaderReader).
    private static InstallShieldHeaderReader OpenHeader(Stream stream, string fileName, InstallShieldCabinetLimits limits)
    {
        if (Path.GetExtension(fileName).Equals(".cab", StringComparison.OrdinalIgnoreCase))
        {
            try
            {
                return InstallShieldHeaderReader.Forward(stream, fileName, limits);
            }
            catch
            {
                stream.Dispose();
                throw;
            }
        }
        using (stream)
        {
            var length = stream.Length;
            if (length > limits.MaximumHeaderBytes)
                throw new InvalidDataException(
                    $"InstallShield header is {length} bytes, more than the limit of {limits.MaximumHeaderBytes}.");
            var bytes = new byte[length];
            stream.ReadExactly(bytes);
            return InstallShieldHeaderReader.Whole(bytes, fileName, limits.MaximumNameBytes);
        }
    }
}
