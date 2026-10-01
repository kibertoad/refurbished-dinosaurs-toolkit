using System.Buffers.Binary;
using System.Text;

namespace ScientificMethod.LegacyFormats;

/// <summary>A file in an <see cref="OriginalContentSource"/>.</summary>
/// <param name="Path">Path from the source's root with <c>/</c> separators.</param>
/// <param name="Size">The file's size in bytes.</param>
public sealed record ContentSourceEntry(string Path, long Size);

/// <summary>
/// Read access to the user's original files, from an installed directory or an ISO 9660 image, behind
/// one interface. Paths are relative with <c>/</c> or <c>\</c> separators and match ignoring case.
/// </summary>
public abstract class OriginalContentSource : IDisposable
{
    /// <summary><c>directory</c> or <c>iso9660</c>.</summary>
    public abstract string Kind { get; }
    /// <summary>The ISO volume identifier, or <see langword="null"/> for a directory.</summary>
    public abstract string? Label { get; }
    /// <summary>Every file, sorted by path ignoring case.</summary>
    public abstract IReadOnlyList<ContentSourceEntry> Files { get; }
    /// <summary>Looks up a file.</summary>
    /// <exception cref="InvalidDataException"><paramref name="relativePath"/> is absolute or contains empty, <c>.</c> or <c>..</c> segments.</exception>
    public abstract bool TryGetFile(string relativePath, out ContentSourceEntry? entry);
    /// <summary>Opens a file as a read-only seekable stream.</summary>
    /// <exception cref="FileNotFoundException">The source has no such file.</exception>
    /// <exception cref="InvalidDataException"><paramref name="relativePath"/> is absolute or contains empty, <c>.</c> or <c>..</c> segments.</exception>
    public abstract Stream OpenRead(string relativePath);
    /// <summary>Releases the source. Streams already opened stay usable.</summary>
    public abstract void Dispose();

    /// <summary>
    /// Opens <paramref name="path"/> as a directory source when it is a directory, and as an ISO 9660
    /// image (2048-byte sectors) when it is a file. An image is checked when opened: block size, volume
    /// size against the file, both-endian fields agreeing, and every directory and file extent inside the
    /// volume.
    /// </summary>
    /// <exception cref="FileNotFoundException">Nothing exists at <paramref name="path"/>.</exception>
    /// <exception cref="InvalidDataException">The image is not a valid ISO 9660 volume.</exception>
    public static OriginalContentSource Open(string path)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        if (Directory.Exists(path)) return new DirectoryContentSource(path);
        if (File.Exists(path)) return new Iso9660ContentSource(path);
        throw new FileNotFoundException("Original-content source does not exist.", path);
    }

    internal static string NormalizeRelative(string path)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        var normalized = path.Replace('\\', '/');
        if (Path.IsPathRooted(path) || normalized.Split('/').Any(x => x is "" or "." or ".."))
            throw new InvalidDataException($"Source path must be relative: '{path}'.");
        return normalized;
    }
}

internal sealed class DirectoryContentSource : OriginalContentSource
{
    private readonly string root;
    private readonly Dictionary<string, (ContentSourceEntry Entry, string FullPath)> files;

    public DirectoryContentSource(string path)
    {
        root = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        files = new Dictionary<string, (ContentSourceEntry, string)>(StringComparer.OrdinalIgnoreCase);
        var options = new EnumerationOptions
        {
            RecurseSubdirectories = true,
            IgnoreInaccessible = false,
            AttributesToSkip = FileAttributes.ReparsePoint
        };
        foreach (var fullPath in Directory.EnumerateFiles(root, "*", options))
        {
            var relative = OriginalContentSource.NormalizeRelative(Path.GetRelativePath(root, fullPath));
            var entry = new ContentSourceEntry(relative, new FileInfo(fullPath).Length);
            if (!files.TryAdd(relative, (entry, fullPath)))
                throw new InvalidDataException($"Source contains duplicate path '{relative}'.");
        }
        Files = files.Values.Select(value => value.Entry)
            .OrderBy(entry => entry.Path, StringComparer.OrdinalIgnoreCase).ToArray();
    }

    public override string Kind => "directory";
    public override string? Label => null;
    public override IReadOnlyList<ContentSourceEntry> Files { get; }

    public override bool TryGetFile(string relativePath, out ContentSourceEntry? entry)
    {
        if (files.TryGetValue(OriginalContentSource.NormalizeRelative(relativePath), out var value))
        {
            entry = value.Entry;
            return true;
        }
        entry = null;
        return false;
    }

    public override Stream OpenRead(string relativePath)
    {
        if (!files.TryGetValue(OriginalContentSource.NormalizeRelative(relativePath), out var value))
            throw new FileNotFoundException("Source file was not found.", relativePath);
        return new FileStream(value.FullPath, FileMode.Open, FileAccess.Read, FileShare.Read);
    }

    public override void Dispose() { }
}

internal sealed class Iso9660ContentSource : OriginalContentSource
{
    private const int SectorSize = 2048;
    private const int MaximumDescriptors = 240;
    private const int MaximumDirectoryDepth = 32;
    private const int MaximumEntries = 100_000;
    private const uint MaximumDirectoryBytes = 64 * 1024 * 1024;

    private readonly string imagePath;
    private readonly long imageLength;
    private readonly long volumeLength;
    private readonly Dictionary<string, IsoEntry> files = new(StringComparer.OrdinalIgnoreCase);

    public Iso9660ContentSource(string path)
    {
        imagePath = Path.GetFullPath(path);
        using var stream = new FileStream(imagePath, FileMode.Open, FileAccess.Read, FileShare.Read);
        imageLength = stream.Length;
        if (imageLength < 18L * SectorSize)
            throw new InvalidDataException("Source is too small to be an ISO9660 image.");

        var descriptor = FindPrimaryVolumeDescriptor(stream);
        var blockSize = ReadBothEndianUInt16(descriptor, 128, "logical block size");
        if (blockSize != SectorSize)
            throw new InvalidDataException("ISO9660 logical block size is unsupported.");
        var volumeSectors = ReadBothEndianUInt32(descriptor, 80, "volume space size");
        volumeLength = checked((long)volumeSectors * SectorSize);
        if (volumeSectors < 18 || volumeLength > imageLength)
            throw new InvalidDataException("ISO9660 declared volume exceeds the image.");
        Label = DecodeIdentifier(descriptor.AsSpan(40, 32));
        var root = ParseDirectoryRecord(descriptor, 156, descriptor[156]);
        if (!root.IsDirectory) throw new InvalidDataException("ISO9660 root record is not a directory.");
        ReadDirectory(stream, root, string.Empty, 0, new HashSet<(uint, uint)>());
        Files = files.Values.Select(value => value.Entry)
            .OrderBy(entry => entry.Path, StringComparer.OrdinalIgnoreCase).ToArray();
    }

    public override string Kind => "iso9660";
    public override string? Label { get; }
    public override IReadOnlyList<ContentSourceEntry> Files { get; }

    public override bool TryGetFile(string relativePath, out ContentSourceEntry? entry)
    {
        if (files.TryGetValue(OriginalContentSource.NormalizeRelative(relativePath), out var value))
        {
            entry = value.Entry;
            return true;
        }
        entry = null;
        return false;
    }

    public override Stream OpenRead(string relativePath)
    {
        if (!files.TryGetValue(OriginalContentSource.NormalizeRelative(relativePath), out var value))
            throw new FileNotFoundException("Source file was not found in the ISO image.", relativePath);
        return new ExtentReadStream(imagePath, checked((long)value.Extent * SectorSize), value.Entry.Size);
    }

    public override void Dispose() { }

    private static byte[] FindPrimaryVolumeDescriptor(Stream stream)
    {
        var buffer = new byte[SectorSize];
        for (var index = 16; index < 16 + MaximumDescriptors; index++)
        {
            stream.Position = checked((long)index * SectorSize);
            ReadExactly(stream, buffer);
            if (!buffer.AsSpan(1, 5).SequenceEqual("CD001"u8) || buffer[6] != 1)
                throw new InvalidDataException($"Invalid ISO9660 volume descriptor at sector {index}.");
            if (buffer[0] == 1) return buffer.ToArray();
            if (buffer[0] == 255) break;
        }
        throw new InvalidDataException("ISO9660 primary volume descriptor was not found.");
    }

    private void ReadDirectory(
        Stream stream, DirectoryRecord directory, string parent, int depth,
        HashSet<(uint Extent, uint Length)> visited)
    {
        if (depth > MaximumDirectoryDepth)
            throw new InvalidDataException("ISO9660 directory depth exceeds the safety limit.");
        if (directory.DataLength > MaximumDirectoryBytes)
            throw new InvalidDataException("ISO9660 directory exceeds the safety limit.");
        ValidateExtent(directory.Extent, directory.DataLength);
        if (!visited.Add((directory.Extent, directory.DataLength))) return;

        var data = new byte[checked((int)directory.DataLength)];
        stream.Position = checked((long)directory.Extent * SectorSize);
        ReadExactly(stream, data);
        var offset = 0;
        while (offset < data.Length)
        {
            var recordLength = data[offset];
            if (recordLength == 0)
            {
                offset = Math.Min(data.Length, checked(((offset / SectorSize) + 1) * SectorSize));
                continue;
            }
            if (recordLength < 34 || offset + recordLength > data.Length)
                throw new InvalidDataException($"Invalid ISO9660 directory record at byte {offset}.");
            var record = ParseDirectoryRecord(data, offset, recordLength);
            offset += recordLength;
            if (record.Identifier is "\0" or "\u0001") continue;
            if (record.IsMultiExtent)
                throw new InvalidDataException($"Multi-extent ISO9660 entry is unsupported: '{record.Identifier}'.");
            var name = NormalizeIsoName(record.Identifier);
            var relative = string.IsNullOrEmpty(parent) ? name : $"{parent}/{name}";
            ValidateExtent(record.Extent, record.DataLength);
            if (record.IsDirectory)
            {
                ReadDirectory(stream, record, relative, depth + 1, visited);
                continue;
            }
            var entry = new ContentSourceEntry(relative, record.DataLength);
            if (!files.TryAdd(relative, new IsoEntry(entry, record.Extent)))
                throw new InvalidDataException($"ISO9660 image contains duplicate path '{relative}'.");
            if (files.Count > MaximumEntries)
                throw new InvalidDataException("ISO9660 entry count exceeds the safety limit.");
        }
    }

    private void ValidateExtent(uint extent, uint length)
    {
        var start = checked((long)extent * SectorSize);
        var end = checked(start + length);
        if (start < 0 || end > volumeLength)
            throw new InvalidDataException("ISO9660 extent lies outside the declared volume.");
    }

    private static DirectoryRecord ParseDirectoryRecord(byte[] data, int offset, int recordLength)
    {
        if (recordLength < 34 || offset < 0 || offset + recordLength > data.Length)
            throw new InvalidDataException("ISO9660 directory record is truncated.");
        var identifierLength = data[offset + 32];
        if (33 + identifierLength > recordLength)
            throw new InvalidDataException("ISO9660 directory identifier is truncated.");
        var extent = ReadBothEndianUInt32(data, offset + 2, "extent");
        var length = ReadBothEndianUInt32(data, offset + 10, "data length");
        var identifier = Encoding.ASCII.GetString(data, offset + 33, identifierLength);
        var flags = data[offset + 25];
        return new(extent, length, identifier, (flags & 0x02) != 0, (flags & 0x80) != 0);
    }

    private static uint ReadBothEndianUInt32(byte[] data, int offset, string field)
    {
        var little = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(offset, 4));
        var big = BinaryPrimitives.ReadUInt32BigEndian(data.AsSpan(offset + 4, 4));
        if (little != big) throw new InvalidDataException($"ISO9660 {field} byte orders disagree.");
        return little;
    }

    private static ushort ReadBothEndianUInt16(byte[] data, int offset, string field)
    {
        var little = BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(offset, 2));
        var big = BinaryPrimitives.ReadUInt16BigEndian(data.AsSpan(offset + 2, 2));
        if (little != big) throw new InvalidDataException($"ISO9660 {field} byte orders disagree.");
        return little;
    }

    private static string NormalizeIsoName(string identifier)
    {
        var separator = identifier.LastIndexOf(';');
        var name = separator >= 0 ? identifier[..separator] : identifier;
        name = name.TrimEnd('.');
        if (string.IsNullOrWhiteSpace(name) || name.Contains('/') || name.Contains('\\') || name.Contains('\0'))
            throw new InvalidDataException($"Invalid ISO9660 identifier '{identifier}'.");
        return name;
    }

    private static string? DecodeIdentifier(ReadOnlySpan<byte> bytes)
    {
        var value = Encoding.ASCII.GetString(bytes).TrimEnd(' ');
        return value.Length == 0 ? null : value;
    }

    private static void ReadExactly(Stream stream, byte[] buffer)
    {
        var offset = 0;
        while (offset < buffer.Length)
        {
            var read = stream.Read(buffer, offset, buffer.Length - offset);
            if (read == 0) throw new EndOfStreamException("ISO9660 image ended unexpectedly.");
            offset += read;
        }
    }

    private sealed record IsoEntry(ContentSourceEntry Entry, uint Extent);
    private sealed record DirectoryRecord(
        uint Extent, uint DataLength, string Identifier, bool IsDirectory, bool IsMultiExtent);
}

internal sealed class ExtentReadStream : Stream
{
    private readonly FileStream stream;
    private readonly long start;
    private readonly long length;
    private long position;

    public ExtentReadStream(string path, long start, long length)
    {
        stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        this.start = start;
        this.length = length;
        stream.Position = start;
    }

    public override bool CanRead => true;
    public override bool CanSeek => true;
    public override bool CanWrite => false;
    public override long Length => length;
    public override long Position
    {
        get => position;
        set => Seek(value, SeekOrigin.Begin);
    }

    public override int Read(byte[] buffer, int offset, int count)
    {
        ArgumentNullException.ThrowIfNull(buffer);
        ArgumentOutOfRangeException.ThrowIfNegative(offset);
        ArgumentOutOfRangeException.ThrowIfNegative(count);
        if (buffer.Length - offset < count) throw new ArgumentException("Buffer range is invalid.");
        var bounded = (int)Math.Min(count, length - position);
        if (bounded <= 0) return 0;
        var read = stream.Read(buffer, offset, bounded);
        position += read;
        return read;
    }

    public override int Read(Span<byte> buffer)
    {
        var bounded = (int)Math.Min(buffer.Length, length - position);
        if (bounded <= 0) return 0;
        var read = stream.Read(buffer[..bounded]);
        position += read;
        return read;
    }

    public override async ValueTask<int> ReadAsync(
        Memory<byte> buffer, CancellationToken cancellationToken = default)
    {
        var bounded = (int)Math.Min(buffer.Length, length - position);
        if (bounded <= 0) return 0;
        var read = await stream.ReadAsync(buffer[..bounded], cancellationToken);
        position += read;
        return read;
    }

    public override long Seek(long offset, SeekOrigin origin)
    {
        var next = origin switch
        {
            SeekOrigin.Begin => offset,
            SeekOrigin.Current => checked(position + offset),
            SeekOrigin.End => checked(length + offset),
            _ => throw new ArgumentOutOfRangeException(nameof(origin))
        };
        if (next < 0 || next > length) throw new IOException("Seek lies outside the ISO9660 extent.");
        stream.Position = checked(start + next);
        return position = next;
    }

    public override void Flush() { }
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

    protected override void Dispose(bool disposing)
    {
        if (disposing) stream.Dispose();
        base.Dispose(disposing);
    }

    public override async ValueTask DisposeAsync()
    {
        await stream.DisposeAsync();
        GC.SuppressFinalize(this);
    }
}
