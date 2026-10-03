using System.IO.Enumeration;

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

    private static readonly EnumerationOptions EntryOptions = new()
    {
        AttributesToSkip = 0,
        IgnoreInaccessible = false,
        MatchType = MatchType.Simple,
        RecurseSubdirectories = false,
        ReturnSpecialDirectories = false
    };

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
    /// This is a read-time check, not protection against concurrent directory replacement.
    /// </summary>
    /// <exception cref="ArgumentException"><paramref name="root"/> is null or blank.</exception>
    /// <exception cref="InvalidDataException">
    /// The root is not a directory, or the reference is unsafe, missing, ambiguous, linked or not a file.
    /// </exception>
    public static string ResolveFile(string root, string relative)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        var parts = Relative(relative).Split('/');
        var rootInfo = new DirectoryInfo(Path.GetFullPath(root));
        if (!rootInfo.Exists) throw new InvalidDataException("Asset root directory does not exist.");
        RejectLink(rootInfo.Attributes);
        var current = rootInfo.FullName;
        for (var index = 0; index < parts.Length; index++)
        {
            var match = SingleEntry(current, parts[index]);
            RejectLink(match.Attributes);
            var last = index == parts.Length - 1;
            if (match.IsDirectory == last)
                throw new InvalidDataException(last ? "Asset reference is not a file." : "Asset directory does not exist.");
            current = match.FullPath;
        }
        return Path.GetRelativePath(rootInfo.FullName, current).Replace('\\', '/');
    }

    /// <summary>
    /// Finds the one entry of <paramref name="directory"/> named <paramref name="name"/> ignoring case.
    /// Hidden, system and reparse entries are listed and an unreadable directory throws, so nothing a
    /// different host would see is skipped. Only matching entries build a full path.
    /// </summary>
    private static (string FullPath, FileAttributes Attributes, bool IsDirectory) SingleEntry(string directory, string name)
    {
        var matches = new FileSystemEnumerable<(string, FileAttributes, bool)>(
            directory,
            (ref FileSystemEntry entry) => (entry.ToFullPath(), entry.Attributes, entry.IsDirectory),
            EntryOptions)
        {
            ShouldIncludePredicate = (ref FileSystemEntry entry) =>
                entry.FileName.Equals(name, StringComparison.OrdinalIgnoreCase)
        }.Take(2).ToArray();
        return matches.Length == 1
            ? matches[0]
            : throw new InvalidDataException(matches.Length == 0 ? "Asset does not exist." : "Asset spelling is ambiguous.");
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

    private static void RejectLink(FileAttributes attributes)
    {
        if ((attributes & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException("Asset path contains a symbolic link or reparse point.");
    }
}
