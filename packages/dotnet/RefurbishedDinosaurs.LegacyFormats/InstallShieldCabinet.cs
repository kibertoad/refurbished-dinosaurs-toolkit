using System.Buffers.Binary;
using System.Text;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>
/// Bounds applied when an InstallShield cabinet set is opened. Each is checked against what the
/// header declares, before any member is read.
/// </summary>
/// <param name="MaximumFiles">The most members the set may list.</param>
/// <param name="MaximumExpandedBytes">The most bytes all listed members may expand to together.</param>
/// <param name="MaximumHeaderBytes">The largest header file that is read into memory.</param>
public sealed record InstallShieldCabinetLimits(
    int MaximumFiles = 100_000,
    long MaximumExpandedBytes = 8L * 1024 * 1024 * 1024,
    int MaximumHeaderBytes = 64 * 1024 * 1024)
{
    /// <summary>The default limits.</summary>
    public static InstallShieldCabinetLimits Default { get; } = new();

    internal void Validate()
    {
        ArgumentOutOfRangeException.ThrowIfNegative(MaximumFiles);
        ArgumentOutOfRangeException.ThrowIfNegative(MaximumExpandedBytes);
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(MaximumHeaderBytes);
    }
}

/// <summary>A file-table entry an <see cref="InstallShieldCabinetSource"/> does not list.</summary>
/// <param name="Index">The entry's index in the cabinet's file table.</param>
/// <param name="Path">The entry's path, when it has a name.</param>
/// <param name="Reason">Why the entry is not listed.</param>
public sealed record InstallShieldSkippedFile(int Index, string? Path, string Reason);

/// <summary>
/// The files of an InstallShield 5 or 6 cabinet set (<c>dataN.hdr</c> with <c>dataN.cab</c> volumes)
/// as an <see cref="OriginalContentSource"/>. Open it with
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
/// Reading a member to its end checks that its data expands to exactly the declared size and, for
/// a version 6 set, that the expanded bytes match the MD5 the header records. A failed check throws
/// <see cref="InvalidDataException"/> from the read that would have returned the member's last bytes.
/// Version 5 members are checked by size only.
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
    private const int MaximumVolume = 255;
    private const ushort InvalidFlag = 8;
    private const ushort CompressedFlag = 4;
    private const ushort ObfuscatedFlag = 2;
    private const ushort SplitFlag = 1;
    private const byte LinkPreviousFlag = 1;

    private readonly Func<int, Stream> openVolume;
    private readonly Dictionary<string, Member> members = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<int, VolumeHeader> volumes = [];

    internal InstallShieldCabinetSource(byte[] header, Func<int, Stream> openVolume, InstallShieldCabinetLimits limits)
    {
        limits.Validate();
        this.openVolume = openVolume;
        var reader = new HeaderReader(header);
        var signature = reader.UInt32(0);
        if (signature == MicrosoftCabinetSignature)
            throw new InvalidDataException("File is a Microsoft cabinet, not an InstallShield cabinet.");
        if (signature != Signature)
            throw new InvalidDataException("File is not an InstallShield cabinet header.");
        var version = reader.UInt32(4);
        MajorVersion = ReadMajorVersion(version);
        if (MajorVersion is not (5 or 6))
            throw new NotSupportedException(
                $"InstallShield cabinet version word 0x{version:x8} reads as major version {MajorVersion}; only 5 and 6 are supported.");

        long descriptor = reader.UInt32(12);
        var descriptorSize = reader.UInt32(16);
        if (descriptorSize < DescriptorFieldsSize)
            throw new InvalidDataException("InstallShield header has no cabinet descriptor.");
        reader.Require(descriptor, descriptorSize, "cabinet descriptor");
        var table = descriptor + reader.UInt32(descriptor + 0x0c);
        var directoryCount = reader.UInt32(descriptor + 0x1c);
        var fileCount = reader.UInt32(descriptor + 0x28);
        long descriptorsOffset = reader.UInt32(descriptor + 0x2c);
        if (fileCount > limits.MaximumFiles)
            throw new InvalidDataException(
                $"InstallShield cabinet declares {fileCount} files, more than the limit of {limits.MaximumFiles}.");
        if (directoryCount > header.Length / 4)
            throw new InvalidDataException("InstallShield directory count exceeds the header.");

        var directories = new string[directoryCount];
        for (var index = 0; index < directories.Length; index++)
            directories[index] = reader.String(table + reader.UInt32(table + 4L * index), "directory name");

        var descriptors = new FileDescriptor[fileCount];
        for (var index = 0; index < descriptors.Length; index++)
            descriptors[index] = MajorVersion == 5
                ? ReadVersion5Descriptor(reader, table + reader.UInt32(table + 4L * (directoryCount + index)))
                : ReadVersion6Descriptor(reader, table + descriptorsOffset + (long)index * Version6DescriptorSize);

        var skipped = new List<InstallShieldSkippedFile>();
        long expandedTotal = 0;
        for (var index = 0; index < descriptors.Length; index++)
        {
            var file = descriptors[index];
            string? path = null;
            if (file.NameOffset != 0)
            {
                if (file.DirectoryIndex >= directories.Length)
                    throw new InvalidDataException($"InstallShield file {index} names directory {file.DirectoryIndex}, which does not exist.");
                var name = reader.String(table + file.NameOffset, "file name");
                var directory = directories[file.DirectoryIndex];
                var combined = directory.Length == 0 ? name : $"{directory}\\{name}";
                try
                {
                    path = PortableAssetPath.Relative(combined);
                }
                catch (InvalidDataException exception)
                {
                    throw new InvalidDataException($"InstallShield file {index} has a path that is not relative: '{combined}'.", exception);
                }
            }

            var reason = (file.Flags & InvalidFlag) != 0 ? "The cabinet marks the file invalid."
                : path is null ? "The file has no name."
                : file.DataOffset == 0 ? "The file has no data offset."
                : null;
            if (reason is not null)
            {
                skipped.Add(new(index, path, reason));
                continue;
            }

            var (dataIndex, data) = ResolveLink(descriptors, index);
            if (data.ExpandedSize < 0 || data.CompressedSize < 0)
                throw new InvalidDataException($"InstallShield file {index} declares a size beyond 2^63 bytes.");
            var segments = Segments(dataIndex, data, descriptors.Length);
            var member = new Member(
                new ContentSourceEntry(path!, data.ExpandedSize), dataIndex, (data.Flags & CompressedFlag) != 0,
                (data.Flags & ObfuscatedFlag) != 0, MajorVersion >= 6 ? data.Md5 : null, segments);
            if (members.TryGetValue(path!, out var existing))
            {
                if (existing.DataIndex == dataIndex) continue;
                throw new InvalidDataException(
                    $"InstallShield cabinet holds two different files at '{path}' (file {existing.DataIndex} and file {dataIndex}).");
            }
            members.Add(path!, member);
            expandedTotal += data.ExpandedSize;
            if (expandedTotal > limits.MaximumExpandedBytes)
                throw new InvalidDataException(
                    $"InstallShield cabinet expands to more than the limit of {limits.MaximumExpandedBytes} bytes.");
        }

        SkippedFiles = skipped;
        Files = members.Values.Select(member => member.Entry)
            .OrderBy(entry => entry.Path, StringComparer.OrdinalIgnoreCase).ToArray();
    }

    /// <inheritdoc />
    public override string Kind => ContentSourceKinds.InstallShieldCabinet;
    /// <summary>Always <see langword="null"/>: a cabinet set has no volume identifier.</summary>
    public override string? Label => null;
    /// <inheritdoc />
    public override IReadOnlyList<ContentSourceEntry> Files { get; }
    /// <summary>The InstallShield major version the header's version word gives: 5 or 6.</summary>
    public int MajorVersion { get; }
    /// <summary>
    /// File-table entries that are not listed, in table order: entries the cabinet marks invalid, and
    /// entries with no name or no data offset.
    /// </summary>
    public IReadOnlyList<InstallShieldSkippedFile> SkippedFiles { get; }

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

    internal static int ReadMajorVersion(uint version) => (version >> 24) switch
    {
        1 => (int)((version >> 12) & 0xf),
        2 or 4 => (int)(version & 0xffff) / 100,
        _ => 0
    };

    private static FileDescriptor ReadVersion5Descriptor(HeaderReader reader, long offset)
    {
        reader.Require(offset, Version5DescriptorSize, "file descriptor");
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

    private static FileDescriptor ReadVersion6Descriptor(HeaderReader reader, long offset)
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

    // A version 6 entry can link to an earlier entry whose data it shares.
    private static (int Index, FileDescriptor Descriptor) ResolveLink(FileDescriptor[] descriptors, int index)
    {
        var current = index;
        for (var step = 0; step <= descriptors.Length; step++)
        {
            var descriptor = descriptors[current];
            if ((descriptor.LinkFlags & LinkPreviousFlag) == 0)
            {
                if ((descriptor.Flags & InvalidFlag) != 0 || descriptor.DataOffset == 0)
                    throw new InvalidDataException($"InstallShield file {index} links to file {current}, which has no data.");
                return (current, descriptor);
            }
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
        var volume = MajorVersion == 5 ? 1 : file.Volume;
        var header = Volume(volume, index);
        var split = (file.Flags & SplitFlag) != 0;
        if (MajorVersion == 5)
        {
            // Version 5 descriptors do not name a volume: the member is in the first volume whose
            // last file index reaches it, and is split when that volume's record of it disagrees.
            while (index > header.LastIndex) header = Volume(++volume, index);
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
        var size = MajorVersion == 5 ? 40 : 64;
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
        var header = MajorVersion == 5
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
        ContentSourceEntry Entry, int DataIndex, bool Compressed, bool Obfuscated, byte[]? Md5,
        InstallShieldSegment[] Segments);

    private sealed class HeaderReader(byte[] data)
    {
        public void Require(long offset, long length, string what)
        {
            if (offset < 0 || length < 0 || offset > data.Length - length)
                throw new InvalidDataException($"InstallShield header is truncated: its {what} lies past the end.");
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

        public byte[] Bytes(long offset, int length)
        {
            Require(offset, length, "data");
            return data.AsSpan((int)offset, length).ToArray();
        }

        // Names are NUL-terminated single-byte strings, read as ISO 8859-1.
        public string String(long offset, string what)
        {
            Require(offset, 1, what);
            var end = Array.IndexOf(data, (byte)0, (int)offset);
            if (end < 0) throw new InvalidDataException($"InstallShield header is truncated: a {what} has no terminator.");
            return Encoding.Latin1.GetString(data, (int)offset, end - (int)offset);
        }
    }
}

internal sealed record InstallShieldSegment(int Volume, long Offset, long Length);

internal static class InstallShieldCabinetOpener
{
    public static InstallShieldCabinetSource FromDirectory(string path, InstallShieldCabinetLimits limits)
    {
        var fullPath = Path.GetFullPath(path);
        if (!File.Exists(fullPath)) throw new FileNotFoundException("InstallShield cabinet header does not exist.", fullPath);
        var directory = Path.GetDirectoryName(fullPath)!;
        var prefix = VolumePrefix(Path.GetFileName(fullPath));
        var header = ReadHeader(new FileStream(fullPath, FileMode.Open, FileAccess.Read, FileShare.Read), limits);
        return new InstallShieldCabinetSource(header, volume =>
        {
            var name = $"{prefix}{volume}.cab";
            var matches = Directory.EnumerateFiles(directory)
                .Where(file => Path.GetFileName(file).Equals(name, StringComparison.OrdinalIgnoreCase)).ToArray();
            return matches.Length switch
            {
                1 => new FileStream(matches[0], FileMode.Open, FileAccess.Read, FileShare.Read),
                0 => throw new FileNotFoundException($"InstallShield volume {volume} was not found.", Path.Combine(directory, name)),
                _ => throw new InvalidDataException($"Several files match InstallShield volume name '{name}'.")
            };
        }, limits);
    }

    public static InstallShieldCabinetSource FromSource(OriginalContentSource container, string headerPath, InstallShieldCabinetLimits limits)
    {
        var relative = PortableAssetPath.Relative(headerPath);
        var separator = relative.LastIndexOf('/');
        var directory = separator < 0 ? string.Empty : relative[..(separator + 1)];
        var prefix = VolumePrefix(relative[(separator + 1)..]);
        if (!container.TryGetFile(relative, out _))
            throw new FileNotFoundException("InstallShield cabinet header was not found in the source.", relative);
        var header = ReadHeader(container.OpenRead(relative), limits);
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

    private static byte[] ReadHeader(Stream stream, InstallShieldCabinetLimits limits)
    {
        using (stream)
        {
            if (stream.Length > limits.MaximumHeaderBytes)
                throw new InvalidDataException(
                    $"InstallShield header is {stream.Length} bytes, more than the limit of {limits.MaximumHeaderBytes}.");
            var bytes = new byte[stream.Length];
            stream.ReadExactly(bytes);
            return bytes;
        }
    }
}
