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
public sealed record RecoverableFileResult<T>(T Value, FileGeneration Generation, Exception? PrimaryFailure)
{
    /// <summary>
    /// Whether <see cref="RecoverableFile.ReadAndRepair"/> restored the primary from the backup. Always
    /// false from <see cref="RecoverableFile.Read"/>.
    /// </summary>
    public bool PrimaryRepaired { get; init; }

    /// <summary>
    /// Why <see cref="RecoverableFile.ReadAndRepair"/> could not restore the primary after loading the
    /// backup, or null. <see cref="Value"/> is still the backup's value when this is set.
    /// </summary>
    public Exception? RepairFailure { get; init; }
}

/// <summary>
/// Validates staged writes before promotion and keeps a readable previous generation. Serializers,
/// incompatibility admission and slot naming belong to the caller. Operations require one writer;
/// a backup is not a journal and directory rename durability depends on the filesystem.
/// </summary>
/// <remarks>
/// Every promotion over an existing primary goes through
/// <see cref="File.Replace(string, string, string?, bool)"/>, which also moves the old primary to the
/// kept name (the backup on a write, the rejected file on a repair) when it is kept, so the primary
/// path names either the old file or the new one at every moment and the old one is never copied.
/// On Windows that is one <c>ReplaceFile</c> call. It fails while another handle has the primary or
/// the kept file open without <see cref="FileShare.Delete"/>, and succeeds when the handles share
/// delete, where a rename with <see cref="File.Move(string, string, bool)"/> over the open primary is
/// refused; a reader that shared delete keeps reading the old generation, under its new name when it
/// was kept. The new primary takes the replaced file's creation time and attributes on Windows and
/// keeps its own last write time. On other systems .NET removes the old kept file, hard-links the primary to the kept name
/// (copying it where links are unsupported) and renames the new file over the primary.
/// The previous kept file is given up before the promotion completes: a promotion that fails after
/// the primary was renamed (Windows) or after the old kept file was removed (other systems) leaves
/// the primary in place but can lose the previous backup or rejected file.
/// </remarks>
public static class RecoverableFile
{
    /// <summary>The suffix <see cref="ReadAndRepair"/> and <see cref="Restore"/> give a rejected primary by default.</summary>
    public const string DefaultRejectedSuffix = ".corrupt";

    /// <summary>
    /// Reads the primary and, on an admitted failure, its backup. Browsing never modifies either file.
    /// The default admits I/O and access errors; pass a predicate for format errors and exclude
    /// incompatible versions that must not silently load an older generation. Pass the same
    /// <paramref name="backupSuffix"/> that <see cref="Write"/> was given.
    /// </summary>
    /// <remarks>
    /// A primary failure the predicate does not admit propagates unchanged, and so does such a
    /// failure of the backup.
    /// </remarks>
    /// <exception cref="FileGenerationsUnreadableException">
    /// The primary failed with an admitted error and the backup is missing or failed with one too.
    /// </exception>
    public static RecoverableFileResult<T> Read<T>(string path, Func<string, T> read,
        Func<Exception, bool>? canRecover = null, string backupSuffix = ".bak")
    {
        ArgumentNullException.ThrowIfNull(read);
        canRecover ??= IsFileFailure;
        var backupPath = SiblingPath(path, backupSuffix, nameof(backupSuffix));
        try { return new(read(path), FileGeneration.Primary, null); }
        catch (Exception primary) when (canRecover(primary))
        {
            // A missing backup is reported the same way whatever the predicate admits, so the
            // caller always learns about the primary, which is the file it asked for.
            if (IsMissing(backupPath))
                throw new FileGenerationsUnreadableException(primary,
                    new FileNotFoundException("The backup generation does not exist.", backupPath));
            try { return new(read(backupPath), FileGeneration.Backup, primary); }
            catch (Exception backup) when (canRecover(backup))
            { throw new FileGenerationsUnreadableException(primary, backup); }
        }
    }

    /// <summary>
    /// Reads like <see cref="Read"/> and, when the value came from the backup, restores the primary
    /// from it with <see cref="Restore"/>, keeping the rejected primary at
    /// <paramref name="rejectedSuffix"/>. Use it to open a file for play; use <see cref="Read"/>
    /// for browsing.
    /// </summary>
    /// <remarks>
    /// Only a primary that <paramref name="isDamaged"/> calls damaged or missing is repaired. The
    /// default treats every admitted failure as damage except an access error and, on Windows, a
    /// sharing or lock violation: those say another process holds the file, which may be the newest
    /// generation, so the backup's value is returned without touching the primary and
    /// <see cref="RecoverableFileResult{T}.PrimaryRepaired"/> stays false with no
    /// <see cref="RecoverableFileResult{T}.RepairFailure"/>.
    /// The repair is best effort. An I/O or access error, or a failure the predicate admits, while
    /// restoring is returned as <see cref="RecoverableFileResult{T}.RepairFailure"/> and leaves the
    /// primary as it was; the backup's value is returned either way. <paramref name="read"/>
    /// validates the restored copy before it is promoted. A repair writes the primary, so serialize it
    /// with <see cref="Write"/> as for any other writer.
    /// </remarks>
    /// <typeparam name="T">The caller's decoded value type.</typeparam>
    /// <param name="path">The primary.</param>
    /// <param name="read">Decodes a generation; it also validates the restored copy.</param>
    /// <param name="canRecover">Which primary failures load the backup, as for <see cref="Read"/>.</param>
    /// <param name="backupSuffix">The suffix <see cref="Write"/> was given.</param>
    /// <param name="rejectedSuffix">Where a replaced primary is kept.</param>
    /// <param name="isDamaged">
    /// Which admitted primary failures mean the primary itself is damaged or missing and should be
    /// replaced. Null uses the default described in the remarks.
    /// </param>
    /// <exception cref="FileGenerationsUnreadableException">As for <see cref="Read"/>.</exception>
    public static RecoverableFileResult<T> ReadAndRepair<T>(string path, Func<string, T> read,
        Func<Exception, bool>? canRecover = null, string backupSuffix = ".bak",
        string rejectedSuffix = DefaultRejectedSuffix, Func<Exception, bool>? isDamaged = null)
    {
        ArgumentNullException.ThrowIfNull(read);
        canRecover ??= IsFileFailure;
        isDamaged ??= IsDamage;
        _ = RejectedPath(path, backupSuffix, rejectedSuffix);
        var result = Read(path, read, canRecover, backupSuffix);
        if (result.Generation == FileGeneration.Primary || !isDamaged(result.PrimaryFailure!)) return result;
        try
        {
            Restore(path, candidate => _ = read(candidate), backupSuffix, rejectedSuffix);
            return result with { PrimaryRepaired = true };
        }
        catch (Exception error) when (IsFileFailure(error) || canRecover(error))
        {
            return result with { RepairFailure = error };
        }
    }

    /// <summary>
    /// Restores the primary from its backup. The backup is copied to a durable sibling, which
    /// <paramref name="validate"/> checks; the sibling then replaces the primary, and the primary it
    /// replaces is kept at <paramref name="rejectedSuffix"/>, overwriting an earlier rejected file.
    /// The backup is not changed.
    /// </summary>
    /// <remarks>
    /// The rejected primary is the evidence of what went wrong, so it is moved aside rather than
    /// overwritten. A failure before the promotion leaves the primary unchanged and removes the
    /// sibling. When the primary is missing the sibling is moved into place. Serialize it with
    /// <see cref="Write"/> as for any other writer.
    /// </remarks>
    /// <exception cref="FileNotFoundException">The backup does not exist.</exception>
    public static void Restore(string path, Action<string> validate, string backupSuffix = ".bak",
        string rejectedSuffix = DefaultRejectedSuffix)
    {
        ArgumentNullException.ThrowIfNull(validate);
        var full = Path.GetFullPath(path);
        var backupPath = SiblingPath(full, backupSuffix, nameof(backupSuffix));
        var rejectedPath = RejectedPath(full, backupSuffix, rejectedSuffix);
        var temporary = AtomicFile.TemporaryPath(full);
        try
        {
            using (var input = new FileStream(backupPath, FileMode.Open, FileAccess.Read, FileShare.Read))
                Stage(temporary, input.CopyTo);
            validate(temporary);
            Promote(temporary, full, rejectedPath);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }

    /// <summary>
    /// Writes a durable sibling, validates it, keeps a usable primary as the backup and promotes the
    /// sibling. Only trust an existing primary while holding a writer lease and knowing its exact
    /// generation. A rejected primary is overwritten and leaves an existing backup intact. A
    /// preservation predicate may also keep an incompatible primary. A validation or staging failure
    /// leaves the primary unchanged.
    /// </summary>
    /// <remarks>
    /// A kept primary is renamed to the backup in the same <see cref="File.Replace(string, string, string?, bool)"/>
    /// that promotes the sibling, so neither generation is read or copied again; the class remarks
    /// give the sharing rules. The previous backup is replaced.
    /// </remarks>
    public static void Write(string path, Action<Stream> write, Action<string> validate,
        Func<Exception, bool>? canReject = null, Func<Exception, bool>? preserveRejected = null,
        string backupSuffix = ".bak", bool trustExistingPrimary = false)
    {
        ArgumentNullException.ThrowIfNull(write);
        ArgumentNullException.ThrowIfNull(validate);
        canReject ??= IsFileFailure;
        var full = Path.GetFullPath(path);
        var backupPath = SiblingPath(full, backupSuffix, nameof(backupSuffix));
        Directory.CreateDirectory(Path.GetDirectoryName(full)!);
        var temporary = AtomicFile.TemporaryPath(full);
        try
        {
            Stage(temporary, write);
            validate(temporary);
            var preserve = false;
            if (File.Exists(full))
            {
                try { if (!trustExistingPrimary) validate(full); preserve = true; }
                catch (Exception error) when (canReject(error))
                { preserve = preserveRejected?.Invoke(error) ?? false; }
            }
            Promote(temporary, full, preserve ? backupPath : null);
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

    // HRESULT of ERROR_UNABLE_TO_MOVE_REPLACEMENT_2, which ReplaceFile returns after it has renamed
    // the replaced file to the backup name but could not move the replacement in.
    private const int UnableToMoveReplacement2 = unchecked((int)0x80070499);

    // Writes the staged sibling and flushes it to disk before anything validates or promotes it.
    private static void Stage(string staged, Action<Stream> write)
    {
        using var stream = new FileStream(staged, FileMode.CreateNew, FileAccess.Write,
            FileShare.None, 128 * 1024, FileOptions.WriteThrough);
        write(stream);
        stream.Flush(true);
    }

    // Moves the staged file over the primary and, when keptPath is set, the primary to keptPath in
    // one call. A missing primary has nothing to replace, so the staged file is moved into place.
    private static void Promote(string staged, string primary, string? keptPath)
    {
        if (!File.Exists(primary))
        {
            File.Move(staged, primary);
            return;
        }
        try { File.Replace(staged, primary, keptPath, ignoreMetadataErrors: true); }
        catch (IOException error) when (keptPath is not null && error.HResult == UnableToMoveReplacement2
            && !File.Exists(primary) && File.Exists(keptPath))
        {
            // Put the primary back, so that a failed promotion leaves the primary where it was.
            File.Move(keptPath, primary);
            throw;
        }
        catch (IOException) when (keptPath is null && !File.Exists(primary) && File.Exists(staged))
        {
            // Without a kept name, ReplaceFile can fail after deleting the primary
            // (ERROR_UNABLE_TO_MOVE_REPLACEMENT). Move the staged file in, so the new generation is
            // not deleted with the temporary files.
            File.Move(staged, primary);
        }
    }

    private static string RejectedPath(string path, string backupSuffix, string rejectedSuffix)
    {
        var rejected = SiblingPath(path, rejectedSuffix, nameof(rejectedSuffix));
        if (string.Equals(rejected, SiblingPath(path, backupSuffix, nameof(backupSuffix)),
                StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("Rejected suffix must name a file other than the backup.",
                nameof(rejectedSuffix));
        return rejected;
    }

    private static string SiblingPath(string path, string suffix, string parameterName)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        ArgumentException.ThrowIfNullOrWhiteSpace(suffix, parameterName);
        if (suffix.Any(character => character is '/' or '\\' or ':' or '\0'))
            throw new ArgumentException("Suffix must name a file beside the primary.", parameterName);
        var full = Path.GetFullPath(path);
        var sibling = Path.GetFullPath(full + suffix);
        // Windows trims trailing dots and spaces, so a suffix such as "." names the primary itself.
        if (string.Equals(sibling, full, StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("Suffix must name a file other than the primary.", parameterName);
        return sibling;
    }

    // HRESULTs of ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION: another handle holds the file.
    private const int SharingViolation = unchecked((int)0x80070020);
    private const int LockViolation = unchecked((int)0x80070021);

    private static bool IsDamage(Exception error) => error is not UnauthorizedAccessException
        && !(error is IOException && error.HResult is SharingViolation or LockViolation);

    // File.Exists also returns false for a path it may not inspect, which would report a backup that
    // exists as missing; GetAttributes throws a different error in that case.
    private static bool IsMissing(string path)
    {
        try { File.GetAttributes(path); return false; }
        catch (Exception error) when (error is FileNotFoundException or DirectoryNotFoundException) { return true; }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { return false; }
    }

    private static bool IsFileFailure(Exception error) => error is IOException or UnauthorizedAccessException;
}
