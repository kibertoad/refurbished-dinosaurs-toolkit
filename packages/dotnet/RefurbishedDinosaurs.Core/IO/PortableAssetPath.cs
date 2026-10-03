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
    public static string Relative(string reference)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(reference);
        var normalized = reference.Replace('\\', '/');
        if (normalized.StartsWith('/') || normalized.Contains(':') || normalized.Contains('\0') ||
            !normalized.Split('/').All(IsPortableComponent))
            throw new InvalidDataException("Asset reference must contain only relative path components.");
        return normalized;
    }

    /// <summary>
    /// Removes an explicit ASCII drive and rooted separator from a legacy installation reference.
    /// UNC references and drive-relative names are rejected. The caller opts into discarding a drive.
    /// </summary>
    public static string WithoutDriveRoot(string reference)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(reference);
        var normalized = reference.Replace('\\', '/');
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
    /// This is a read-time check, not protection against concurrent directory replacement.
    /// </summary>
    /// <exception cref="InvalidDataException">
    /// The root is not a directory, or the reference is unsafe, missing, ambiguous, linked or not a file.
    /// </exception>
    public static string ResolveFile(string root, string relative)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        var parts = Relative(relative).Split('/');
        var fullRoot = Path.GetFullPath(root);
        if (!Directory.Exists(fullRoot))
            throw new InvalidDataException("Asset root directory does not exist.");
        RejectLink(fullRoot);
        var current = fullRoot;
        for (var index = 0; index < parts.Length; index++)
        {
            if (!Directory.Exists(current))
                throw new InvalidDataException("Asset directory does not exist.");
            var matches = Directory.EnumerateFileSystemEntries(current)
                .Where(entry => string.Equals(Path.GetFileName(entry), parts[index], StringComparison.OrdinalIgnoreCase))
                .Take(2).ToArray();
            if (matches.Length != 1)
                throw new InvalidDataException(matches.Length == 0 ? "Asset does not exist." : "Asset spelling is ambiguous.");
            current = matches[0];
            RejectLink(current);
        }
        if (!File.Exists(current)) throw new InvalidDataException("Asset reference is not a file.");
        return Path.GetRelativePath(fullRoot, current).Replace('\\', '/');
    }

    private static bool IsPortableComponent(string part)
    {
        if (part.Length == 0 || part[^1] is '.' or ' ') return false;
        var stem = part.Split('.')[0].TrimEnd(' ');
        return !DeviceNames.Contains(stem, StringComparer.OrdinalIgnoreCase);
    }

    private static void RejectLink(string path)
    {
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException("Asset path contains a symbolic link or reparse point.");
    }
}
