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
    /// Normalizes <c>\</c> separators to <c>/</c> and checks that the reference names the same
    /// relative file on Windows, Linux and macOS. Every host rejects the same references:
    /// <list type="bullet">
    /// <item><description>a rooted reference, a UNC name included;</description></item>
    /// <item><description>an empty component, and a <c>.</c> or <c>..</c> component;</description></item>
    /// <item><description>
    /// a character Windows refuses in a file name: a C0 control character (U+0000 to U+001F) or one of
    /// <c>&lt; &gt; : " | ? *</c>, which also rejects a drive-relative name such as <c>C:x.dat</c>;
    /// </description></item>
    /// <item><description>
    /// DEL (U+007F), and a UTF-16 surrogate without its pair, which Linux and macOS cannot store
    /// under the same name;
    /// </description></item>
    /// <item><description>
    /// a component Windows reads differently: one that ends in a dot or a space, or a reserved device
    /// name such as <c>CON</c> or <c>nul.dat</c>.
    /// </description></item>
    /// </list>
    /// C1 control characters (U+0080 to U+009F) are accepted. Every host stores them, and the readers
    /// that decode legacy names byte for byte as Latin-1 give them for bytes 0x80 to 0x9F, which hold
    /// letters and punctuation in DOS, Windows and Shift-JIS names.
    /// </summary>
    /// <param name="reference">A relative reference with <c>/</c> or <c>\</c> separators.</param>
    /// <returns>The reference with <c>/</c> separators.</returns>
    /// <exception cref="InvalidDataException">
    /// <paramref name="reference"/> is null, blank or not a portable relative path. The message gives
    /// the reference as a JSON string, with each control character and unpaired surrogate written as a
    /// <c>\u</c> escape, and names the rule it breaks.
    /// </exception>
    public static string Relative(string reference)
    {
        var normalized = Present(reference).Replace('\\', '/');
        if (Problem(normalized) is { } problem)
            throw new InvalidDataException($"Asset reference {Shown(reference)} is not a portable relative path: {problem}.");
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

    // The characters Windows refuses in a file name besides the C0 controls and the two separators.
    private const string WindowsReservedCharacters = "<>:\"|?*";

    // The first rule a normalized reference breaks, or null when it is portable.
    private static string? Problem(string normalized)
    {
        if (normalized.StartsWith('/')) return "it is rooted";
        for (var index = 0; index < normalized.Length; index++)
        {
            var c = normalized[index];
            if (c < ' ' || c == '\u007f') return $"it contains the control character U+{(int)c:X4}";
            if (WindowsReservedCharacters.Contains(c))
                return $"it contains '{c}', which Windows does not allow in a file name";
            if (char.IsHighSurrogate(c) && index + 1 < normalized.Length && char.IsLowSurrogate(normalized[index + 1]))
                index++;
            else if (char.IsSurrogate(c))
                return $"it contains the unpaired surrogate U+{(int)c:X4}";
        }
        foreach (var part in normalized.Split('/'))
        {
            if (part.Length == 0) return "it has an empty component";
            if (part is "." or "..") return $"it has a '{part}' component";
            if (part[^1] is '.' or ' ')
                return $"component {Shown(part)} ends with a dot or a space, which Windows drops";
            var stem = part.Split('.')[0].TrimEnd(' ');
            if (DeviceNames.Contains(stem, StringComparer.OrdinalIgnoreCase))
                return $"component {Shown(part)} is the reserved Windows device name {stem}";
        }
        return null;
    }

    // A JSON string, so a control character or an unpaired surrogate stays visible on one line.
    private static string Shown(string value)
    {
        var text = new System.Text.StringBuilder("\"", value.Length + 2);
        for (var index = 0; index < value.Length; index++)
        {
            var c = value[index];
            if (c is '"' or '\\') text.Append('\\').Append(c);
            else if (char.IsHighSurrogate(c) && index + 1 < value.Length && char.IsLowSurrogate(value[index + 1]))
                text.Append(c).Append(value[++index]);
            else if (char.IsControl(c) || char.IsSurrogate(c)) text.Append($"\\u{(int)c:X4}");
            else text.Append(c);
        }
        return text.Append('"').ToString();
    }
}
