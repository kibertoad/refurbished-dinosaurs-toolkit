namespace RefurbishedDinosaurs.Core.IO;

/// <summary>Interprets legacy asset references consistently on Windows, Linux and macOS.</summary>
public static class PortableAssetPath
{
    private static readonly string[] DeviceNames =
    [
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "COM¹", "COM²", "COM³",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9", "LPT¹", "LPT²", "LPT³"
    ];

    /// <summary>
    /// Normalizes separators and rejects roots, drive-relative names, traversal, empty components, and
    /// components Windows would read differently: a trailing dot or space, or a reserved device name.
    /// </summary>
    /// <exception cref="InvalidDataException">
    /// <paramref name="reference"/> is null, blank or not a portable relative path.
    /// </exception>
    public static string Relative(string reference)
    {
        var normalized = Present(reference).Replace('\\', '/');
        if (normalized.StartsWith('/') || normalized.Contains(':') || normalized.Contains('\0') ||
            !normalized.Split('/').All(IsPortableComponent))
            throw new InvalidDataException($"Asset reference must contain only relative path components: '{reference}'.");
        return normalized;
    }

    /// <summary>
    /// Removes an explicit ASCII drive and rooted separator from a legacy installation reference.
    /// UNC references and drive-relative names are rejected. The caller opts into discarding a drive.
    /// </summary>
    /// <exception cref="InvalidDataException">
    /// <paramref name="reference"/> is null, blank, only a drive root, or not portable once the drive is removed.
    /// </exception>
    public static string WithoutDriveRoot(string reference)
    {
        var normalized = Present(reference).Replace('\\', '/');
        if (normalized.Length >= 3 && char.IsAsciiLetter(normalized[0]) &&
            normalized[1] == ':' && normalized[2] == '/')
            normalized = normalized[3..];
        if (normalized.Length == 0) throw new InvalidDataException("Asset reference is only a drive root.");
        return Relative(normalized);
    }

    /// <summary>
    /// Resolves an existing file component by component, ignoring ordinal case. Multiple matching
    /// names are rejected even when one matches exactly. Symbolic links and reparse points are
    /// rejected, including the root. Returns the actual relative spelling with forward slashes.
    /// The check reads the tree once; a directory replaced after it is not detected.
    /// </summary>
    /// <param name="root">The directory the reference is relative to.</param>
    /// <param name="relative">The reference, checked with <see cref="Relative"/>.</param>
    /// <returns>The file's path below <paramref name="root"/> as the file system spells it.</returns>
    /// <exception cref="ArgumentException"><paramref name="root"/> is null or blank.</exception>
    /// <exception cref="InvalidDataException">
    /// The root is not a directory, or the reference is unsafe, missing, ambiguous, linked or not a file.
    /// </exception>
    public static string ResolveFile(string root, string relative) => Resolve(root, relative, directory: false);

    /// <summary>
    /// Resolves an existing directory with the rules of <see cref="ResolveFile"/>: each component
    /// matches exactly one entry ignoring ordinal case, even when one of the matches is spelled
    /// exactly, and symbolic links and reparse points are rejected, including the root. Returns the
    /// actual relative spelling with forward slashes. The check reads the tree once; a directory
    /// replaced after it is not detected.
    /// </summary>
    /// <param name="root">The directory the reference is relative to.</param>
    /// <param name="relative">The reference, checked with <see cref="Relative"/>.</param>
    /// <returns>The directory's path below <paramref name="root"/> as the file system spells it.</returns>
    /// <exception cref="ArgumentException"><paramref name="root"/> is null or blank.</exception>
    /// <exception cref="InvalidDataException">
    /// The root is not a directory, or the reference is unsafe, missing, ambiguous, linked or not a directory.
    /// </exception>
    public static string ResolveDirectory(string root, string relative) => Resolve(root, relative, directory: true);

    private static string Resolve(string root, string relative, bool directory)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        var parts = Relative(relative).Split('/');
        var rootInfo = new DirectoryInfo(Path.GetFullPath(root));
        if (!rootInfo.Exists) throw new InvalidDataException("Asset root directory does not exist.");
        if ((rootInfo.Attributes & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException("Asset root is a symbolic link or reparse point.");
        var (spelled, isDirectory) = new AssetPathWalker(rootInfo.FullName, cacheListings: false).Walk(parts);
        if (spelled.Count < parts.Length)
            throw new InvalidDataException($"Asset does not exist: {AssetPathWalker.Shown(spelled, parts[spelled.Count])}");
        var path = string.Join('/', spelled);
        if (isDirectory != directory)
            throw new InvalidDataException(directory ? $"Asset reference is not a directory: {path}" : $"Asset reference is not a file: {path}");
        return path;
    }

    private static string Present(string reference) => string.IsNullOrWhiteSpace(reference)
        ? throw new InvalidDataException("Asset reference is blank.")
        : reference;

    private static bool IsPortableComponent(string part)
    {
        if (part.Length == 0 || part[^1] is '.' or ' ') return false;
        var stem = part.Split('.')[0].TrimEnd(' ');
        return !DeviceNames.Contains(stem, StringComparer.OrdinalIgnoreCase);
    }
}
