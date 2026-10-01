using System.Security.Cryptography;

namespace ScientificMethod.Core.Assets;

/// <summary>SHA-256 fingerprints of files, as lowercase hex.</summary>
public static class FileFingerprint
{
    /// <summary>Hashes the file at <paramref name="path"/> and returns 64 lowercase hex digits.</summary>
    public static string Sha256(string path)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        using var stream = new FileStream(
            path, FileMode.Open, FileAccess.Read, FileShare.Read,
            bufferSize: 128 * 1024, FileOptions.SequentialScan);
        return Convert.ToHexStringLower(SHA256.HashData(stream));
    }

    /// <summary>Hashes the file at <paramref name="path"/> asynchronously and returns 64 lowercase hex digits.</summary>
    /// <param name="path">The file to hash.</param>
    /// <param name="cancellationToken">Cancels the read.</param>
    public static async Task<string> Sha256Async(
        string path,
        CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        await using var stream = new FileStream(
            path, FileMode.Open, FileAccess.Read, FileShare.Read,
            bufferSize: 128 * 1024, FileOptions.Asynchronous | FileOptions.SequentialScan);
        var hash = await SHA256.HashDataAsync(stream, cancellationToken).ConfigureAwait(false);
        return Convert.ToHexString(hash).ToLowerInvariant();
    }

    /// <summary>Whether <paramref name="value"/> is 64 hex digits, in either case.</summary>
    public static bool IsSha256(string? value) =>
        value is { Length: 64 } && value.All(Uri.IsHexDigit);
}
