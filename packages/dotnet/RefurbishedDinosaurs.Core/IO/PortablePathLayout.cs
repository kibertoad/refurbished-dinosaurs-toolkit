namespace RefurbishedDinosaurs.Core.IO;

/// <summary>
/// Lays out a set of relative file paths so the tree they describe is the same on a case-sensitive
/// and a case-insensitive file system. Each directory gets one spelling, the one of the first path
/// added under it. A path used twice ignoring case is refused, and so is a path that is a file in one
/// place and a directory of another file in another, ignoring case.
/// </summary>
public sealed class PortablePathLayout
{
    private readonly Dictionary<string, string> _directories = new(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> _files = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>The number of file paths added.</summary>
    public int Count => _files.Count;

    /// <summary>
    /// Adds a file path and returns it with each of its directories spelled as the first path added
    /// under that directory spelled it. The file name keeps the spelling given.
    /// </summary>
    /// <param name="relative">A path <see cref="PortableAssetPath.Relative"/> accepts.</param>
    /// <returns>The path with <c>/</c> separators and the layout's directory spellings.</returns>
    /// <exception cref="InvalidDataException">
    /// <paramref name="relative"/> is not accepted by <see cref="PortableAssetPath.Relative"/>, was
    /// already added ignoring case, is a directory of a file already added, or passes through a path
    /// already added as a file. A rejected path leaves the layout unchanged.
    /// </exception>
    public string Add(string relative)
    {
        var parts = PortableAssetPath.Relative(relative).Split('/');
        var spelled = string.Empty;
        for (var index = 0; index < parts.Length - 1; index++)
        {
            var next = spelled.Length == 0 ? parts[index] : $"{spelled}/{parts[index]}";
            if (_files.Contains(next))
                throw new InvalidDataException($"Path is both a file and a directory of another file: {next}");
            // A new directory can only follow other new ones, under which no file exists yet, so
            // recording it here never leaves a rejected path's directories behind.
            if (!_directories.TryGetValue(next, out var existing)) _directories.Add(next, existing = next);
            spelled = existing;
        }
        var path = spelled.Length == 0 ? parts[^1] : $"{spelled}/{parts[^1]}";
        if (_directories.ContainsKey(path))
            throw new InvalidDataException($"Path is both a file and a directory of another file: {path}");
        if (!_files.Add(path)) throw new InvalidDataException($"Path appears twice, ignoring case: {path}");
        return path;
    }
}
