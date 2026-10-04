using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.Core.Persistence;

/// <summary>Identifies the generation used by a recovering read.</summary>
public enum FileGeneration
{
    /// <summary>The primary file was valid.</summary>
    Primary,
    /// <summary>The backup file was used.</summary>
    Backup
}

/// <summary>A decoded value and the generation it came from.</summary>
/// <typeparam name="T">The caller's decoded value type.</typeparam>
/// <param name="Value">The decoded value.</param>
/// <param name="Generation">The successful file generation.</param>
/// <param name="PrimaryFailure">The rejected primary's error, or null.</param>
public sealed record RecoverableFileResult<T>(T Value, FileGeneration Generation, Exception? PrimaryFailure);

/// <summary>
/// Validates staged writes before promotion and keeps a readable previous generation. Serializers,
/// incompatibility admission and slot naming belong to the caller. Operations require one writer;
/// a backup is not a journal and directory rename durability depends on the filesystem.
/// </summary>
public static class RecoverableFile
{
    /// <summary>
    /// Reads the primary and, on an admitted failure, its backup. Browsing never modifies either file.
    /// The default admits I/O and access errors; pass a predicate for format errors and exclude
    /// incompatible versions that must not silently load an older generation. Pass the same
    /// <paramref name="backupSuffix"/> that <see cref="Write"/> was given.
    /// </summary>
    public static RecoverableFileResult<T> Read<T>(string path, Func<string, T> read,
        Func<Exception, bool>? canRecover = null, string backupSuffix = ".bak")
    {
        ArgumentNullException.ThrowIfNull(read);
        canRecover ??= IsFileFailure;
        var backupPath = BackupPath(path, backupSuffix);
        try { return new(read(path), FileGeneration.Primary, null); }
        catch (Exception primary) when (canRecover(primary))
        {
            try { return new(read(backupPath), FileGeneration.Backup, primary); }
            catch (Exception backup) when (canRecover(backup))
            { throw new AggregateException("Neither file generation is readable.", primary, backup); }
        }
    }

    /// <summary>
    /// Writes a durable sibling, validates it, backs up a usable primary and promotes the sibling.
    /// Only trust an existing primary while holding a writer lease and knowing its exact generation.
    /// A rejected primary leaves an existing backup intact. A preservation predicate may also keep
    /// an incompatible primary. A validation or staging failure leaves the primary unchanged.
    /// </summary>
    public static void Write(string path, Action<Stream> write, Action<string> validate,
        Func<Exception, bool>? canReject = null, Func<Exception, bool>? preserveRejected = null,
        string backupSuffix = ".bak", bool trustExistingPrimary = false)
    {
        ArgumentNullException.ThrowIfNull(write);
        ArgumentNullException.ThrowIfNull(validate);
        canReject ??= IsFileFailure;
        var full = Path.GetFullPath(path);
        var backupPath = BackupPath(full, backupSuffix);
        Directory.CreateDirectory(Path.GetDirectoryName(full)!);
        var temporary = AtomicFile.TemporaryPath(full);
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write,
                FileShare.None, 128 * 1024, FileOptions.WriteThrough))
            {
                write(stream);
                stream.Flush(true);
            }
            validate(temporary);
            var preserve = false;
            if (File.Exists(full))
            {
                try { if (!trustExistingPrimary) validate(full); preserve = true; }
                catch (Exception error) when (canReject(error))
                { preserve = preserveRejected?.Invoke(error) ?? false; }
            }
            if (preserve) AtomicFile.Copy(full, backupPath);
            File.Move(temporary, full, true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }

    /// <summary>Reads at most the specified number of bytes, rejecting larger files before allocation.</summary>
    public static byte[] ReadBounded(string path, int maximumBytes)
    {
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(maximumBytes);
        using var stream = File.OpenRead(path);
        if (stream.Length > maximumBytes) throw new InvalidDataException("File exceeds its size limit.");
        var bytes = new byte[(int)stream.Length];
        stream.ReadExactly(bytes);
        if (stream.ReadByte() != -1) throw new InvalidDataException("File grew beyond its admitted length.");
        return bytes;
    }

    private static string BackupPath(string path, string backupSuffix)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        ArgumentException.ThrowIfNullOrWhiteSpace(backupSuffix);
        if (backupSuffix.Any(character => character is '/' or '\\' or ':' or '\0'))
            throw new ArgumentException("Backup suffix must remain beside the primary.", nameof(backupSuffix));
        var full = Path.GetFullPath(path);
        var backup = Path.GetFullPath(full + backupSuffix);
        // Windows trims trailing dots and spaces, so a suffix such as "." names the primary itself.
        if (string.Equals(backup, full, StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("Backup suffix must name a different file.", nameof(backupSuffix));
        return backup;
    }

    private static bool IsFileFailure(Exception error) => error is IOException or UnauthorizedAccessException;
}
