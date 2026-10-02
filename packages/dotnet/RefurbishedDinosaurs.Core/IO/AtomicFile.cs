using System.Text;

namespace RefurbishedDinosaurs.Core.IO;

/// <summary>
/// Writes files through a temporary sibling that is flushed to disk and then moved over the target, so
/// readers see the old file or the new one, never a partial write.
/// </summary>
public static class AtomicFile
{
    /// <summary>Writes <paramref name="contents"/> as UTF-8 without a byte-order mark.</summary>
    public static void WriteAllText(string path, string contents) =>
        WriteBytes(path, new UTF8Encoding(false).GetBytes(contents));

    /// <summary>Writes <paramref name="contents"/>, creating the parent directory if needed.</summary>
    public static void WriteBytes(string path, ReadOnlySpan<byte> contents)
    {
        var destination = Path.GetFullPath(path);
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        var temporary = TemporaryPath(destination);
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write,
                       FileShare.None, 128 * 1024, FileOptions.WriteThrough))
            {
                stream.Write(contents);
                stream.Flush(flushToDisk: true);
            }
            File.Move(temporary, destination, overwrite: true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }

    /// <summary>Copies <paramref name="source"/> to <paramref name="destination"/>, creating the parent directory if needed.</summary>
    public static void Copy(string source, string destination)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(source);
        var target = Path.GetFullPath(destination);
        Directory.CreateDirectory(Path.GetDirectoryName(target)!);
        var temporary = TemporaryPath(target);
        try
        {
            File.Copy(source, temporary, overwrite: false);
            using (var stream = File.Open(temporary, FileMode.Open, FileAccess.Write, FileShare.None))
                stream.Flush(flushToDisk: true);
            File.Move(temporary, target, overwrite: true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }

    private static string TemporaryPath(string destination) => Path.Combine(
        Path.GetDirectoryName(destination)!, $".{Path.GetFileName(destination)}.{Guid.NewGuid():N}.tmp");
}
