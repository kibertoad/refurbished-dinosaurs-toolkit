using Toad.Discovery.Core.IO;

namespace Toad.Discovery.Core.Assets;

public sealed record PlannedAsset(string Path, long Size);

public sealed record ImportDiskPlan(long InstalledBytes, long NewBytes, long ReplacementScratchBytes)
{
    public long RequiredAvailableBytes => checked(NewBytes + ReplacementScratchBytes);
}

public static class ImportDiskPlanner
{
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
