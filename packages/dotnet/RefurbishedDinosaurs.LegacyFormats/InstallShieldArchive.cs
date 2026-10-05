using System.Buffers.Binary;
using System.Text;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>
/// Bounds applied when an InstallShield 3 archive is opened. Each is checked against what the
/// archive header and file table declare, before any member is read.
/// </summary>
/// <param name="MaximumFiles">The most entries the file table may hold.</param>
/// <param name="MaximumExpandedBytes">The most bytes all listed members may expand to together.</param>
/// <param name="MaximumTableBytes">The largest directory table and file table, together, that is read into memory.</param>
public sealed record InstallShieldArchiveLimits(
    int MaximumFiles = 65_535,
    long MaximumExpandedBytes = 8L * 1024 * 1024 * 1024,
    int MaximumTableBytes = 16 * 1024 * 1024)
{
    /// <summary>The default limits.</summary>
    public static InstallShieldArchiveLimits Default { get; } = new();

    internal void Validate()
    {
        ArgumentOutOfRangeException.ThrowIfNegative(MaximumFiles);
        ArgumentOutOfRangeException.ThrowIfNegative(MaximumExpandedBytes);
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(MaximumTableBytes);
    }
}

/// <summary>
/// The files of an InstallShield 3 archive (a single <c>.Z</c> file, often named <c>_SETUP.1</c>) as
/// an <see cref="OriginalContentSource"/>. Open it with
/// <see cref="OriginalContentSource.OpenInstallShieldArchive(string, InstallShieldArchiveLimits?)"/>,
/// from inside another source with
/// <see cref="OriginalContentSource.OpenInstallShieldArchive(OriginalContentSource, string, InstallShieldArchiveLimits?)"/>,
/// or from a stream, such as an archive carved out of a self-extractor, with
/// <see cref="OriginalContentSource.OpenInstallShieldArchive(Stream, InstallShieldArchiveLimits?)"/>.
/// </summary>
/// <remarks>
/// <para>
/// Opening reads the header, the directory table and the file table, and checks each entry: the
/// directory it names is the one the directory table's file counts give it, its directory and name
/// joined pass <see cref="PortableAssetPath.Relative"/>, its stored bytes lie inside the archive, a
/// stored entry's two sizes agree, no two listed entries share a path ignoring case, and the entry
/// count and expanded total are within <see cref="InstallShieldArchiveLimits"/>. Members are decoded
/// only when read.
/// </para>
/// <para>
/// An archive whose header sets a split flag is read when it is part 1 of a set of 1 part, so that
/// every member's data is in this file: each listed entry must then name part 1 as its first and last
/// part, or opening throws <see cref="InvalidDataException"/>. Any other part of a split set, a
/// first part that declares more parts or none, and an unsplit archive that declares more than one
/// part are refused with <see cref="NotSupportedException"/> naming the header's flags, part number
/// and part count. So is an entry marked as spanning parts. Entries the archive marks invalid are
/// listed in <see cref="SkippedFiles"/>.
/// </para>
/// <para>
/// The archive records no checksum. Reading a compressed member to its end checks that its PKWARE
/// DCL data expands to exactly the declared size, ends with the end code, and leaves no whole byte
/// of its stored data unread. A failed check throws <see cref="InvalidDataException"/> from the read
/// that would have returned the member's last bytes. A stored member is checked by size only.
/// </para>
/// </remarks>
public sealed class InstallShieldArchiveSource : OriginalContentSource
{
    // The layout (header, directory table, file table) follows the readings of unshieldv3
    // (https://github.com/wfr/unshieldv3) and idecomp (https://github.com/lephilousophe/idecomp).
    internal const uint Signature = 0x8c655d13;
    internal const int HeaderSize = 5 + HeaderFieldsSize;
    private const int HeaderFieldsSize = 0x3a;
    private const int DirectoryFixedSize = 6;
    private const int FileFixedSize = 30;
    private const ushort SplitArchiveFlags = 0x3;
    private const ushort StoredFlag = 0x10;
    private const ushort InvalidFlag = 0x20;
    private const ushort SpansPartsFlag = 0x100;

    private readonly Func<Stream> openArchive;
    private readonly Dictionary<string, Member> members = new(StringComparer.OrdinalIgnoreCase);

    // limits has been validated by the caller. openArchive returns a new seekable stream over the
    // whole archive each time.
    internal InstallShieldArchiveSource(Func<Stream> openArchive, InstallShieldArchiveLimits limits)
    {
        this.openArchive = openArchive;
        using var archive = openArchive();
        var archiveLength = archive.Length;
        var header = new byte[HeaderSize];
        if (archiveLength < HeaderSize)
            throw new InvalidDataException("File is too short to be an InstallShield 3 archive.");
        archive.ReadExactly(header);
        if (BinaryPrimitives.ReadUInt32LittleEndian(header) != Signature)
            throw new InvalidDataException("File is not an InstallShield 3 archive.");
        if (header[4] != HeaderFieldsSize)
            throw new NotSupportedException(
                $"InstallShield 3 archive header declares {header[4]} bytes of fields; only 0x{HeaderFieldsSize:x} is supported.");

        var archiveFlags = BinaryPrimitives.ReadUInt16LittleEndian(header.AsSpan(10));
        var fileCount = BinaryPrimitives.ReadUInt16LittleEndian(header.AsSpan(12));
        var totalParts = header[30];
        var partNumber = header[31];
        long directoriesOffset = BinaryPrimitives.ReadUInt32LittleEndian(header.AsSpan(41));
        long directoriesSize = BinaryPrimitives.ReadUInt32LittleEndian(header.AsSpan(45));
        var directoryCount = BinaryPrimitives.ReadUInt16LittleEndian(header.AsSpan(49));
        long filesOffset = BinaryPrimitives.ReadUInt32LittleEndian(header.AsSpan(51));
        long filesSize = BinaryPrimitives.ReadUInt32LittleEndian(header.AsSpan(55));

        // A split set's first part declares the part count and later parts declare 0. A set of one
        // part holds all of its members' data, which each entry confirms by naming part 1 below.
        var split = (archiveFlags & SplitArchiveFlags) != 0;
        if (split ? partNumber != 1 || totalParts != 1 : totalParts > 1)
            throw new NotSupportedException(
                (split
                    ? "InstallShield 3 archive is one part of a split archive"
                    : "InstallShield 3 archive sets no split flag but declares more than one part")
                + $" (flags 0x{archiveFlags:x4}, part {partNumber}, {totalParts} parts); only unsplit archives and split archives of one part are supported.");
        if (fileCount > limits.MaximumFiles)
            throw new InvalidDataException(
                $"InstallShield 3 archive declares {fileCount} files, more than the limit of {limits.MaximumFiles}.");
        if (directoriesSize + filesSize > limits.MaximumTableBytes)
            throw new InvalidDataException(
                $"InstallShield 3 archive tables are {directoriesSize + filesSize} bytes, more than the limit of {limits.MaximumTableBytes}.");
        var directoryTable = ReadRegion(archive, archiveLength, directoriesOffset, directoriesSize, "directory table");
        var fileTable = ReadRegion(archive, archiveLength, filesOffset, filesSize, "file table");

        var directories = new (string Name, int Files)[directoryCount];
        var offset = 0;
        var declaredFiles = 0;
        var table = new TableReader(directoryTable, "directory table");
        for (var index = 0; index < directories.Length; index++)
        {
            var files = table.UInt16(offset);
            var entrySize = table.UInt16(offset + 2);
            var nameSize = table.UInt16(offset + 4);
            if (entrySize < DirectoryFixedSize + nameSize + 1)
                throw new InvalidDataException($"InstallShield 3 directory {index} declares an entry of {entrySize} bytes, too short for its name.");
            directories[index] = (table.Name(offset + DirectoryFixedSize, nameSize, entrySize, "directory name"), files);
            offset = table.Advance(offset, entrySize);
            declaredFiles += files;
        }
        if (offset != directoryTable.Length)
            throw new InvalidDataException(
                $"InstallShield 3 directory table holds {offset} bytes of entries, but the header declares {directoryTable.Length}.");
        if (declaredFiles != fileCount)
            throw new InvalidDataException(
                $"InstallShield 3 directories hold {declaredFiles} files, but the header declares {fileCount}.");

        var skipped = new List<InstallShieldSkippedFile>();
        long expandedTotal = 0;
        offset = 0;
        var directory = 0;
        var directoryFilesLeft = directories.Length > 0 ? directories[0].Files : 0;
        // Each directory's name with / separators, checked once, when a listed path in it is accepted.
        var portableDirectories = new string?[directories.Length];
        table = new TableReader(fileTable, "file table");
        for (var index = 0; index < fileCount; index++)
        {
            while (directoryFilesLeft == 0) directoryFilesLeft = directories[++directory].Files;
            directoryFilesLeft--;

            var lastPart = table.Byte(offset);
            var directoryIndex = table.UInt16(offset + 1);
            long expandedSize = table.UInt32(offset + 3);
            long storedSize = table.UInt32(offset + 7);
            long dataOffset = table.UInt32(offset + 11);
            var entrySize = table.UInt16(offset + 23);
            var flags = table.UInt16(offset + 25);
            var firstPart = table.Byte(offset + 28);
            var nameSize = table.Byte(offset + 29);
            if (entrySize < FileFixedSize + nameSize + 1)
                throw new InvalidDataException($"InstallShield 3 file {index} declares an entry of {entrySize} bytes, too short for its name.");
            var name = table.Name(offset + FileFixedSize, nameSize, entrySize, "file name");
            offset = table.Advance(offset, entrySize);

            // The file table names each entry's directory, and the directory table's counts place the
            // entries in table order. Readers of the format use one or the other, so they must agree.
            if (directoryIndex != directory)
                throw new InvalidDataException(
                    $"InstallShield 3 file {index} names directory {directoryIndex}, but the directory table's file counts place it in directory {directory}.");
            var combined = directories[directory].Name.Length == 0 ? name : $"{directories[directory].Name}\\{name}";
            string? path;
            try
            {
                path = PortableAssetPath.Relative(combined);
            }
            catch (InvalidDataException exception)
            {
                // An entry that is not listed is never written out, so its path need not be portable.
                if ((flags & InvalidFlag) == 0)
                    throw new InvalidDataException($"InstallShield 3 file {index} has a path that is not portable. {exception.Message}", exception);
                path = null;
            }

            if ((flags & InvalidFlag) != 0)
            {
                skipped.Add(new(index, path, "The archive marks the file invalid."));
                continue;
            }
            if ((flags & SpansPartsFlag) != 0)
                throw new NotSupportedException(
                    $"InstallShield 3 file {index} at '{path}' spans archive parts; only members held whole in one part are supported.");
            // Only a split archive's entries name their parts. In a set of one part, any other part
            // would hold data this file does not have.
            if (split && (firstPart != 1 || lastPart != 1))
                throw new InvalidDataException(
                    $"InstallShield 3 file {index} at '{path}' lies in parts {firstPart} to {lastPart}, but the archive is a split archive of one part.");
            var stored = (flags & StoredFlag) != 0;
            if (stored && storedSize != expandedSize)
                throw new InvalidDataException(
                    $"InstallShield 3 file {index} at '{path}' is stored uncompressed, but declares {storedSize} stored and {expandedSize} expanded bytes.");
            if (dataOffset < HeaderSize || dataOffset > archiveLength - storedSize)
                throw new InvalidDataException(
                    $"InstallShield 3 file {index} at '{path}' lies outside the {archiveLength}-byte archive.");
            if (members.TryGetValue(path!, out var existing))
                throw new InvalidDataException(
                    $"InstallShield 3 archive holds two files at '{path}' (file {existing.Index} and file {index}).");
            // The joined path is accepted, so the directory alone is too: its characters and
            // components are among the path's.
            var directoryName = directories[directory].Name;
            var entry = new ContentSourceEntry(path!, expandedSize);
            var metadata = new InstallShieldEntryMetadata(index, directory,
                portableDirectories[directory] ??= directoryName.Length == 0 ? string.Empty : PortableAssetPath.Relative(directoryName),
                name.Replace('\\', '/'), InstallShieldFileGroupMembership.NoFileGroups);
            members.Add(path!, new Member(entry, index, stored, dataOffset, storedSize, new(entry, metadata, [])));
            if (expandedSize > limits.MaximumExpandedBytes - expandedTotal)
                throw new InvalidDataException(
                    $"InstallShield 3 archive expands to more than the limit of {limits.MaximumExpandedBytes} bytes.");
            expandedTotal += expandedSize;
        }
        if (offset != fileTable.Length)
            throw new InvalidDataException(
                $"InstallShield 3 file table holds {offset} bytes of entries, but the header declares {fileTable.Length}.");

        SkippedFiles = skipped;
        var listed = members.Values.OrderBy(member => member.Entry.Path, StringComparer.OrdinalIgnoreCase).ToArray();
        Files = listed.Select(member => member.Entry).ToArray();
        Members = listed.Select(member => member.Metadata).ToArray();
    }

    /// <inheritdoc />
    public override string Kind => ContentSourceKinds.InstallShieldArchive;
    /// <summary>Always <see langword="null"/>: an archive has no volume identifier.</summary>
    public override string? Label => null;
    /// <inheritdoc />
    public override IReadOnlyList<ContentSourceEntry> Files { get; }
    /// <summary>File-table entries the archive marks invalid, in table order. They are not listed.</summary>
    public IReadOnlyList<InstallShieldSkippedFile> SkippedFiles { get; }

    /// <summary>
    /// The listed members with the file-table entries they are listed from, in the order of
    /// <see cref="Files"/>, in the shape <see cref="InstallShieldCabinetSource.Members"/> gives: each
    /// entry's index, directory index, directory and name. The archive format has no file groups, so
    /// every membership is <see cref="InstallShieldFileGroupMembershipKind.NoFileGroups"/>, and
    /// <see cref="InstallShieldMember.SharedBy"/> is empty, since two entries at one path fail the open.
    /// </summary>
    public IReadOnlyList<InstallShieldMember> Members { get; }

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
    /// <exception cref="FileNotFoundException">The archive has no such member.</exception>
    /// <exception cref="InvalidDataException"><paramref name="relativePath"/> is not accepted by <see cref="PortableAssetPath.Relative"/>.</exception>
    public override Stream OpenRead(string relativePath)
    {
        if (!members.TryGetValue(PortableAssetPath.Relative(relativePath), out var member))
            throw new FileNotFoundException("InstallShield 3 archive has no such file.", relativePath);
        return new InstallShieldArchiveMemberStream(
            member.Entry.Path, member.Entry.Size, member.Stored, member.DataOffset, member.StoredSize, openArchive);
    }

    /// <inheritdoc />
    public override void Dispose() { }

    private static byte[] ReadRegion(Stream archive, long archiveLength, long offset, long size, string what)
    {
        if (offset < HeaderSize || offset > archiveLength - size)
            throw new InvalidDataException($"InstallShield 3 archive's {what} lies outside the {archiveLength}-byte archive.");
        var bytes = new byte[size];
        archive.Position = offset;
        archive.ReadExactly(bytes);
        return bytes;
    }

    private sealed record Member(
        ContentSourceEntry Entry, int Index, bool Stored, long DataOffset, long StoredSize, InstallShieldMember Metadata);

    private readonly struct TableReader(byte[] data, string table)
    {
        public byte Byte(int offset) => data[Require(offset, 1)];
        public ushort UInt16(int offset) => BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(Require(offset, 2)));
        public uint UInt32(int offset) => BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(Require(offset, 4)));

        // A name is nameSize single-byte characters, read as ISO 8859-1, then a NUL, all inside the entry.
        public string Name(int offset, int nameSize, int entrySize, string what)
        {
            Require(offset, nameSize + 1);
            if (data[offset + nameSize] != 0)
                throw new InvalidDataException($"InstallShield 3 {what} at byte {offset} of the {table} has no terminator after its {nameSize} bytes.");
            var name = Encoding.Latin1.GetString(data, offset, nameSize);
            if (name.Contains('\0'))
                throw new InvalidDataException($"InstallShield 3 {what} at byte {offset} of the {table} holds a NUL.");
            return name;
        }

        public int Advance(int offset, int entrySize)
        {
            Require(offset, entrySize);
            return offset + entrySize;
        }

        private int Require(int offset, int length)
        {
            if (offset > data.Length - length)
                throw new InvalidDataException($"InstallShield 3 {table} is truncated: an entry runs past its {data.Length} bytes.");
            return offset;
        }
    }
}

internal static class InstallShieldArchiveOpener
{
    public static bool HasSignature(string path)
    {
        Span<byte> start = stackalloc byte[4];
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        return stream.ReadAtLeast(start, 4, throwOnEndOfStream: false) == 4 &&
               BinaryPrimitives.ReadUInt32LittleEndian(start) == InstallShieldArchiveSource.Signature;
    }

    public static InstallShieldArchiveSource FromFile(string path, InstallShieldArchiveLimits limits)
    {
        var fullPath = Path.GetFullPath(path);
        if (!File.Exists(fullPath)) throw new FileNotFoundException("InstallShield 3 archive does not exist.", fullPath);
        return new InstallShieldArchiveSource(
            () => new FileStream(fullPath, FileMode.Open, FileAccess.Read, FileShare.Read), limits);
    }

    public static InstallShieldArchiveSource FromSource(OriginalContentSource container, string archivePath, InstallShieldArchiveLimits limits)
    {
        var relative = PortableAssetPath.Relative(archivePath);
        if (!container.TryGetFile(relative, out _))
            throw new FileNotFoundException("InstallShield 3 archive was not found in the source.", relative);
        return new InstallShieldArchiveSource(() => container.OpenRead(relative), limits);
    }

    public static InstallShieldArchiveSource FromStream(Stream archive, InstallShieldArchiveLimits limits)
    {
        var gate = SharedStreamView.GateFor(archive);
        return new InstallShieldArchiveSource(() => new SharedStreamView(archive, gate), limits);
    }
}
