namespace RefurbishedDinosaurs.Core.IO;

/// <summary>Interprets legacy asset references consistently on Windows, Linux and macOS.</summary>
public static class PortableAssetPath
{
    /// <summary>Normalizes separators and rejects roots, drive-relative names, traversal and empty components.</summary>
    public static string Relative(string reference)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(reference);
        var normalized = reference.Replace('\\', '/');
        if (normalized.StartsWith('/') || normalized.Contains(':') || normalized.Contains('\0') ||
            normalized.Split('/').Any(part => part is "" or "." or ".."))
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
        return Relative(normalized);
    }

    /// <summary>
    /// Resolves an existing file component by component, ignoring ordinal case. Multiple matching
    /// names are rejected even when one matches exactly. Symbolic links and reparse points are
    /// rejected, including the root. Returns the actual relative spelling with forward slashes.
    /// This is a read-time check, not protection against concurrent directory replacement.
    /// </summary>
    public static string ResolveFile(string root, string relative)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        var parts = Relative(relative).Split('/');
        var current = Path.GetFullPath(root);
        RejectLink(current);
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
        return Path.GetRelativePath(Path.GetFullPath(root), current).Replace('\\', '/');
    }

    private static void RejectLink(string path)
    {
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException("Asset path contains a symbolic link or reparse point.");
    }
}
