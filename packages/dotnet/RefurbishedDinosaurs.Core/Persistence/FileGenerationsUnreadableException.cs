namespace RefurbishedDinosaurs.Core.Persistence;

/// <summary>
/// Thrown by <see cref="RecoverableFile.Read"/> and <see cref="RecoverableFile.ReadAndRepair"/> when the
/// primary fails with an admitted error and the backup is missing or fails with an admitted error too.
/// <see cref="AggregateException.InnerExceptions"/> holds the primary's failure and then the backup's.
/// </summary>
/// <remarks>
/// A caller that classifies failures by type, or by data a reader attached to its own exceptions,
/// classifies <see cref="PrimaryFailure"/>: the primary is the file that was asked for, and it is the
/// one the player or the log should hear about. Rethrowing it with
/// <see cref="System.Runtime.ExceptionServices.ExceptionDispatchInfo.Throw(Exception)"/> keeps the
/// reader's stack trace.
/// </remarks>
public sealed class FileGenerationsUnreadableException : AggregateException
{
    /// <summary>Creates the exception from the two generations' failures.</summary>
    /// <param name="primaryFailure">Why the primary could not be read.</param>
    /// <param name="backupFailure">Why the backup could not be read. <see cref="RecoverableFile"/> reports a missing backup as a <see cref="FileNotFoundException"/> naming the backup path.</param>
    public FileGenerationsUnreadableException(Exception primaryFailure, Exception backupFailure)
        : base("Neither file generation is readable.",
            primaryFailure ?? throw new ArgumentNullException(nameof(primaryFailure)),
            backupFailure ?? throw new ArgumentNullException(nameof(backupFailure)))
    {
        PrimaryFailure = primaryFailure;
        BackupFailure = backupFailure;
    }

    /// <summary>Why the primary could not be read.</summary>
    public Exception PrimaryFailure { get; }

    /// <summary>Why the backup could not be read.</summary>
    public Exception BackupFailure { get; }
}
