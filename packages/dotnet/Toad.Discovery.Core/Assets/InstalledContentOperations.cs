using System.Security.Cryptography;
using Toad.Discovery.Core.IO;

namespace Toad.Discovery.Core.Assets;

/// <summary>The file an <see cref="InstalledContentWriter"/> call left in place.</summary>
/// <param name="Path">Full path of the installed file.</param>
/// <param name="Bytes">Its size.</param>
/// <param name="Sha256">Its SHA-256 as lowercase hex.</param>
/// <param name="Changed">Whether the file was written, or an identical file was already there.</param>
public sealed record InstalledFileResult(string Path, long Bytes, string Sha256, bool Changed);

/// <summary>Writes generated/imported files atomically and reuses identical installed output.</summary>
public static class InstalledContentWriter
{
    /// <summary>
    /// Writes <paramref name="bytes"/> to <paramref name="relativePath"/> under <paramref name="root"/>,
    /// unless a file with the same size and hash is already there.
    /// </summary>
    /// <exception cref="InvalidDataException">The path escapes <paramref name="root"/>.</exception>
    public static InstalledFileResult WriteBytes(string root, string relativePath, ReadOnlySpan<byte> bytes)
    {
        var target = SafePath.Below(root, relativePath);
        var hash = Convert.ToHexStringLower(SHA256.HashData(bytes));
        if (Matches(target, bytes.Length, hash)) return new(target, bytes.Length, hash, false);
        AtomicFile.WriteBytes(target, bytes);
        return new(target, bytes.Length, hash, true);
    }

    /// <summary>
    /// Copies <paramref name="source"/> to <paramref name="relativePath"/> under <paramref name="root"/>,
    /// unless a file with the same size and hash is already there.
    /// </summary>
    /// <exception cref="InvalidDataException">The path escapes <paramref name="root"/>.</exception>
    public static InstalledFileResult CopyFile(string root, string relativePath, string source)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(source);
        var target = SafePath.Below(root, relativePath);
        var info = new FileInfo(source);
        var hash = FileFingerprint.Sha256(source);
        if (Matches(target, info.Length, hash)) return new(target, info.Length, hash, false);
        AtomicFile.Copy(source, target);
        return new(target, info.Length, hash, true);
    }

    private static bool Matches(string path, long bytes, string hash) => File.Exists(path)
        && new FileInfo(path).Length == bytes
        && FileFingerprint.Sha256(path).Equals(hash, StringComparison.OrdinalIgnoreCase);
}

/// <summary>Removes only files declared by an installed-content manifest.</summary>
public static class InstalledContentUninstaller
{
    /// <summary>
    /// Deletes every file the manifest lists, then the manifest file, then any directories under
    /// <paramref name="root"/> left empty. Files the manifest does not list are kept.
    /// </summary>
    /// <param name="root">The content root.</param>
    /// <param name="manifest">The manifest whose files to remove.</param>
    /// <param name="manifestFileName">The manifest's own file name under <paramref name="root"/>.</param>
    /// <returns>How many listed files existed and were deleted.</returns>
    /// <exception cref="InvalidDataException">A listed path escapes <paramref name="root"/> or a record is null.</exception>
    public static int Remove(string root, InstalledAssetManifest manifest, string manifestFileName = "manifest.json")
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        ArgumentNullException.ThrowIfNull(manifest);
        var fullRoot = Path.GetFullPath(root);
        var targets = (manifest.Files ?? [])
            .Select(asset => asset?.Path ?? throw new InvalidDataException("Manifest contains a null asset record."))
            .Select(relative => SafePath.Below(fullRoot, relative))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        var removed = 0;
        foreach (var target in targets)
        {
            if (!File.Exists(target)) continue;
            File.Delete(target);
            removed++;
        }

        var manifestPath = SafePath.Below(fullRoot, manifestFileName);
        if (File.Exists(manifestPath)) File.Delete(manifestPath);
        if (Directory.Exists(fullRoot))
        {
            foreach (var directory in Directory.EnumerateDirectories(fullRoot, "*", SearchOption.AllDirectories)
                         .OrderByDescending(path => path.Length))
                if (!Directory.EnumerateFileSystemEntries(directory).Any()) Directory.Delete(directory);
        }
        return removed;
    }
}
