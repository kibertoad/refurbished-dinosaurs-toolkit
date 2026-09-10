namespace Toad.Discovery.Core.IO;

public static class SafePath
{
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
