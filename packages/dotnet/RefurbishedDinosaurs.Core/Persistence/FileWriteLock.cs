using System.Diagnostics;

namespace RefurbishedDinosaurs.Core.Persistence;

/// <summary>A cooperative exclusive sibling-file lock for serialized save writers.</summary>
public static class FileWriteLock
{
    /// <summary>
    /// Opens path plus .lock exclusively, retrying I/O sharing failures until timeout. The returned
    /// stream owns the lease and must be disposed. The lock file remains after release so waiters
    /// cannot lock different inodes. Every participating process must use this convention.
    /// </summary>
    public static IDisposable Acquire(string path, TimeSpan timeout, CancellationToken cancellationToken = default)
    {
        if (timeout < TimeSpan.Zero || timeout.TotalMilliseconds > int.MaxValue)
            throw new ArgumentOutOfRangeException(nameof(timeout));
        var full = Path.GetFullPath(path);
        Directory.CreateDirectory(Path.GetDirectoryName(full)!);
        var clock = Stopwatch.StartNew();
        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            try { return new FileStream(full + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
            // Missing directories and over-long paths will not clear by waiting; report them as they are.
            catch (IOException error) when (error is not (FileNotFoundException or DirectoryNotFoundException or
                                                          PathTooLongException))
            {
                var remaining = timeout - clock.Elapsed;
                if (remaining <= TimeSpan.Zero) throw new TimeoutException("Save writer lock was not available.", error);
                cancellationToken.WaitHandle.WaitOne(TimeSpan.FromMilliseconds(Math.Min(50, remaining.TotalMilliseconds)));
            }
        }
    }
}
