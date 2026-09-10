namespace Toad.Discovery.Core.Assets;

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

    public string StagingDirectory { get; }

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

    public void Dispose()
    {
        if (!_committed && Directory.Exists(StagingDirectory))
            Directory.Delete(StagingDirectory, recursive: true);
    }
}
