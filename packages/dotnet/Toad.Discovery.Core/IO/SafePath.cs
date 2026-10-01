namespace Toad.Discovery.Core.IO;

/// <summary>Builds file system paths from untrusted names without letting them escape a root.</summary>
public static class SafePath
{
    /// <summary>
    /// Returns the full path of <paramref name="relative"/> under <paramref name="root"/>. Comparison
    /// ignores case on Windows.
    /// </summary>
    /// <exception cref="InvalidDataException">
    /// <paramref name="relative"/> is absolute or resolves outside <paramref name="root"/>.
    /// </exception>
    public static string Below(string root, string relative)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        ArgumentException.ThrowIfNullOrWhiteSpace(relative);
        if (Path.IsPathFullyQualified(relative))
            throw new InvalidDataException("Path must be relative.");
        var fullRoot = Path.GetFullPath(root).TrimEnd(
            Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var prefix = fullRoot + Path.DirectorySeparatorChar;
        var target = Path.GetFullPath(Path.Combine(fullRoot, relative));
        var comparison = OperatingSystem.IsWindows()
            ? StringComparison.OrdinalIgnoreCase
            : StringComparison.Ordinal;
        if (!target.StartsWith(prefix, comparison))
            throw new InvalidDataException("Path escapes its designated root.");
        return target;
    }

    /// <summary>Keeps only letters, digits, <c>.</c>, <c>_</c> and <c>-</c> from <paramref name="candidate"/>.</summary>
    /// <exception cref="InvalidDataException">Nothing usable remains, or only <c>.</c> or <c>..</c>.</exception>
    public static string FileName(string candidate)
    {
        ArgumentNullException.ThrowIfNull(candidate);
        var safe = string.Concat(candidate.Where(character =>
            char.IsLetterOrDigit(character) || character is '.' or '_' or '-'));
        return string.IsNullOrWhiteSpace(safe) || safe is "." or ".."
            ? throw new InvalidDataException("Value has no safe filename characters.")
            : safe;
    }
}
