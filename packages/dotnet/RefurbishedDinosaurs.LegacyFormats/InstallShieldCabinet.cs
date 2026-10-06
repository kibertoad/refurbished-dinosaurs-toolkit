using System.Buffers.Binary;
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
/// How the compressed members of an InstallShield cabinet set store their deflate data. The header
/// records no field that says which one a set uses, so the caller chooses it when opening the set,
/// and every compressed member of the set is read that way. A member that does not decode in the
/// chosen form fails its read; the reader never tries it in the other form.
/// </summary>
public enum InstallShieldCompressedFormat
{
    /// <summary>
    /// A run of chunks, each a 16-bit little-endian length and that many bytes of raw deflate data
    /// that expands to at most 64 KiB. This is what Unshield reads by default, and the default here.
    /// </summary>
    LengthPrefixedChunks = 0,

    /// <summary>
    /// One raw deflate stream with no chunk lengths, flushed to a byte boundary after each chunk, so
    /// each chunk ends with the empty stored block <c>00 00 FF FF</c>. This is what Unshield reads with
    /// <c>-O</c> ("old compression"). The stored bytes are decoded as one deflate stream, so a
    /// <c>00 00 FF FF</c> that occurs inside a chunk's data is read as data, and chunks are not
    /// required to expand to 64 KiB or less.
    /// </summary>
    MarkerDelimitedChunks = 1,
}

/// <summary>Why an <see cref="InstallShieldSkippedFile"/> is not listed.</summary>
public enum InstallShieldSkippedFileKind
{
    /// <summary>The cabinet or archive marks the entry invalid.</summary>
    MarkedInvalid,

    /// <summary>The entry has no name.</summary>
    NoName,

    /// <summary>The entry has no data offset.</summary>
    NoDataOffset,

    /// <summary>
    /// A version 6 entry whose link chain ends at an entry the cabinet marks invalid or that has no
    /// data offset.
    /// </summary>
    LinksToUnavailableEntry,

    /// <summary>The entry shares the data of the listed member at its path through a link.</summary>
    SharesListedData,

    /// <summary>
    /// A version 6 entry stored apart from the listed member at its path, with the same expanded size
    /// and MD5.
    /// </summary>
    DuplicatesListedMember,

    /// <summary>
    /// The entry's data is stored outside the cabinet's volumes: the entry has stored bytes and its data
    /// offset is the exact length of the volume where its data starts, split or not, which is how
    /// Unshield recognizes such a file. The cabinet source looks for the file beside the header and
    /// reports what it found in its <c>OutsideFiles</c>; the entry stays unlisted either way. Entries
    /// that link to its data, and version 6 copies of it at its path that are also stored outside,
    /// have this kind too. A volume cut short exactly where its last member's data starts reads the
    /// same way, so the reason gives the offset and the volume.
    /// </summary>
    StoredOutsideCabinet,

    /// <summary>
    /// The entry's path, ignoring case, holds two or more files that the source cannot show to be the
    /// same, so the path lists none of them. The reason names the first entry of each file. The
    /// source's <c>PathConflicts</c> gives each file with its metadata, and <c>OpenEntry</c> reads it by
    /// the entry's index. An entry stored outside the cabinet at such a path keeps
    /// <see cref="StoredOutsideCabinet"/>.
    /// </summary>
    PathHeldByDifferentFiles,
}

/// <summary>
/// A file-table entry an <see cref="InstallShieldCabinetSource"/> or an
/// <see cref="InstallShieldArchiveSource"/> does not list.
/// </summary>
/// <param name="Index">The entry's index in the cabinet's or archive's file table.</param>
/// <param name="Path">
/// The entry's path, when it has a name that reads and <see cref="PortableAssetPath.Relative"/> accepts; otherwise <see langword="null"/>.
/// </param>
/// <param name="Kind">Why the entry is not listed, as a value a caller can test.</param>
/// <param name="Reason">Why the entry is not listed, naming any other entry the reason refers to.</param>
public sealed record InstallShieldSkippedFile(int Index, string? Path, InstallShieldSkippedFileKind Kind, string Reason);

/// <summary>
/// The files of an InstallShield cabinet set of major version 0, 5 or 6 (<c>dataN.hdr</c> with
/// <c>dataN.cab</c> volumes) as an <see cref="OriginalContentSource"/>. Open it with
/// <see cref="OriginalContentSource.OpenInstallShieldCabinet(string, InstallShieldCabinetLimits?, InstallShieldCompressedFormat)"/>
/// or, for a set inside another source such as a disc image,
/// <see cref="OriginalContentSource.OpenInstallShieldCabinet(OriginalContentSource, string, InstallShieldCabinetLimits?, InstallShieldCompressedFormat)"/>.
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
/// without its stored bytes being read. Any other pair of entries at one path, and every such pair
/// in a version 5 set, which records no MD5, holds different files. Such a path lists none of them:
/// <see cref="PathConflicts"/> gives each file with its metadata, every entry at the path goes to
/// <see cref="SkippedFiles"/> as <see cref="InstallShieldSkippedFileKind.PathHeldByDifferentFiles"/>,
/// and <see cref="OpenEntry"/> reads each file by an entry's index. The reader never picks one of
/// them for the path.
/// </para>
/// <para>
/// An entry whose data lies outside the cabinet's volumes is not listed: it goes to
/// <see cref="SkippedFiles"/> as <see cref="InstallShieldSkippedFileKind.StoredOutsideCabinet"/>,
/// and the rest of the set opens. As Unshield tells it, such an entry's data offset is exactly the
/// length of the volume where its data starts, whether or not the entry is split. An extent that starts past that
/// length, or that starts inside the volume and runs past its end, is damage and fails the open.
/// </para>
/// <para>
/// Each file stored outside the cabinet is looked for where Unshield's <c>-O</c> looks: beside the
/// header, at the directory and name of the entry that holds its data, matched ignoring case. The
/// source reports what it found in <see cref="OutsideFiles"/>. Only a file found exactly once, with
/// exactly the entry's stored length, at a path that holds no other file, is read, by
/// <see cref="OpenEntry"/>. Nothing else is searched for, and <see cref="Files"/> never lists a file
/// stored outside.
/// </para>
/// <para>
/// Compressed members are decoded in the <see cref="InstallShieldCompressedFormat"/> chosen when the
/// set is opened (<see cref="CompressedFormat"/>), since the header does not say which one the set
/// uses. A member that does not decode in that form fails its read; it is never tried in the other.
/// </para>
/// <para>
/// Reading a member to its end checks that its data expands to exactly the declared size and, for
/// a version 6 set, that the expanded bytes match the MD5 the header records. A failed check throws
/// <see cref="InvalidDataException"/> from the read that would have returned the member's last bytes.
/// Version 5 members are checked by size only. A member read as
/// <see cref="InstallShieldCompressedFormat.MarkerDelimitedChunks"/> is also checked to end with the
/// <c>00 00 FF FF</c> marker, with the decoder reaching the end of its stored bytes and no block final.
/// </para>
/// <para>
/// <see cref="Members"/> gives each listed member's file-table index, directory and name, and the
/// file groups whose ranges hold it, read from the header the open has already read. A member's path
/// stays its directory and name joined; an installer that places files by group does so outside the
/// cabinet, so a caller that needs installed paths builds them from this metadata. A group name is
/// reported as data and is not checked as a path. The groups never fail the open: a list that does not
/// read, a malformed group and an entry held by several groups are each reported, with
/// <see cref="FileGroupProblem"/>, <see cref="InstallShieldFileGroup.Problem"/> and
/// <see cref="InstallShieldFileGroupMembership.Kind"/>. An I/O error from the stream a header-holding
/// <c>.cab</c> is read from still fails the open, as it does for the rest of the header.
/// </para>
/// </remarks>
public sealed class InstallShieldCabinetSource : OriginalContentSource
{
    // The layout (common header, cabinet descriptor, file table, volume headers, both forms of
    // compressed data, members stored outside the cabinet and the obfuscation) follows Unshield's
    // reading of the format: https://github.com/twogood/unshield.
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
    private readonly InstallShieldOutsideStore outside;
    private readonly Dictionary<string, Member> members = new(StringComparer.OrdinalIgnoreCase);
    // The file each entry's index reads: a listed member's entries and a contested path's files inside the cabinet.
    private readonly Dictionary<int, Member> readable = [];
    private readonly HashSet<string> contested = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<int, VolumeHeader> volumes = [];
    // Major versions 0 and 5 share the file descriptor and volume header layout.
    private readonly bool version5Layout;

    // limits and compressedFormat have been validated by the caller.
    internal InstallShieldCabinetSource(
        InstallShieldHeaderReader reader, Func<int, Stream> openVolume, InstallShieldOutsideStore outside,
        InstallShieldCabinetLimits limits, InstallShieldCompressedFormat compressedFormat)
    {
        this.openVolume = openVolume;
        this.outside = outside;
        CompressedFormat = compressedFormat;
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
        // Each directory's name with / separators, checked once, when a path in it is first accepted.
        var portableDirectories = new string?[directoryCount];

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
                var portable = portableDirectories[file.DirectoryIndex] ??=
                    directory.Length == 0 ? string.Empty : PortableAssetPath.Relative(directory);
                return (path, portable, name.Replace('\\', '/'));
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

        string? LinkedPath(int index, FileDescriptor file)
        {
            if (file.NameOffset == 0) return null;
            try
            {
                return EntryPath(index, file).Path;
            }
            catch (InvalidDataException)
            {
                return null;
            }
        }

        var skipped = new List<InstallShieldSkippedFile>();
        // The different files at each path, in the order their first entries come in the table. A
        // path that ends up with one file lists it, or skips it when it is stored outside; a path
        // with more lists none of them, and each one held inside the cabinet is read by index.
        var byPath = new Dictionary<string, List<Member>>(StringComparer.OrdinalIgnoreCase);
        long expandedTotal = 0;
        for (var index = 0; index < descriptors.Length; index++)
        {
            var file = descriptors[index];
            var (kind, reason) = (file.Flags & InvalidFlag) != 0
                ? (InstallShieldSkippedFileKind.MarkedInvalid, "The cabinet marks the file invalid.")
                : file.NameOffset == 0 ? (InstallShieldSkippedFileKind.NoName, "The file has no name.")
                : file.DataOffset == 0 ? (InstallShieldSkippedFileKind.NoDataOffset, "The file has no data offset.")
                : (default, (string?)null);
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
                skipped.Add(new(index, skippedPath, kind, reason));
                continue;
            }

            // Any other entry's path has to read and be relative, or the open fails.
            var (path, directoryName, name) = EntryPath(index, file);

            var dataIndex = ResolveLink(descriptors, index);
            var data = descriptors[dataIndex];
            var linkReason = (data.Flags & InvalidFlag) != 0 ? $"The file links to file {dataIndex}, which the cabinet marks invalid."
                : data.DataOffset == 0 ? $"The file links to file {dataIndex}, which has no data offset."
                : null;
            if (linkReason is not null)
            {
                skipped.Add(new(index, path, InstallShieldSkippedFileKind.LinksToUnavailableEntry, linkReason));
                continue;
            }

            if (data.ExpandedSize < 0 || data.CompressedSize < 0)
                throw new InvalidDataException($"InstallShield file {index} declares a size beyond 2^63 bytes.");
            // Version 0 and 5 descriptors carry no MD5 the reader checks, so theirs is null.
            var md5 = data.Md5;
            var entryName = new EntryName(index, file.DirectoryIndex, directoryName, name);
            if (!byPath.TryGetValue(path, out var files)) byPath.Add(path, files = []);

            var linked = files.Find(existing => existing.DataIndex == dataIndex);
            if (linked is not null)
            {
                linked.SharedBy.Add(entryName);
                linked.Skipped.Add(linked.Segments is null
                    ? new(new(index, path, InstallShieldSkippedFileKind.StoredOutsideCabinet,
                        $"The file shares the data of file {linked.Index} at '{linked.Entry.Path}', which is stored outside the cabinet."), null)
                    : new(new(index, path, InstallShieldSkippedFileKind.SharesListedData,
                        $"The file shares the data of file {linked.Index} at '{linked.Entry.Path}', which is listed."),
                        $"The file shares the data of file {linked.Index} at '{linked.Entry.Path}'."));
                continue;
            }
            // Version 6 records each file's MD5, so two copies stored apart can be told to be the
            // same file. The copy kept is the one checked when read; the other is not read, and
            // neither are its volumes, so a duplicate in a missing or damaged volume is still skipped.
            var same = files.Find(existing => existing.Md5 is not null && md5 is not null &&
                existing.Entry.Size == data.ExpandedSize && existing.Md5.AsSpan().SequenceEqual(md5));
            if (same is { Segments: not null })
            {
                var duplicate = $"The file duplicates file {same.Index} at '{same.Entry.Path}': same expanded size and MD5.";
                same.SharedBy.Add(entryName);
                same.Skipped.Add(new(new(index, path, InstallShieldSkippedFileKind.DuplicatesListedMember, duplicate), duplicate));
                continue;
            }

            var segments = Segments(dataIndex, data, descriptors.Length, out var outsideVolume);
            var member = new Member(
                new ContentSourceEntry(path, data.ExpandedSize), index, dataIndex, (data.Flags & CompressedFlag) != 0,
                (data.Flags & ObfuscatedFlag) != 0, md5, segments, entryName)
            {
                StoredSize = (data.Flags & CompressedFlag) != 0 ? data.CompressedSize : data.ExpandedSize
            };
            if (segments is null)
            {
                // Unshield looks for a file stored outside by the directory and name of the entry that
                // holds its data, which for a link is the entry the link ends at.
                member.LookupPath = dataIndex == index ? path : LinkedPath(dataIndex, data);
                var evidence = $"its data offset, {data.DataOffset}, is the length of volume {outsideVolume}, which would hold it";
                var record = new InstallShieldSkippedFile(index, path, InstallShieldSkippedFileKind.StoredOutsideCabinet,
                    same is not null
                        ? $"The file duplicates file {same.Index} at '{same.Entry.Path}' (same expanded size and MD5), and is also stored outside the cabinet: {evidence}."
                        : dataIndex == index
                            ? $"The file is stored outside the cabinet: {evidence}."
                            : $"The file links to file {dataIndex}, which is stored outside the cabinet: {evidence}.");
                if (same is not null) same.SharedBy.Add(entryName);
                else files.Add(member);
                (same ?? member).Skipped.Add(new(record, null));
                continue;
            }
            if (same is not null)
            {
                // A version 6 copy of a file stored outside is read when its own data is inside. The
                // entries stored outside hold the same file, so it takes their place and shares them,
                // as it would had they come after it in the table.
                member.SharedBy.AddRange(same.SharedBy.Prepend(same.Listed));
                member.Skipped.AddRange(same.Skipped);
                files[files.IndexOf(same)] = member;
            }
            else files.Add(member);
            // Compared this way round, the total cannot overflow even when the limit is near long.MaxValue.
            if (data.ExpandedSize > limits.MaximumExpandedBytes - expandedTotal)
                throw new InvalidDataException(
                    $"InstallShield cabinet expands to more than the limit of {limits.MaximumExpandedBytes} bytes.");
            expandedTotal += data.ExpandedSize;
        }

        var conflicts = new List<(string Path, List<Member> Files)>();
        foreach (var (path, files) in byPath)
        {
            if (files.Count == 1)
            {
                var only = files[0];
                skipped.AddRange(only.Skipped.Select(entry => entry.Record));
                if (only.Segments is not null) members.Add(path, only);
                continue;
            }
            conflicts.Add((path, files));
            // A version 6 copy inside the cabinet that took the place of entries stored outside is read
            // from its own entry, but the file's first entry is the first of those.
            var held = InstallShieldPathConflict.HeldReason(
                files.Select(file => file.SharedBy.Prepend(file.Listed).Min(entry => entry.Index)));
            foreach (var file in files)
            {
                if (file.Segments is not null)
                    skipped.Add(new(file.Index, file.Entry.Path, InstallShieldSkippedFileKind.PathHeldByDifferentFiles, held));
                // Entries stored outside keep that kind, since it is why they are not read.
                skipped.AddRange(file.Skipped.Select(entry => entry.Contested is null ? entry.Record
                    : entry.Record with { Kind = InstallShieldSkippedFileKind.PathHeldByDifferentFiles, Reason = $"{held} {entry.Contested}" }));
            }
        }

        SkippedFiles = skipped.OrderBy(file => file.Index).ToArray();
        var listed = members.Values.OrderBy(member => member.Entry.Path, StringComparer.OrdinalIgnoreCase).ToArray();
        Files = listed.Select(member => member.Entry).ToArray();
        conflicts.Sort((left, right) => StringComparer.OrdinalIgnoreCase.Compare(left.Path, right.Path));

        // Each file stored outside is looked up beside the header, at the path of the entry that holds
        // its data. Only one found there with exactly its stored length is read. At a path that holds
        // other files too, the file found cannot be told to be this one, so it is not read.
        var storedOutside = byPath.Values
            .SelectMany(files => files.Where(file => file.Segments is null).Select(file => (File: file, Contested: files.Count > 1)))
            .OrderBy(item => item.File.Index).ToArray();
        foreach (var (file, isContested) in storedOutside)
        {
            if (file.LookupPath is null)
            {
                file.OutsideStatus = InstallShieldOutsideFileStatus.NoUsablePath;
                continue;
            }
            var (matches, length) = outside.Find(file.LookupPath);
            file.FoundLength = matches == 1 ? length : null;
            file.OutsideStatus = isContested ? InstallShieldOutsideFileStatus.PathHeldByDifferentFiles
                : matches == 0 ? InstallShieldOutsideFileStatus.Missing
                : matches > 1 ? InstallShieldOutsideFileStatus.SeveralMatches
                : length != file.StoredSize ? InstallShieldOutsideFileStatus.LengthDiffers
                : InstallShieldOutsideFileStatus.Available;
            if (file.OutsideStatus != InstallShieldOutsideFileStatus.Available) continue;
            if (file.Entry.Size > limits.MaximumExpandedBytes - expandedTotal)
                throw new InvalidDataException(
                    $"InstallShield cabinet expands to more than the limit of {limits.MaximumExpandedBytes} bytes.");
            expandedTotal += file.Entry.Size;
        }

        // The file groups are read last, from the header already open, so a set that opens without
        // them opens the same way with them: what does not read is reported, never thrown. They are
        // read for every entry of a listed member, of a contested path and of a file stored outside.
        var described = listed.Concat(conflicts.SelectMany(conflict => conflict.Files))
            .Concat(storedOutside.Where(item => !item.Contested).Select(item => item.File)).ToArray();
        var groupTable = InstallShieldFileGroupTable.Read(reader, descriptor, MajorVersion, descriptors.Length, limits);
        var (memberships, groupProblem) = groupTable.Memberships(described
            .SelectMany(member => member.SharedBy.Prepend(member.Listed)).Select(entry => entry.Index)
            .Order().ToArray());
        FileGroups = groupTable.Groups;
        FileGroupProblem = groupProblem;
        InstallShieldEntryMetadata Metadata(EntryName entry) => new(
            entry.Index, entry.DirectoryIndex, entry.Directory, entry.Name, memberships[entry.Index]);
        foreach (var member in described)
        {
            member.Metadata = new(member.Entry, Metadata(member.Listed), member.SharedBy.Select(Metadata).ToArray());
            if (member.Segments is null && member.OutsideStatus != InstallShieldOutsideFileStatus.Available) continue;
            foreach (var entry in member.SharedBy.Prepend(member.Listed)) readable.Add(entry.Index, member);
        }
        Members = listed.Select(member => member.Metadata!).ToArray();
        OutsideFiles = storedOutside.Select(item => new InstallShieldOutsideFile(
            item.File.Metadata!, item.File.LookupPath, item.File.StoredSize, item.File.Compressed,
            item.File.OutsideStatus, item.File.FoundLength)).ToArray();
        PathConflicts = conflicts.Select(conflict => new InstallShieldPathConflict(
            conflict.Path,
            conflict.Files.Where(file => file.Segments is not null).Select(file => file.Metadata!).ToArray(),
            conflict.Files.Where(file => file.Segments is null)
                .SelectMany(file => file.SharedBy.Prepend(file.Listed)).OrderBy(entry => entry.Index).Select(Metadata).ToArray()))
            .ToArray();
        foreach (var conflict in PathConflicts) contested.Add(conflict.Path);
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
    /// How the set's compressed members are decoded, as the caller chose when opening it. The header
    /// does not record it.
    /// </summary>
    public InstallShieldCompressedFormat CompressedFormat { get; }

    /// <summary>
    /// File-table entries that are not listed, in table order, each with its
    /// <see cref="InstallShieldSkippedFile.Kind"/>: entries the cabinet marks invalid,
    /// entries with no name or no data offset (their name and directory are not checked, and the
    /// path is <see langword="null"/> when they do not read), version 6 entries whose link ends at an
    /// entry the cabinet marks invalid or that has no data offset, entries that share a listed member's data
    /// at the same path, version 6 entries stored apart from a listed member at the same path
    /// with the same expanded size and MD5, entries stored outside the cabinet's volumes, and the
    /// entries at a path that holds different files (<see cref="PathConflicts"/>). Each reason names
    /// the entries it refers to.
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
    /// The paths, ignoring case, that hold two or more files the source cannot show to be the same,
    /// ordered by path. <see cref="Files"/> lists none of their files, and every entry at them is in
    /// <see cref="SkippedFiles"/>. Each file the cabinet holds inside its volumes is given with its
    /// entries' metadata and file groups, and <see cref="OpenEntry"/> reads it by index; its data was
    /// checked to lie inside the volumes and counts toward
    /// <see cref="InstallShieldCabinetLimits.MaximumExpandedBytes"/>, as a listed member's does.
    /// Empty when every path holds one file.
    /// </summary>
    public IReadOnlyList<InstallShieldPathConflict> PathConflicts { get; }

    /// <summary>
    /// Each file whose data is stored outside the cabinet's volumes, in the table order of its first
    /// entry, with where the source looked for it and what it found there
    /// (<see cref="InstallShieldOutsideFile.Status"/>). Its entries are in <see cref="SkippedFiles"/> as
    /// <see cref="InstallShieldSkippedFileKind.StoredOutsideCabinet"/>, and <see cref="Files"/> does not
    /// list it. <see cref="OpenEntry"/> reads one whose status is
    /// <see cref="InstallShieldOutsideFileStatus.Available"/>, whose expanded size counts toward
    /// <see cref="InstallShieldCabinetLimits.MaximumExpandedBytes"/>. Empty when every entry's data lies
    /// inside the volumes.
    /// </summary>
    public IReadOnlyList<InstallShieldOutsideFile> OutsideFiles { get; }

    /// <summary>
    /// The file groups the cabinet descriptor lists, in the order Unshield reads them: its 71 lists in
    /// turn, each from its head. Empty when the descriptor lists none. A group whose descriptor, name or
    /// range does not read or lies outside the file table is still listed, with its
    /// <see cref="InstallShieldFileGroup.Problem"/>.
    /// </summary>
    public IReadOnlyList<InstallShieldFileGroup> FileGroups { get; }

    /// <summary>
    /// Why the file groups were not read whole, or <see langword="null"/> when they were: a list entry
    /// that does not read, a list that returns to an entry it already read, more groups than
    /// <see cref="InstallShieldCabinetLimits.MaximumFiles"/>, or ranges that together name more
    /// (entry, group) pairs than that limit. <see cref="FileGroups"/> then holds the groups read
    /// before the problem (every group, when only the pairs pass the limit), and every entry's membership is
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
        var path = PortableAssetPath.Relative(relativePath);
        if (!members.TryGetValue(path, out var member))
            throw new FileNotFoundException(contested.Contains(path)
                ? "InstallShield cabinet holds different files at this path; PathConflicts lists them, and OpenEntry reads each one held inside the cabinet by its index."
                : "InstallShield cabinet has no such file.", relativePath);
        return Open(member);
    }

    /// <summary>
    /// Opens the file a file-table entry holds, by the entry's index, as <see cref="OpenRead"/> opens a
    /// member. The entry is one of a listed member (<see cref="InstallShieldMember.Metadata"/> or
    /// <see cref="InstallShieldMember.SharedBy"/> in <see cref="Members"/>), of a file of a path that
    /// holds different files (<see cref="InstallShieldPathConflict.Files"/>), or of a file stored outside
    /// the cabinet that was found beside the header (<see cref="OutsideFiles"/> with the status
    /// <see cref="InstallShieldOutsideFileStatus.Available"/>). An entry in
    /// <see cref="InstallShieldMember.SharedBy"/> reads the bytes of the member it belongs to. A file
    /// stored outside is read from the file found beside the header as the file's stored bytes, in
    /// <see cref="CompressedFormat"/> when the entry is compressed and deobfuscated when it is
    /// obfuscated, and checked as a member inside the cabinet is.
    /// </summary>
    /// <exception cref="FileNotFoundException">
    /// No such entry holds a file the source reads: the index is outside the file table; the entry is
    /// marked invalid, has no name or data offset, or links to an entry that cannot be read; or it is
    /// stored outside the cabinet, no version 6 copy inside matches it, and its file was not found
    /// beside the header with exactly its stored length. A volume, or a file stored outside, being
    /// gone also throws.
    /// </exception>
    public Stream OpenEntry(int index)
    {
        if (!readable.TryGetValue(index, out var member))
            throw new FileNotFoundException($"InstallShield cabinet file {index} holds no file the source reads.");
        return Open(member);
    }

    // A file stored outside is read from the file found beside the header, as one segment of its stored
    // bytes, decoded and checked as a member inside the cabinet is.
    private InstallShieldMemberStream Open(Member member) => new(
        member.Entry.Path, member.Entry.Size, member.Compressed ? CompressedFormat : null, member.Obfuscated,
        member.Md5, member.Segments ?? [new(0, 0, member.StoredSize)],
        member.Segments is null ? _ => outside.Open(member.LookupPath!) : openVolume);

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

    // Where a member's stored bytes lie, following the volume headers as Unshield does. Returns null,
    // with the volume in outsideVolume, when the member is stored outside the cabinet: as Unshield
    // tells it, a member whose data offset is exactly the length of the volume where its data starts,
    // split or not. A member with no stored bytes reads as empty wherever its offset points, so
    // it is never taken to be outside. Any other extent past the end of a volume is damage.
    private InstallShieldSegment[]? Segments(int index, FileDescriptor file, int fileCount, out int outsideVolume)
    {
        outsideVolume = 0;
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

        // Unshield compares the descriptor's offset with the length of the volume it opens for the
        // member before it reads a split member's parts, so the split flag, set or inferred, plays no part.
        if (remaining > 0 && file.DataOffset == header.Length)
        {
            outsideVolume = volume;
            return null;
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

    // One file a path holds, from the first entry that holds it (Listed). Segments is null when it is
    // stored outside the cabinet. Skipped holds the records of its other entries, and of the first
    // when it is stored outside, each with the reason to give when its path holds other files too
    // (null keeps the record as it is).
    private sealed record Member(
        ContentSourceEntry Entry, int Index, int DataIndex, bool Compressed, bool Obfuscated, byte[]? Md5,
        InstallShieldSegment[]? Segments, EntryName Listed)
    {
        public List<EntryName> SharedBy { get; } = [];
        public List<SkippedEntry> Skipped { get; } = [];
        public InstallShieldMember? Metadata { get; set; }
        // The bytes the cabinet stores for the file: its compressed size, or its expanded size when it
        // is not compressed.
        public long StoredSize { get; init; }
        // For a file stored outside: where it is looked up, what was found there and whether it is read.
        public string? LookupPath { get; set; }
        public long? FoundLength { get; set; }
        public InstallShieldOutsideFileStatus OutsideStatus { get; set; }
    }

    private sealed record SkippedEntry(InstallShieldSkippedFile Record, string? Contested);

    private sealed record EntryName(int Index, int DirectoryIndex, string Directory, string Name);
}

internal sealed record InstallShieldSegment(int Volume, long Offset, long Length);
