using System.IO.Compression;
using System.IO.Enumeration;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.Core.Assets;

/// <summary>
/// A set of replacement and added files, such as an official patch or a fix delivered as files, that
/// is applied to a content directory only where each target holds the bytes the overlay expects.
/// An overlay is a zip archive or a directory holding <see cref="ManifestFileName"/> and one payload
/// for each record under <see cref="PayloadDirectory"/>.
/// </summary>
public sealed class ContentOverlay : IDisposable
{
    /// <summary>The manifest's name at the root of the overlay.</summary>
    public const string ManifestFileName = "overlay.json";

    /// <summary>The directory of the overlay that holds the payloads, at each record's path.</summary>
    public const string PayloadDirectory = "files";

    private readonly Dictionary<string, Func<Stream>> _payloads;
    private readonly IDisposable? _archive;

    private ContentOverlay(ContentOverlayManifest manifest, Dictionary<string, Func<Stream>> payloads, IDisposable? archive)
    {
        Manifest = manifest;
        _payloads = payloads;
        _archive = archive;
    }

    /// <summary>The overlay's validated manifest.</summary>
    public ContentOverlayManifest Manifest { get; }

    /// <summary>
    /// Opens an overlay zip archive. Every entry under <see cref="PayloadDirectory"/> that is not a
    /// directory must be the payload of exactly one record, matched ignoring case, with the record's
    /// size; entries outside it other than the manifest are ignored.
    /// </summary>
    /// <param name="path">The zip archive.</param>
    /// <param name="limits">The bounds to check, or <see langword="null"/> for <see cref="ContentOverlayLimits.Default"/>.</param>
    /// <exception cref="ArgumentException"><paramref name="path"/> is null or blank.</exception>
    /// <exception cref="InvalidDataException">
    /// The archive is not a zip, the manifest is missing or invalid, or a payload is missing, unlisted,
    /// listed twice or of another size than its record.
    /// </exception>
    public static ContentOverlay OpenZip(string path, ContentOverlayLimits? limits = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        limits ??= ContentOverlayLimits.Default;
        ZipArchive archive;
        try { archive = ZipFile.OpenRead(path); }
        catch (InvalidDataException exception)
        {
            throw new InvalidDataException($"Overlay is not a zip archive: {exception.Message}", exception);
        }
        try
        {
            var manifestEntry = archive.GetEntry(ManifestFileName)
                ?? throw new InvalidDataException($"Overlay has no {ManifestFileName}.");
            if (manifestEntry.Length > limits.MaximumManifestBytes)
                throw new InvalidDataException($"Overlay manifest is larger than {limits.MaximumManifestBytes} bytes.");
            byte[] json;
            using (var stream = manifestEntry.Open()) json = ReadBounded(stream, limits.MaximumManifestBytes);
            var manifest = ContentOverlayManifest.Parse(json, limits);

            var records = Records(manifest);
            var payloads = new Dictionary<string, Func<Stream>>(StringComparer.OrdinalIgnoreCase);
            foreach (var entry in archive.Entries)
            {
                var name = entry.FullName.Replace('\\', '/');
                if (!name.StartsWith(PayloadDirectory + "/", StringComparison.Ordinal) || name.EndsWith('/')) continue;
                var record = Payload(records, name[(PayloadDirectory.Length + 1)..], payloads);
                if (entry.Length != record.Bytes)
                    throw new InvalidDataException($"Overlay payload is {entry.Length} bytes; its record gives {record.Bytes}: {record.Path}");
                payloads.Add(record.Path, entry.Open);
            }
            return new(manifest, RequireAll(records, payloads), archive);
        }
        catch
        {
            archive.Dispose();
            throw;
        }
    }

    /// <summary>
    /// Opens an overlay laid out as a directory. Every file under <see cref="PayloadDirectory"/> must
    /// be the payload of exactly one record, matched ignoring case, with the record's size. Links are
    /// rejected, including a linked manifest or payload directory; other entries outside it are ignored.
    /// </summary>
    /// <param name="path">The overlay directory.</param>
    /// <param name="limits">The bounds to check, or <see langword="null"/> for <see cref="ContentOverlayLimits.Default"/>.</param>
    /// <exception cref="ArgumentException"><paramref name="path"/> is null or blank.</exception>
    /// <exception cref="InvalidDataException">
    /// The manifest is missing or invalid, or a payload is missing, unlisted, listed twice, linked or
    /// of another size than its record.
    /// </exception>
    public static ContentOverlay OpenDirectory(string path, ContentOverlayLimits? limits = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        limits ??= ContentOverlayLimits.Default;
        var root = Path.GetFullPath(path);
        var manifestPath = Path.Combine(root, ManifestFileName);
        if (!File.Exists(manifestPath)) throw new InvalidDataException($"Overlay has no {ManifestFileName}.");
        if ((File.GetAttributes(manifestPath) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException($"Overlay {ManifestFileName} is a link.");
        byte[] json;
        using (var stream = File.OpenRead(manifestPath)) json = ReadBounded(stream, limits.MaximumManifestBytes);
        var manifest = ContentOverlayManifest.Parse(json, limits);

        var records = Records(manifest);
        var payloads = new Dictionary<string, Func<Stream>>(StringComparer.OrdinalIgnoreCase);
        var payloadRoot = Path.Combine(root, PayloadDirectory);
        if (Directory.Exists(payloadRoot))
        {
            if ((File.GetAttributes(payloadRoot) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException($"Overlay {PayloadDirectory} directory is a link.");
            var entries = new FileSystemEnumerable<(string Path, FileAttributes Attributes, bool IsDirectory, long Length)>(
                payloadRoot,
                (ref FileSystemEntry entry) => (entry.ToFullPath(), entry.Attributes, entry.IsDirectory, entry.Length),
                new EnumerationOptions { AttributesToSkip = 0, IgnoreInaccessible = false, RecurseSubdirectories = true });
            foreach (var entry in entries)
            {
                var relative = Path.GetRelativePath(payloadRoot, entry.Path).Replace('\\', '/');
                if ((entry.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException($"Overlay payload is a link: {relative}");
                if (entry.IsDirectory) continue;
                var record = Payload(records, relative, payloads);
                if (entry.Length != record.Bytes)
                    throw new InvalidDataException($"Overlay payload is {entry.Length} bytes; its record gives {record.Bytes}: {record.Path}");
                var full = entry.Path;
                payloads.Add(record.Path, () => new FileStream(full, FileMode.Open, FileAccess.Read, FileShare.Read,
                    bufferSize: 0, FileOptions.Asynchronous | FileOptions.SequentialScan));
            }
        }
        return new(manifest, RequireAll(records, payloads), null);
    }

    /// <summary>
    /// Applies the overlay to the content directory <paramref name="root"/>, usually a
    /// <see cref="StagedAssetPack.StagingDirectory"/>, in ordinal path order.
    /// <para>
    /// First every target is checked, finding each path component ignoring case. A target that
    /// already has the record's size and <see cref="ContentOverlayFile.Xxh3"/> is already applied and
    /// is not written. Otherwise a target with a <see langword="null"/>
    /// <see cref="ContentOverlayFile.BaseXxh3"/> must not exist, and any other target must exist and
    /// have that fingerprint. Then every payload is copied into a scratch directory under
    /// <paramref name="root"/> and hashed as it is copied; a payload must have its record's size and
    /// fingerprint. Only when every target and payload checks out are the copies moved over their
    /// targets. Nothing is written when a check fails, and the scratch directory is always removed.
    /// </para>
    /// <para>
    /// The moves are not one transaction: if one fails, the targets moved before it keep their new
    /// bytes. Discard the staging directory after any exception. Nothing guards against another
    /// process changing <paramref name="root"/> during the call.
    /// </para>
    /// </summary>
    /// <param name="root">The content directory to apply the overlay to.</param>
    /// <param name="cancellationToken">Cancels the checks and the copies. It is not observed once the moves start.</param>
    /// <returns>Every record's outcome, with the target's actual spelling.</returns>
    /// <exception cref="ArgumentException"><paramref name="root"/> is null or blank.</exception>
    /// <exception cref="DirectoryNotFoundException"><paramref name="root"/> does not exist.</exception>
    /// <exception cref="ContentOverlayException">A target or payload does not match its record.</exception>
    /// <exception cref="InvalidDataException">
    /// A target path is ambiguous ignoring case, is a directory, passes through a file or a link.
    /// </exception>
    public async Task<ContentOverlayResult> ApplyAsync(string root, CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        var fullRoot = Path.TrimEndingDirectorySeparator(Path.GetFullPath(root));
        if (!Directory.Exists(fullRoot)) throw new DirectoryNotFoundException($"Content root does not exist: {fullRoot}");
        if ((File.GetAttributes(fullRoot) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException("Content root is a symbolic link or reparse point.");

        var plan = new List<(ContentOverlayFile Record, string Target, ContentOverlayOutput Output)>();
        var locator = new TargetLocator(fullRoot);
        foreach (var record in Manifest.Files.OrderBy(file => file.Path, StringComparer.Ordinal))
        {
            cancellationToken.ThrowIfCancellationRequested();
            var (relative, exists) = locator.Locate(PortableAssetPath.Relative(record.Path));
            var target = SafePath.Below(fullRoot, relative);
            ContentOverlayAction action;
            if (exists)
            {
                var length = new FileInfo(target).Length;
                var found = await FileFingerprint.Xxh3Async(target, cancellationToken).ConfigureAwait(false);
                if (length == record.Bytes && found == record.Xxh3) action = ContentOverlayAction.AlreadyApplied;
                else if (record.BaseXxh3 is null)
                    throw new ContentOverlayException(ContentOverlayProblem.TargetExists, relative, found,
                        $"The overlay adds {relative}, but a file with xxh3 {found} is already there.");
                else if (found != record.BaseXxh3)
                    throw new ContentOverlayException(ContentOverlayProblem.TargetChanged, relative, found,
                        $"Expected {relative} to have xxh3 {record.BaseXxh3} or {record.Xxh3}; found {found}.");
                else action = ContentOverlayAction.Replaced;
            }
            else if (record.BaseXxh3 is null) action = ContentOverlayAction.Added;
            else
                throw new ContentOverlayException(ContentOverlayProblem.TargetMissing, relative, null,
                    $"The overlay replaces {relative}, which does not exist.");
            plan.Add((record, target, new(relative, record.Bytes, record.Xxh3, record.BaseXxh3, action)));
        }

        var pending = plan.Where(step => step.Output.Action != ContentOverlayAction.AlreadyApplied).ToArray();
        if (pending.Length > 0)
        {
            var scratch = Path.Combine(fullRoot, $".overlay-{Guid.NewGuid():N}");
            Directory.CreateDirectory(scratch);
            var moved = false;
            try
            {
                var copies = new string[pending.Length];
                for (var index = 0; index < pending.Length; index++)
                {
                    copies[index] = Path.Combine(scratch, index.ToString(System.Globalization.CultureInfo.InvariantCulture));
                    await CopyVerifiedAsync(pending[index].Record, pending[index].Output.Path, copies[index],
                        cancellationToken).ConfigureAwait(false);
                }
                cancellationToken.ThrowIfCancellationRequested();
                for (var index = 0; index < pending.Length; index++)
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(pending[index].Target)!);
                    File.Move(copies[index], pending[index].Target, overwrite: true);
                }
                moved = true;
            }
            finally
            {
                // A failure to remove the scratch directory must not hide the exception that ended
                // the apply. After a successful apply it still throws, since the directory would
                // otherwise stay in the content.
                try { Directory.Delete(scratch, recursive: true); }
                catch (DirectoryNotFoundException) { }
                catch (Exception) when (!moved) { }
            }
        }
        return new(Manifest.Name, plan.Select(step => step.Output).ToArray());
    }

    /// <summary>Closes the overlay's archive.</summary>
    public void Dispose() => _archive?.Dispose();

    private async Task CopyVerifiedAsync(ContentOverlayFile record, string relative, string copy,
        CancellationToken cancellationToken)
    {
        FingerprintedCopy copied;
        await using (var source = _payloads[record.Path]())
        await using (var output = new FileStream(copy, FileMode.CreateNew, FileAccess.Write, FileShare.None,
                         bufferSize: 0, FileOptions.Asynchronous))
        {
            copied = await FileFingerprint.CopyXxh3Async(source, output, record.Bytes, cancellationToken)
                .ConfigureAwait(false);
            await output.FlushAsync(cancellationToken).ConfigureAwait(false);
            output.Flush(flushToDisk: true);
        }

        if (copied.Exceeded || copied.Bytes != record.Bytes)
            throw new ContentOverlayException(ContentOverlayProblem.PayloadWrongSize, relative, null,
                copied.Exceeded
                    ? $"The payload for {relative} is larger than the {record.Bytes} bytes its record gives."
                    : $"The payload for {relative} is {copied.Bytes} bytes; its record gives {record.Bytes}.");
        var found = copied.Xxh3;
        if (found != record.Xxh3)
            throw new ContentOverlayException(ContentOverlayProblem.PayloadWrongHash, relative, found,
                $"The payload for {relative} has xxh3 {found}; its record gives {record.Xxh3}.");
    }

    /// <summary>
    /// Finds targets under a content root with the rules of <see cref="PortableAssetPath.ResolveFile"/>,
    /// through one <see cref="AssetPathWalker"/> that lists each directory once per apply. A directory
    /// the overlay creates gets one spelling for every record under it, so records that spell a new
    /// directory differently do not create two directories on a case-sensitive file system.
    /// </summary>
    private sealed class TargetLocator(string root)
    {
        private readonly AssetPathWalker _walker = new(root, cacheListings: true);
        private readonly PortablePathLayout _planned = new();

        /// <summary>
        /// Returns the actual spelling of the components of <paramref name="relative"/> that exist,
        /// followed by the spelling of the rest (the first record's spelling for a directory the
        /// overlay creates), and whether the whole path names an existing file.
        /// </summary>
        public (string Relative, bool Exists) Locate(string relative)
        {
            var parts = relative.Split('/');
            IReadOnlyList<string> spelled;
            bool isDirectory;
            try
            {
                (spelled, isDirectory) = _walker.Walk(parts);
            }
            catch (InvalidDataException exception)
            {
                throw new InvalidDataException($"Overlay target {relative} is rejected: {exception.Message}", exception);
            }
            var existing = string.Join('/', spelled);
            if (spelled.Count < parts.Length) return (Planned(existing, parts[spelled.Count..]), false);
            if (isDirectory) throw new InvalidDataException($"Overlay target is a directory: {existing}");
            return (existing, true);
        }

        private string Planned(string existing, string[] rest) =>
            _planned.Add(existing.Length == 0 ? string.Join('/', rest) : $"{existing}/{string.Join('/', rest)}");
    }

    private static Dictionary<string, ContentOverlayFile> Records(ContentOverlayManifest manifest) =>
        manifest.Files.ToDictionary(file => PortableAssetPath.Relative(file.Path), StringComparer.OrdinalIgnoreCase);

    private static ContentOverlayFile Payload(Dictionary<string, ContentOverlayFile> records, string relative,
        Dictionary<string, Func<Stream>> payloads)
    {
        var path = PortableAssetPath.Relative(relative);
        if (!records.TryGetValue(path, out var record))
            throw new InvalidDataException($"Overlay payload has no record: {path}");
        if (payloads.ContainsKey(record.Path))
            throw new InvalidDataException($"Overlay payload appears twice: {path}");
        return record;
    }

    private static Dictionary<string, Func<Stream>> RequireAll(Dictionary<string, ContentOverlayFile> records,
        Dictionary<string, Func<Stream>> payloads)
    {
        var missing = records.Values.FirstOrDefault(record => !payloads.ContainsKey(record.Path));
        return missing is null ? payloads : throw new InvalidDataException($"Overlay payload is missing: {missing.Path}");
    }

    private static byte[] ReadBounded(Stream stream, int maximumBytes)
    {
        using var copy = new MemoryStream();
        var chunk = new byte[81920];
        int read;
        while ((read = stream.Read(chunk)) > 0)
        {
            if (copy.Length + read > maximumBytes)
                throw new InvalidDataException($"Overlay manifest is larger than {maximumBytes} bytes.");
            copy.Write(chunk, 0, read);
        }
        return copy.ToArray();
    }
}

/// <summary>What <see cref="ContentOverlay.ApplyAsync"/> did with one record.</summary>
public enum ContentOverlayAction
{
    /// <summary>The target did not exist and was written.</summary>
    Added,
    /// <summary>The target had the record's base fingerprint and was replaced.</summary>
    Replaced,
    /// <summary>The target already had the record's size and fingerprint and was not written.</summary>
    AlreadyApplied
}

/// <summary>One record's outcome.</summary>
/// <param name="Path">The target relative to the content root, with its actual spelling and <c>/</c> separators.</param>
/// <param name="Bytes">The target's size.</param>
/// <param name="Xxh3">The target's XXH3-128 fingerprint.</param>
/// <param name="BaseXxh3">The fingerprint the record expected before, or <see langword="null"/> for an added file.</param>
/// <param name="Action">What was done.</param>
public sealed record ContentOverlayOutput(string Path, long Bytes, string Xxh3, string? BaseXxh3, ContentOverlayAction Action);

/// <summary>The outcome of <see cref="ContentOverlay.ApplyAsync"/>.</summary>
/// <param name="Name">The overlay's <see cref="ContentOverlayManifest.Name"/>.</param>
/// <param name="Outputs">Every record's outcome, in ordinal path order.</param>
public sealed record ContentOverlayResult(string Name, IReadOnlyList<ContentOverlayOutput> Outputs)
{
    /// <summary>How many targets were written.</summary>
    public int Written => Outputs.Count(output => output.Action != ContentOverlayAction.AlreadyApplied);

    /// <summary>
    /// Returns <paramref name="files"/> with the overlay's outputs in it, for an
    /// <see cref="InstalledAssetManifest"/>. Each record's path goes through
    /// <see cref="PortableAssetPath.Relative"/>, the check <see cref="InstalledAssetVerifier"/> applies,
    /// and a record no output matches is returned under that path, so a <c>\</c> separator becomes
    /// <c>/</c>. A record whose path matches an output ignoring case is replaced, keeping its media
    /// type; outputs no record matches are appended as <c>application/octet-stream</c>. Each output's
    /// record takes the output's path, size and fingerprint, the output's path as its source path,
    /// and an <see cref="AssetConversion"/> whose method is <see cref="Name"/>.
    /// </summary>
    /// <param name="files">The records the import wrote for the content before the overlay.</param>
    /// <exception cref="InvalidDataException">
    /// A record in <paramref name="files"/> is null, its path is not accepted by
    /// <see cref="PortableAssetPath.Relative"/>, or two records name the same path ignoring case and
    /// separators. For a duplicate, the message names both spellings.
    /// </exception>
    public IReadOnlyList<InstalledAsset> UpdateInstalledFiles(IEnumerable<InstalledAsset> files)
    {
        ArgumentNullException.ThrowIfNull(files);
        var outputs = Outputs.ToDictionary(output => output.Path, StringComparer.OrdinalIgnoreCase);
        // Each normalized record path, mapped to the spelling the record gave it.
        var listed = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var updated = new List<InstalledAsset>();
        foreach (var file in files)
        {
            if (file is null) throw new InvalidDataException("Installed files contain a null record.");
            var path = PortableAssetPath.Relative(file.Path);
            if (!listed.TryAdd(path, file.Path))
                throw new InvalidDataException(
                    $"Installed files list one path twice: '{listed[path]}' and '{file.Path}'.");
            updated.Add(outputs.TryGetValue(path, out var output)
                ? Record(output, file.MediaType)
                : file with { Path = path });
        }
        updated.AddRange(Outputs.Where(output => !listed.ContainsKey(output.Path))
            .Select(output => Record(output, "application/octet-stream")));
        return updated;
    }

    private InstalledAsset Record(ContentOverlayOutput output, string mediaType) =>
        new(output.Path, output.Bytes, output.Xxh3, output.Path, mediaType, new AssetConversion(Name));
}

/// <summary>Why a content overlay was not applied.</summary>
public enum ContentOverlayProblem
{
    /// <summary>The overlay replaces a file that does not exist.</summary>
    TargetMissing,
    /// <summary>The overlay adds a file, and a file with other contents is already there.</summary>
    TargetExists,
    /// <summary>The target has neither the record's base fingerprint nor its patched one.</summary>
    TargetChanged,
    /// <summary>A payload's size differs from its record.</summary>
    PayloadWrongSize,
    /// <summary>A payload's fingerprint differs from its record.</summary>
    PayloadWrongHash
}

/// <summary>A target or payload did not match its overlay record, so nothing was written.</summary>
public sealed class ContentOverlayException : Exception
{
    /// <summary>Creates the exception.</summary>
    /// <param name="problem">What did not match.</param>
    /// <param name="path">The record's target, relative to the content root.</param>
    /// <param name="foundXxh3">The fingerprint found, or <see langword="null"/> when nothing was hashed.</param>
    /// <param name="message">One sentence describing it.</param>
    public ContentOverlayException(ContentOverlayProblem problem, string path, string? foundXxh3, string message)
        : base(message)
    {
        Problem = problem;
        Path = path;
        FoundXxh3 = foundXxh3;
    }

    /// <summary>What did not match.</summary>
    public ContentOverlayProblem Problem { get; }

    /// <summary>The record's target, relative to the content root, with <c>/</c> separators.</summary>
    public string Path { get; }

    /// <summary>
    /// The fingerprint found on the target or payload, or <see langword="null"/> for a missing target
    /// or a payload of the wrong size.
    /// </summary>
    public string? FoundXxh3 { get; }
}
