using Toad.Discovery.Core.IO;

namespace Toad.Discovery.Core.Assets;

/// <summary>A file an import will write.</summary>
/// <param name="Path">Path relative to the content root.</param>
/// <param name="Size">Size in bytes.</param>
public sealed record PlannedAsset(string Path, long Size);

/// <summary>Disk space an import needs, from <see cref="ImportDiskPlanner.Calculate"/>.</summary>
/// <param name="InstalledBytes">Total size of all planned files once installed.</param>
/// <param name="NewBytes">Total size of planned files that do not exist yet.</param>
/// <param name="ReplacementScratchBytes">
/// The largest planned file that replaces an existing one, since an atomic replacement holds the old
/// and new copies at once.
/// </param>
public sealed record ImportDiskPlan(long InstalledBytes, long NewBytes, long ReplacementScratchBytes)
{
    /// <summary>Free space to require before starting: new bytes plus the largest replacement.</summary>
    public long RequiredAvailableBytes => checked(NewBytes + ReplacementScratchBytes);
}

/// <summary>Estimates the free space an import into an existing content root needs.</summary>
public static class ImportDiskPlanner
{
    /// <summary>
    /// Plans writing <paramref name="assets"/> under <paramref name="root"/>, counting which files already
    /// exist there.
    /// </summary>
    /// <exception cref="InvalidDataException">
    /// A size is negative, a path escapes <paramref name="root"/>, or two paths name the same file.
    /// </exception>
    public static ImportDiskPlan Calculate(string root, IEnumerable<PlannedAsset> assets)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        ArgumentNullException.ThrowIfNull(assets);
        long installed = 0, newBytes = 0, scratch = 0;
        var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var asset in assets)
        {
            if (asset.Size < 0) throw new InvalidDataException("Planned asset has a negative size.");
            var target = SafePath.Below(root, asset.Path);
            if (!paths.Add(target)) throw new InvalidDataException("Planned asset paths are not unique.");
            installed = checked(installed + asset.Size);
            if (File.Exists(target)) scratch = Math.Max(scratch, asset.Size);
            else newBytes = checked(newBytes + asset.Size);
        }
        return new(installed, newBytes, scratch);
    }
}
