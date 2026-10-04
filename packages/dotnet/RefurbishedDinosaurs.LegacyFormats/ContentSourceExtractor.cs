using System.Buffers;
using System.Globalization;
using System.IO.Hashing;
using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>What <see cref="ContentSourceExtractor.ExtractAsync"/> copies, where to, and within which bounds.</summary>
/// <param name="Prefix">
/// The directory under the root to copy into, accepted by <see cref="PortableAssetPath.Relative"/>,
/// or <see langword="null"/> or empty to copy into the root itself.
/// </param>
/// <param name="Include">Selects the files to copy, or <see langword="null"/> to copy every file.</param>
/// <param name="Conversion">
/// The conversion every record carries, or <see langword="null"/> for files copied byte for byte.
/// </param>
/// <param name="MaximumFiles">The most files the selection may hold.</param>
/// <param name="MaximumTotalBytes">The most bytes the selected files may hold together.</param>
public sealed record ContentExtractionOptions(
    string? Prefix = null,
    Func<ContentSourceEntry, bool>? Include = null,
    AssetConversion? Conversion = null,
    int MaximumFiles = 100_000,
    long MaximumTotalBytes = 8L * 1024 * 1024 * 1024)
{
    /// <summary>Every file into the root, 100,000 files and 8 GiB at most.</summary>
    public static ContentExtractionOptions Default { get; } = new();
}

/// <summary>Copies the files of an <see cref="OriginalContentSource"/> into a content directory.</summary>
public static class ContentSourceExtractor
{
    private const int BufferSize = 1024 * 1024;

    /// <summary>
    /// Copies the selected files of <paramref name="source"/> below
    /// <see cref="ContentExtractionOptions.Prefix"/> in <paramref name="root"/>, usually a
    /// <see cref="StagedAssetPack.StagingDirectory"/>, and returns an <see cref="InstalledAsset"/> for
    /// each, in the order of <see cref="OriginalContentSource.Files"/>.
    /// <para>
    /// Before writing anything it checks the selection against the limits in
    /// <paramref name="options"/>, and checks that the prefix directory is absent or empty, that each
    /// part of the prefix that already exists is spelled as it is on disk, and that neither the prefix
    /// directory nor a directory on the way to it is a link. Every path keeps its source spelling,
    /// except that a directory spelled two ways ignoring case is written once, with the spelling of the
    /// first file under it.
    /// </para>
    /// <para>
    /// Each file is hashed while it is copied, and must yield exactly the size the source lists for
    /// it. A record's <see cref="InstalledAsset.Path"/> is relative to <paramref name="root"/>, with
    /// the prefix and <c>/</c> separators, so the records can go into an
    /// <see cref="InstalledAssetManifest"/> for that root. Its
    /// <see cref="InstalledAsset.SourcePath"/> is the file's path in the source.
    /// </para>
    /// <para>
    /// When the call throws, including on cancellation, it removes what it wrote: the directories it
    /// created on the way to the prefix and the prefix directory, or, when the prefix directory
    /// already existed, everything inside it. A file it cannot remove stays, so after an
    /// exception discard a stage rather than committing it.
    /// </para>
    /// </summary>
    /// <param name="source">The source to copy from.</param>
    /// <param name="root">The content directory. It must exist.</param>
    /// <param name="options">What to copy, or <see langword="null"/> for <see cref="ContentExtractionOptions.Default"/>.</param>
    /// <param name="cancellationToken">Cancels between files and during each copy.</param>
    /// <returns>One record for each copied file; empty when the selection is empty.</returns>
    /// <exception cref="ArgumentException"><paramref name="root"/> is null or blank.</exception>
    /// <exception cref="ArgumentOutOfRangeException">A limit is negative.</exception>
    /// <exception cref="DirectoryNotFoundException"><paramref name="root"/> does not exist.</exception>
    /// <exception cref="IOException">
    /// The prefix names a file or a directory that is not empty, or spells an existing entry with
    /// different case. Other I/O failures while copying also surface as <see cref="IOException"/>.
    /// </exception>
    /// <exception cref="InvalidDataException">
    /// The prefix or a source path is not accepted by <see cref="PortableAssetPath.Relative"/>, the root
    /// or a directory on the way to the prefix is a link, the selection exceeds a limit, a file's path
    /// is also a directory of another file ignoring case, or a file yields more or fewer bytes than the
    /// source lists.
    /// </exception>
    /// <exception cref="OperationCanceledException"><paramref name="cancellationToken"/> was cancelled.</exception>
    public static async Task<IReadOnlyList<InstalledAsset>> ExtractAsync(
        OriginalContentSource source,
        string root,
        ContentExtractionOptions? options = null,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(source);
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        options ??= ContentExtractionOptions.Default;
        ArgumentOutOfRangeException.ThrowIfNegative(options.MaximumFiles);
        ArgumentOutOfRangeException.ThrowIfNegative(options.MaximumTotalBytes);

        var fullRoot = Path.TrimEndingDirectorySeparator(Path.GetFullPath(root));
        if (!Directory.Exists(fullRoot)) throw new DirectoryNotFoundException($"Content root does not exist: {fullRoot}");
        if ((File.GetAttributes(fullRoot) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException("Content root is a symbolic link or reparse point.");
        var prefix = string.IsNullOrEmpty(options.Prefix) ? string.Empty : PortableAssetPath.Relative(options.Prefix);
        var (destination, firstCreated) = CheckDestination(fullRoot, prefix);

        var selected = Select(source, options);
        var plan = Plan(selected, destination, prefix);
        cancellationToken.ThrowIfCancellationRequested();

        try
        {
            Directory.CreateDirectory(destination);
            var records = new InstalledAsset[plan.Count];
            for (var index = 0; index < plan.Count; index++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var (entry, target, recordPath) = plan[index];
                var hash = await CopyAsync(source, entry, target, cancellationToken).ConfigureAwait(false);
                records[index] = new InstalledAsset(recordPath, entry.Size, hash, entry.Path, Conversion: options.Conversion);
            }
            return records;
        }
        catch
        {
            RemoveWritten(destination, firstCreated);
            throw;
        }
    }

    // Returns the full path of the prefix directory once it is known to be absent or empty, with no
    // link on the way to it, and the first directory on the way that does not exist yet, if any.
    private static (string Destination, string? FirstCreated) CheckDestination(string fullRoot, string prefix)
    {
        if (prefix.Length == 0)
        {
            if (Directory.EnumerateFileSystemEntries(fullRoot).Any())
                throw new IOException($"Content root is not empty: {fullRoot}");
            return (fullRoot, null);
        }
        var destination = SafePath.Below(fullRoot, prefix);
        var current = fullRoot;
        foreach (var part in prefix.Split('/'))
        {
            // A part spelled differently from an existing entry would reuse that entry on Windows and
            // create a second one beside it on a case-sensitive file system, so it is refused on both.
            var existing = Directory.EnumerateFileSystemEntries(current)
                .Select(Path.GetFileName)
                .FirstOrDefault(name => string.Equals(name, part, StringComparison.OrdinalIgnoreCase));
            if (existing is not null && existing != part)
                throw new IOException($"Extraction prefix spells {existing} as {part}: {prefix}");
            current = Path.Combine(current, part);
            if (File.Exists(current)) throw new IOException($"Extraction prefix names a file: {prefix}");
            if (!Directory.Exists(current)) return (destination, current);
            if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException($"Extraction prefix passes through a link: {prefix}");
        }
        if (Directory.EnumerateFileSystemEntries(destination).Any())
            throw new IOException($"Extraction prefix is not empty: {prefix}");
        return (destination, null);
    }

    private static List<ContentSourceEntry> Select(OriginalContentSource source, ContentExtractionOptions options)
    {
        var selected = new List<ContentSourceEntry>();
        long total = 0;
        foreach (var entry in source.Files)
        {
            if (options.Include is not null && !options.Include(entry)) continue;
            selected.Add(entry);
            if (selected.Count > options.MaximumFiles)
                throw new InvalidDataException($"The selection holds more than {options.MaximumFiles} files.");
            total = checked(total + entry.Size);
            if (total > options.MaximumTotalBytes)
                throw new InvalidDataException($"The selection holds more than {options.MaximumTotalBytes} bytes.");
        }
        return selected;
    }

    // Gives each file its target before anything is written. A directory gets the spelling of the
    // first file under it, so two spellings ignoring case do not become two directories on a
    // case-sensitive file system.
    private static List<(ContentSourceEntry Entry, string Target, string RecordPath)> Plan(
        List<ContentSourceEntry> selected, string destination, string prefix)
    {
        var directories = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var files = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var plan = new List<(ContentSourceEntry, string, string)>(selected.Count);
        foreach (var entry in selected)
        {
            var parts = PortableAssetPath.Relative(entry.Path).Split('/');
            var spelled = string.Empty;
            for (var index = 0; index < parts.Length - 1; index++)
            {
                var next = spelled.Length == 0 ? parts[index] : $"{spelled}/{parts[index]}";
                if (files.Contains(next))
                    throw new InvalidDataException($"Source path is also a directory of another file: {next}");
                if (!directories.TryGetValue(next, out var existing)) directories.Add(next, existing = next);
                spelled = existing;
            }
            var relative = spelled.Length == 0 ? parts[^1] : $"{spelled}/{parts[^1]}";
            if (directories.ContainsKey(relative))
                throw new InvalidDataException($"Source path is also a directory of another file: {relative}");
            if (!files.Add(relative))
                throw new InvalidDataException($"Source lists a path twice, ignoring case: {relative}");
            var recordPath = prefix.Length == 0 ? relative : $"{prefix}/{relative}";
            plan.Add((entry, SafePath.Below(destination, relative), recordPath));
        }
        return plan;
    }

    private static async Task<string> CopyAsync(
        OriginalContentSource source, ContentSourceEntry entry, string target, CancellationToken cancellationToken)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(target)!);
        var hash = new XxHash128();
        long total = 0;
        var buffer = ArrayPool<byte>.Shared.Rent(BufferSize);
        try
        {
            await using var input = source.OpenRead(entry.Path);
            await using var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None,
                bufferSize: 0, FileOptions.Asynchronous);
            int read;
            while ((read = await input.ReadAsync(buffer.AsMemory(0, BufferSize), cancellationToken)
                       .ConfigureAwait(false)) > 0)
            {
                total += read;
                if (total > entry.Size)
                    throw new InvalidDataException(
                        $"{entry.Path} yields more than the {entry.Size} bytes the source lists.");
                hash.Append(buffer.AsSpan(0, read));
                await output.WriteAsync(buffer.AsMemory(0, read), cancellationToken).ConfigureAwait(false);
            }
            await output.FlushAsync(cancellationToken).ConfigureAwait(false);
            output.Flush(flushToDisk: true);
        }
        finally { ArrayPool<byte>.Shared.Return(buffer); }
        if (total != entry.Size)
            throw new InvalidDataException($"{entry.Path} yields {total} bytes; the source lists {entry.Size}.");
        return hash.GetCurrentHashAsUInt128().ToString("x32", CultureInfo.InvariantCulture);
    }

    // The destination was absent or empty when the call started, so everything in it, and in the first
    // directory the call created on the way to it, was written by the call. A failure to remove it
    // must not hide the exception that ended the extraction.
    private static void RemoveWritten(string destination, string? firstCreated)
    {
        try
        {
            if (firstCreated is not null)
            {
                if (Directory.Exists(firstCreated)) Directory.Delete(firstCreated, recursive: true);
                return;
            }
            if (!Directory.Exists(destination)) return;
            foreach (var directory in Directory.EnumerateDirectories(destination))
                Directory.Delete(directory, recursive: true);
            foreach (var file in Directory.EnumerateFiles(destination))
                File.Delete(file);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
}
