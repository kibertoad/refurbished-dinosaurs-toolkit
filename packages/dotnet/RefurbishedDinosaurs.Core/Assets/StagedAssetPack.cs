namespace RefurbishedDinosaurs.Core.Assets;

/// <summary>Builds and validates a pack before atomically replacing the installed one.</summary>
public sealed class StagedAssetPack : IDisposable
{
    private readonly string _destination;
    private bool _committed;

    private StagedAssetPack(string destination, string stagingDirectory)
    {
        _destination = destination;
        StagingDirectory = stagingDirectory;
    }

    /// <summary>The directory to write the new pack into. It is a sibling of the destination.</summary>
    public string StagingDirectory { get; }

    /// <summary>Creates an empty staging directory next to <paramref name="destination"/>.</summary>
    /// <param name="destination">The directory the pack will replace on <see cref="Commit"/>.</param>
    public static StagedAssetPack Create(string destination)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(destination);
        var fullDestination = Path.GetFullPath(destination);
        var parent = Directory.GetParent(fullDestination)?.FullName
            ?? throw new ArgumentException("Destination must have a parent directory.", nameof(destination));
        Directory.CreateDirectory(parent);
        var staging = Path.Combine(parent,
            $".{Path.GetFileName(fullDestination)}.staging-{Guid.NewGuid():N}");
        Directory.CreateDirectory(staging);
        return new(fullDestination, staging);
    }

    /// <summary>
    /// Moves the staging directory to the destination, replacing any existing pack. If the move fails the
    /// previous pack is restored. A leftover backup that cannot be deleted is left in place.
    /// </summary>
    /// <exception cref="InvalidOperationException">The pack was already committed.</exception>
    public void Commit()
    {
        if (_committed) throw new InvalidOperationException("The staged pack was already committed.");
        var backup = _destination + $".backup-{Guid.NewGuid():N}";
        var hadDestination = Directory.Exists(_destination);
        try
        {
            if (hadDestination) Directory.Move(_destination, backup);
            Directory.Move(StagingDirectory, _destination);
            _committed = true;
        }
        catch
        {
            if (!Directory.Exists(_destination) && Directory.Exists(backup))
                Directory.Move(backup, _destination);
            throw;
        }

        // The new pack is already live. A failure to clean the backup should not
        // report the import as failed or attempt a risky second directory swap.
        if (hadDestination)
        {
            try { Directory.Delete(backup, recursive: true); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    /// <summary>Deletes the staging directory unless the pack was committed.</summary>
    public void Dispose()
    {
        if (!_committed && Directory.Exists(StagingDirectory))
            Directory.Delete(StagingDirectory, recursive: true);
    }
}
