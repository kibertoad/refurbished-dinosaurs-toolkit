using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Text;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

// Where a cabinet's files stored outside its volumes are looked up: the folder that holds the header,
// at each file's directory and name, which is where Unshield's -O looks.
internal abstract class InstallShieldOutsideStore
{
    // How many files match the path ignoring case, and the length of the file when exactly one does.
    // Null when the lookup failed before it could tell.
    public abstract (int Matches, long Length)? Find(string relativePath);

    // Opens the one file at the path. A file now longer than storedSize, the length it had when the
    // set was opened, is refused, since only that many bytes would be read and checked.
    public abstract Stream Open(string relativePath, long storedSize);

    private protected static InvalidDataException Grown(string relativePath) => new(
        $"InstallShield file stored outside the cabinet at '{relativePath}' is longer than when the set was opened.");
}

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
    public static InstallShieldCabinetSource FromDirectory(
        string path, InstallShieldCabinetLimits limits, InstallShieldCompressedFormat compressedFormat)
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
        }, new DirectoryOutsideStore(directory), limits, compressedFormat);
    }

    public static InstallShieldCabinetSource FromSource(
        OriginalContentSource container, string headerPath, InstallShieldCabinetLimits limits,
        InstallShieldCompressedFormat compressedFormat)
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
        }, new ContainerOutsideStore(container, directory), limits, compressedFormat);
    }

    // The folder of the file system that holds the header. Each component of a path is matched
    // ignoring case, as volumes are, so a case-sensitive file system can hold several matches.
    private sealed class DirectoryOutsideStore(string root) : InstallShieldOutsideStore
    {
        // Each folder's entries by name ignoring case, so a lookup costs one probe per component
        // however many files the folder holds.
        private readonly Dictionary<string, ILookup<string, string>> listings = new(StringComparer.Ordinal);

        // A folder that cannot be listed, or a file whose length cannot be read, leaves the lookup
        // unanswered. It is not taken to mean the file is missing.
        public override (int Matches, long Length)? Find(string relativePath)
        {
            try
            {
                var matches = Matches(relativePath);
                return (matches.Count, matches.Count == 1 ? new FileInfo(matches[0]).Length : 0);
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
            {
                return null;
            }
        }

        public override Stream Open(string relativePath, long storedSize)
        {
            var matches = Matches(relativePath);
            if (matches.Count != 1)
                throw new FileNotFoundException(matches.Count == 0
                    ? "InstallShield file stored outside the cabinet is no longer beside the header."
                    : "Several files beside the header now match the path of an InstallShield file stored outside the cabinet.",
                    relativePath);
            var stream = new FileStream(matches[0], FileMode.Open, FileAccess.Read, FileShare.Read);
            if (stream.Length <= storedSize) return stream;
            stream.Dispose();
            throw Grown(relativePath);
        }

        private List<string> Matches(string relativePath)
        {
            var components = relativePath.Split('/');
            List<string> current = [root];
            for (var index = 0; index < components.Length && current.Count > 0; index++)
            {
                var last = index == components.Length - 1;
                current = current.SelectMany(folder => Listing(folder)[components[index]])
                    .Where(entry => last ? File.Exists(entry) : Directory.Exists(entry))
                    .ToList();
            }
            return current;
        }

        private ILookup<string, string> Listing(string folder)
        {
            lock (listings)
            {
                if (!listings.TryGetValue(folder, out var entries))
                    listings.Add(folder, entries = (Directory.Exists(folder) ? Directory.EnumerateFileSystemEntries(folder) : [])
                        .ToLookup(entry => Path.GetFileName(entry), StringComparer.OrdinalIgnoreCase));
                return entries;
            }
        }
    }

    // The folder of another source that holds the header. The source matches paths ignoring case.
    private sealed class ContainerOutsideStore(OriginalContentSource container, string directory) : InstallShieldOutsideStore
    {
        public override (int Matches, long Length)? Find(string relativePath) =>
            container.TryGetFile(directory + relativePath, out var entry) ? (1, entry!.Size) : (0, 0);

        public override Stream Open(string relativePath, long storedSize) =>
            !container.TryGetFile(directory + relativePath, out var entry)
                ? throw new FileNotFoundException(
                    "InstallShield file stored outside the cabinet is no longer beside the header in the source.", directory + relativePath)
                : entry!.Size > storedSize ? throw Grown(directory + relativePath)
                : container.OpenRead(directory + relativePath);
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
